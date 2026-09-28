from datetime import timedelta
from django.db import models
from django.utils import timezone
from core.models import Organization

# ---------------------------------------------------------------------------
# THE pricing engine — one place, not scattered across views/frontend. See
# Subscription.total_price_ngn for how these combine (base + per-additional-
# branch, or a flat enterprise rate). Changing a price later means editing
# these four numbers, nothing else — every view/serializer/frontend reads
# through Subscription, never a literal number.
# ---------------------------------------------------------------------------
PRICING_NGN = {
    'monthly': {'base': 7_000, 'additional_branch': 5_000},
    'yearly': {'base': 60_000, 'additional_branch': 50_000},
    'enterprise': 200_000,  # per year, unlimited branches, fair use
}
PERIOD_DAYS = {'monthly': 30, 'yearly': 365, 'enterprise': 365}
TRIAL_DAYS = 30  # free trial on signup — one month, not one year

# "Enterprise starts looking better than paying for N branches individually"
# — a recommendation, never an automatic switch (see subscriptions/views.py).
ENTERPRISE_BREAKEVEN_BRANCHES = 4


class Subscription(models.Model):
    """ONE per ORGANIZATION — not per branch, not per staff account (see
    the architecture note: "do not create a separate subscription for
    every staff member"). Controls *cloud* services only — sync, Android
    CEO monitoring, cloud backup — for EVERY branch under the org at
    once; never gates local desktop POS operations at any branch (see
    architecture principle: "Subscription = Controls Cloud Services").

    The first branch is included in the base price; every additional
    branch is a smaller add-on charge — see total_price_ngn. Enterprise
    is a flat rate for unlimited branches instead of base+add-ons.
    """
    STATUS_CHOICES = [
        ('trial', 'Trial'),
        ('active', 'Active'),
        ('grace', 'Grace period'),
        ('expired', 'Expired'),
        ('suspended', 'Suspended'),
        ('cancelled', 'Cancelled'),
    ]
    BILLING_CYCLE_CHOICES = [('monthly', 'Monthly'), ('yearly', 'Yearly')]

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name='subscription')
    is_enterprise = models.BooleanField(default=False)
    billing_cycle = models.CharField(max_length=10, choices=BILLING_CYCLE_CHOICES, default='yearly')
    status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default='trial',
        help_text='Manual floor — set to e.g. "suspended" to force-cut cloud services regardless of dates.',
    )
    current_period_end = models.DateTimeField(
        null=True, blank=True,
        help_text='When the current paid period ends. Blank = no expiry enforced (e.g. a comped org).',
    )
    grace_period_days = models.PositiveIntegerField(
        default=3, help_text='Days after current_period_end before cloud services actually cut off.',
    )
    auto_renew = models.BooleanField(default=True)
    last_payment_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.organization.name} — {self.effective_status}'

    @property
    def active_branch_count(self):
        return self.organization.branches.exclude(status='archived').count()

    @property
    def additional_branches(self):
        return max(0, self.active_branch_count - 1)

    @property
    def total_price_ngn(self):
        if self.is_enterprise:
            return PRICING_NGN['enterprise']
        tier = PRICING_NGN[self.billing_cycle]
        return tier['base'] + self.additional_branches * tier['additional_branch']

    @property
    def enterprise_recommended(self):
        """"Enterprise may be better for your business" — a suggestion the
        owner can act on or ignore, never an automatic switch (spec §5)."""
        return not self.is_enterprise and self.active_branch_count >= ENTERPRISE_BREAKEVEN_BRANCHES

    @property
    def effective_status(self):
        """Real-time status, not just the stored flag — a subscription
        drifts from active -> grace -> expired purely because time passed,
        with nothing needing to run a cron job to flip a field. A manual
        floor (suspended/cancelled) always wins outright."""
        if self.status in ('suspended', 'cancelled'):
            return self.status
        if self.current_period_end is None:
            return self.status
        now = timezone.now()
        if now <= self.current_period_end:
            return 'active' if self.status != 'trial' else 'trial'
        if now <= self.current_period_end + timedelta(days=self.grace_period_days):
            return 'grace'
        return 'expired'

    @property
    def cloud_services_enabled(self):
        return self.effective_status in ('trial', 'active', 'grace')

    def extend(self, cycle=None, enterprise=None):
        """Called after a verified successful payment. Extends from
        whichever is later — the current period end, or right now — so
        renewing early never loses paid time, and renewing late (after
        expiry/grace) starts the new period from today rather than being
        backdated to the old expiry."""
        if cycle:
            self.billing_cycle = cycle
        if enterprise is not None:
            self.is_enterprise = enterprise
        now = timezone.now()
        base = max(self.current_period_end or now, now)
        period_key = 'enterprise' if self.is_enterprise else self.billing_cycle
        self.current_period_end = base + timedelta(days=PERIOD_DAYS[period_key])
        self.status = 'active'
        self.last_payment_at = now
        self.save(update_fields=[
            'current_period_end', 'status', 'billing_cycle', 'is_enterprise', 'last_payment_at', 'updated_at',
        ])


class SubscriptionPayment(models.Model):
    """One row per successfully-verified Paystack transaction. The
    `paystack_reference` uniqueness is the entire idempotency mechanism —
    Paystack retries webhooks, and a payment can also be verified via the
    return-from-checkout path AND the webhook for the same transaction;
    both go through record_successful_payment, guarded by this constraint,
    so a payment can never apply twice."""
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='payments')
    paystack_reference = models.CharField(max_length=100, unique=True)
    amount_kobo = models.PositiveIntegerField()
    paid_at = models.DateTimeField(auto_now_add=True)
    period_end_after = models.DateTimeField(help_text='What current_period_end became once this was applied.')

    class Meta:
        ordering = ['-paid_at']

    def __str__(self):
        return f'{self.organization.name} — ₦{self.amount_kobo / 100:,.0f} — {self.paystack_reference}'


def get_or_create_subscription(organization):
    sub, created = Subscription.objects.get_or_create(
        organization=organization,
        defaults={
            'billing_cycle': 'yearly',
            'status': 'trial',
            # One month free, per the pricing model: no payment required
            # until 30 days after signup. The clock starts at
            # registration, not on first login or first payment.
            'current_period_end': timezone.now() + timedelta(days=TRIAL_DAYS),
        },
    )
    return sub


def record_successful_payment(organization, reference, amount_kobo, billing_cycle=None, enterprise=None):
    """Idempotent: if this reference was already recorded, does nothing
    and returns the existing row instead of extending the subscription
    again. Call this from both the webhook and the verify-on-return path
    — whichever gets there first wins, the other is a safe no-op."""
    existing = SubscriptionPayment.objects.filter(paystack_reference=reference).first()
    if existing:
        return existing, False

    sub = get_or_create_subscription(organization)
    sub.extend(cycle=billing_cycle, enterprise=enterprise)
    payment = SubscriptionPayment.objects.create(
        organization=organization, paystack_reference=reference, amount_kobo=amount_kobo,
        period_end_after=sub.current_period_end,
    )
    # A seller with the app open on a different device (at ANY branch of
    # this org) should see cloud sync come back / the "please subscribe"
    # banner clear the instant the OWNER finishes paying somewhere else —
    # not just whenever they next happen to refresh.
    from realtime.events import broadcast
    for shop in organization.branches.all():
        broadcast(shop, 'subscription.updated', {'effective_status': sub.effective_status})
    return payment, True
