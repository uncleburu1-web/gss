import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Organization(models.Model):
    """The top of the hierarchy: Organization -> Branch (Shop) -> business
    records. One Organization per paying customer/CEO. A solo shop owner
    still gets an Organization (with exactly one Branch under it) --
    there's no separate "simple" data model, just an org that happens to
    have one branch instead of several. See Shop below for why the
    existing `shop` FK name is kept everywhere rather than renamed.
    """
    BUSINESS_TYPE_CHOICES = [
        ('gadgets', 'Gadgets & electronics (phones, laptops, accessories)'),
        ('clothing', 'Clothing & fashion'),
        ('pharmacy', 'Pharmacy & health products'),
        ('general', 'General retail / other'),
    ]
    # Which business types get device-repair tracking (tickets, diagnosis,
    # parts, repair payments). A clothing or general retail shop has no use
    # for a "Repairs" tab at all -- this is the single source of truth the
    # frontend nav and the dashboard's repair stats both key off of, via
    # the repairs_enabled property below, so adding a new repair-capable
    # type later is a one-line change in this set, not a hunt through
    # every screen that currently checks "== 'gadgets'" by hand.
    REPAIR_CAPABLE_TYPES = {'gadgets'}

    # Same idea as REPAIR_CAPABLE_TYPES above, for the pharmacy-specific
    # side of the business (prescription flag on stock items, drug-style
    # categories). One-line change here if another health-adjacent type
    # is ever added.
    PHARMACY_TYPES = {'pharmacy'}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    business_type = models.CharField(
        max_length=20, choices=BUSINESS_TYPE_CHOICES, default='general',
        help_text='Set once at signup. Drives which optional modules (like Repairs) are shown.',
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owned_organizations',
        null=True, blank=True,  # null only for the one legacy default-org row — see core.utils.DEFAULT_SHOP_ID
        help_text='The CEO — organization-wide access to every branch. NOT the same as a '
                   'branch-level Worker(role="owner"), which is scoped to one branch only.',
    )
    # Enterprise billing lives here, not as a per-branch Subscription, since
    # it's ONE flat price covering unlimited branches -- see
    # subscriptions.models for how this is checked alongside per-branch
    # subscriptions.
    enterprise_until = models.DateTimeField(
        null=True, blank=True,
        help_text='If set and in the future, every branch under this org has cloud services '
                   'enabled regardless of its own individual subscription.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def has_enterprise_access(self):
        from django.utils import timezone
        return bool(self.enterprise_until and timezone.now() <= self.enterprise_until)

    @property
    def repairs_enabled(self):
        return self.business_type in self.REPAIR_CAPABLE_TYPES

    # Alias under the client-facing name — every API response, and every
    # frontend/mobile variable, calls this module "Service" now. The
    # property above stays for any internal code that still refers to it
    # by the old name; service_enabled is what MeView actually returns.
    @property
    def service_enabled(self):
        return self.repairs_enabled

    @property
    def pharmacy_enabled(self):
        return self.business_type in self.PHARMACY_TYPES


class Shop(models.Model):
    """A Branch. Kept the name `Shop` (not renamed to `Branch`) on purpose
    -- it's the FK target on every single syncable model in the app
    (InventoryItem, Sale, Customer, ... via SyncModel below), and renaming
    it would mean touching every app's models/migrations for a purely
    cosmetic reason. Conceptually this IS "Branch" now: it belongs to an
    Organization, has a branch_code, a manager, a status, etc. — the
    multi-branch architecture the CEO asked for maps onto this model
    directly, just under its original name.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('suspended', 'Suspended'),
        ('archived', 'Archived'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name='branches',
        null=True, blank=True,  # null only for the one legacy default-shop row — see core.utils.DEFAULT_SHOP_ID
    )
    name = models.CharField(max_length=200)
    branch_code = models.CharField(max_length=30, blank=True, help_text='Unique within the organization, e.g. "WUSE001".')
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    logo_url = models.URLField(
        blank=True, max_length=500,
        help_text='Hosted image URL (e.g. from an image host) — printed at the top of receipts and '
                   'shown in the desktop app once downloaded at login. Not a file upload field: this '
                   'backend has no persistent media storage configured (Railway\'s filesystem is '
                   'ephemeral), so a URL to an already-hosted image is the reliable option here.',
    )
    receipt_footer_note = models.CharField(
        max_length=200, blank=True,
        help_text='Custom closing line printed at the bottom of this branch\'s receipts (e.g. a return '
                   'policy or a thank-you message), replacing the generic default. Set from Settings -> '
                   'Receipt, usually right after signup. Thermal (Android) receipts print this as plain '
                   'text; the web/desktop invoice prints it the same way — neither renders a logo image '
                   'from this field, that\'s what logo_url is for.',
    )
    manager = models.ForeignKey(
        'staff.Worker', on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_branches',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    opening_date = models.DateField(null=True, blank=True)
    timezone = models.CharField(max_length=50, default='Africa/Lagos')
    currency = models.CharField(max_length=10, default='NGN')
    description = models.TextField(blank=True)
    tax_rate_default = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    last_event_sequence = models.BigIntegerField(
        default=0,
        help_text='Monotonic counter for realtime.RealtimeEvent — incremented under a row lock '
                   'each time an event is broadcast for this shop (see realtime.events.broadcast), '
                   'so every event gets a gapless, per-shop sequence number a client can compare '
                   "against its own last-seen value to tell whether it missed anything.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['organization', 'branch_code'],
                condition=~models.Q(branch_code=''),
                name='unique_branch_code_per_organization',
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_operational(self):
        """SUSPENDED/ARCHIVED branches keep their historical records fully
        readable (reports, audits) but block new business activity — see
        sales/views.py and the other create paths that check this."""
        return self.status == 'active'


class RolePermission(models.Model):
    """One Control Center toggle: within `organization`, may workers with
    `role` use `capability`? See core.capabilities for the fixed list of
    capabilities, their built-in defaults, and has_capability() -- the
    function that actually consults this table.

    Scoped to the whole Organization, not a single Shop/branch: role
    definitions (Worker.ROLE_CHOICES) are shared across every branch an
    org runs, so "can a seller delete a sale" is one policy decision for
    the business, not a per-branch one. Only the org's CEO can change it
    (see core.views.ControlCenterView) -- a branch manager's "owner-like"
    authority is deliberately scoped to their own branch's day-to-day
    data, not to org-wide policy like this.

    No row for a given (organization, role, capability) means "still
    using the capability's built-in default" -- a row only gets created
    the first time an owner/CEO actually flips that toggle away from the
    default, via ControlCenterView's PUT.
    """
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name='role_permissions')
    role = models.CharField(max_length=20)
    capability = models.CharField(max_length=40)
    allowed = models.BooleanField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['organization', 'role', 'capability'], name='unique_role_capability_per_org'),
        ]

    def __str__(self):
        return f'{self.organization_id}: {self.role} -> {self.capability} = {self.allowed}'


class EmailOTP(models.Model):
    """A 6-digit code emailed to a newly-registered OWNER to confirm they
    control the address they signed up with, before RegisterView's
    `is_active=False` account is flipped active and allowed to log in
    (see views.RegisterView / VerifyOTPView / ResendOTPView and
    auth_serializers.DeviceAwareTokenObtainPairSerializer for the
    "please verify your email" login rejection).

    Deliberately NOT used for Worker/branch-manager logins created via
    staff.serializers.WorkerSerializer or
    branches.serializers.BranchSerializer._sync_manager_login — those are
    stood up by an already-verified owner/CEO and stay active immediately,
    exactly as before this existed.
    """
    LIFETIME_MINUTES = 10
    MAX_ATTEMPTS = 5
    RESEND_COOLDOWN_SECONDS = 60

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='email_otps',
    )
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'OTP for {self.user.email} ({"used" if self.is_used else "pending"})'

    @classmethod
    def issue(cls, user):
        """Create (and return) a fresh code for `user`. Doesn't invalidate
        any still-pending prior code -- callers that care about "only the
        latest code should work" look up `.order_by('-created_at').first()`
        (the newest one), which this makes correct without needing a
        separate cleanup step."""
        code = f'{secrets.randbelow(1_000_000):06d}'
        return cls.objects.create(
            user=user, code=code, expires_at=timezone.now() + timedelta(minutes=cls.LIFETIME_MINUTES),
        )

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class SyncModel(models.Model):
    """Abstract base for every table that needs to sync between the cloud
    and the offline-first desktop client (architecture doc §2 / §7).

    - id: UUID, not autoincrement. Client-generated on the desktop so a
      record created offline never collides with one created on another
      device or on the cloud before they've ever talked to each other.
    - shop: branch partition (see Shop's docstring for why the field is
      still named `shop`, not `branch`). Every syncable row belongs to
      exactly one branch so per-branch AND organization-wide (aggregated
      across branches) scoping can both work cleanly.
    - updated_at: last-write timestamp. This is both the field the conflict
      resolver compares on for master-data (field-level last-write-wins)
      and the cursor `/sync/pull/?since=<updated_at>` uses for delta pulls —
      every syncable table must expose it under this exact name.
    - is_deleted: soft delete. A deletion has to flow through the sync
      queue like any other operation instead of disappearing before it's
      had a chance to sync, so we never hard-delete syncable rows.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name='%(class)ss')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True
