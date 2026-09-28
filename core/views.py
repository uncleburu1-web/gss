from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from datetime import timedelta

from inventory.models import InventoryItem, StockBatch
from repairs.models import RepairTicket, RepairPayment
from sales.models import Sale
from core.models import Shop, Organization, EmailOTP
from core.permissions import is_ceo, IsCeo
from core.capabilities import capabilities_for_user, effective_role_permissions, CAPABILITIES, CONFIGURABLE_ROLES
from core.utils import get_shop_for_user, get_shops_for_user, get_organization_for_user
from core.auth_serializers import DeviceAwareTokenObtainPairSerializer
from core.email_utils import send_otp_email
from devices.models import Device, ONLINE_THRESHOLD
from staff.models import Worker
from subscriptions.models import get_or_create_subscription


class DeviceAwareLoginView(TokenObtainPairView):
    """Drop-in replacement for SimpleJWT's stock TokenObtainPairView at
    `/api/auth/login/` — identical response shape and identical behavior
    for the web/Android clients that never send device_id, plus the
    device-pairing enforcement in DeviceAwareTokenObtainPairSerializer for
    the desktop client that does. See that serializer's docstring for the
    actual rule.
    """
    serializer_class = DeviceAwareTokenObtainPairSerializer


class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok'})


class RegisterView(APIView):
    """POST /api/auth/register/ -- the entire onboarding flow for a new
    customer: creates their Organization, its first Branch, the owner's
    login account, links them via a Worker(role='owner') on that branch,
    and starts the organization's free trial month -- all atomically, so a
    failure partway through never leaves an orphaned half-created org.

    The registering user becomes BOTH Organization.owner (org-wide CEO
    authority -- create more branches, enterprise billing) AND a branch-
    level Worker(role='owner') on their first branch (so is_owner() checks
    keep working exactly as before multi-branch existed). A solo shop
    owner never has to think about the organization/branch distinction at
    all -- they just have one branch, automatically.

    Deliberately does NOT set is_staff/is_superuser -- those are reserved
    for platform-admin accounts (me, for support), never an ordinary
    customer, however many branches they run.

    The account is created INACTIVE (`is_active=False`) and does NOT get
    tokens back here -- see VerifyOTPView. Everything (org/branch/worker/
    trial subscription) is still created up front, in the same atomic
    block as before, so the moment the code is confirmed there's nothing
    left to set up; only the User row's is_active flips. This does NOT
    apply to Worker/branch-manager logins created later via
    staff.serializers.WorkerSerializer or
    branches.serializers.BranchSerializer -- those are stood up by an
    already-verified owner and stay active immediately, same as always.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        shop_name = (request.data.get('shop_name') or '').strip()
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password') or ''
        email = (request.data.get('email') or '').strip()
        full_name = (request.data.get('full_name') or '').strip() or username
        business_type = (request.data.get('business_type') or '').strip() or 'general'

        if not shop_name or not username or not password or not email:
            return Response({'detail': 'shop_name, username, email, and password are required.'}, status=400)
        if len(password) < 8:
            return Response({'detail': 'Password must be at least 8 characters.'}, status=400)
        valid_types = dict(Organization.BUSINESS_TYPE_CHOICES)
        if business_type not in valid_types:
            return Response({'detail': f'business_type must be one of: {", ".join(valid_types)}.'}, status=400)

        User = get_user_model()

        # Email is checked FIRST and separately from username: two
        # different signups could previously share one email with no
        # error at all — a duplicate email just sailed through to a
        # brand-new account and landed on the "enter code" screen exactly
        # like a fresh signup, silently creating a second organization
        # tied to the same address. Django's default User model has no
        # DB-level uniqueness on email (only username), so this has to be
        # enforced here.
        existing_email_user = User.objects.filter(email__iexact=email).first()
        if existing_email_user is not None:
            if existing_email_user.is_active:
                return Response({
                    'detail': 'That email is already registered. Try logging in instead, '
                              'or use "Forgot password" if you don\u2019t remember your details.',
                }, status=400)
            # A previous signup attempt with this exact email exists but was
            # never verified (code expired, tab closed, etc.) — same
            # reasoning as the username case below: resend a fresh code for
            # THAT pending account rather than creating a second one.
            otp = EmailOTP.issue(existing_email_user)
            send_otp_email(existing_email_user, otp.code)
            return Response({
                'detail': 'That email already has a signup pending verification. '
                          "We've sent a new code to it — enter it to activate the account.",
                'email': existing_email_user.email,
            }, status=200)

        existing = User.objects.filter(username=username).first()
        if existing is not None:
            if existing.is_active:
                return Response({'detail': 'That username is already taken.'}, status=400)
            # A previous signup attempt with this exact username exists but
            # was never verified (code expired, tab closed, etc.) — rather
            # than permanently locking someone out of their own chosen
            # username with no way back in, just issue a fresh code for
            # that same pending account instead of erroring.
            otp = EmailOTP.issue(existing)
            send_otp_email(existing, otp.code)
            return Response({
                'detail': 'That username already has a signup pending verification. '
                          "We've sent a new code to its email — enter it to activate the account.",
                'email': existing.email,
            }, status=200)

        with transaction.atomic():
            user = User.objects.create_user(username=username, password=password, email=email, is_active=False)
            org = Organization.objects.create(name=shop_name, owner=user, business_type=business_type)
            shop = Shop.objects.create(organization=org, name=shop_name, email=email)
            Worker.objects.create(shop=shop, user=user, full_name=full_name, role='owner')
            get_or_create_subscription(org)  # starts the free trial month immediately, no payment required yet

        otp = EmailOTP.issue(user)
        send_otp_email(user, otp.code)

        return Response({
            'detail': "Account created. We've sent a 6-digit code to your email — enter it to activate your account.",
            'email': email,
        }, status=201)


class VerifyOTPView(APIView):
    """POST /api/auth/verify-otp/ {email, code} -- the last step of the
    signup flow started by RegisterView. Success flips the pending
    account's `is_active` to True and returns the same {access, refresh}
    shape RegisterView used to return directly, so the frontend can log
    the person straight in the moment they're verified.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        code = (request.data.get('code') or '').strip()
        if not email or not code:
            return Response({'detail': 'email and code are required.'}, status=400)

        User = get_user_model()
        user = User.objects.filter(email__iexact=email, is_active=False).order_by('-date_joined').first()
        if user is None:
            # Either no such signup, or it's already verified -- send them
            # to log in rather than claiming the code is wrong.
            return Response({'detail': 'No pending signup found for that email. Try logging in instead.'}, status=400)

        otp = EmailOTP.objects.filter(user=user, is_used=False).order_by('-created_at').first()
        if otp is None:
            return Response({'detail': 'No active code found for that email. Request a new one.'}, status=400)
        if otp.is_expired:
            return Response({'detail': 'That code has expired. Request a new one.'}, status=400)
        if otp.attempts >= EmailOTP.MAX_ATTEMPTS:
            return Response({'detail': 'Too many incorrect attempts. Request a new code.'}, status=400)
        if otp.code != code:
            otp.attempts += 1
            otp.save(update_fields=['attempts'])
            return Response({'detail': 'Incorrect code.'}, status=400)

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        user.is_active = True
        user.save(update_fields=['is_active'])

        refresh = RefreshToken.for_user(user)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh)}, status=200)


class ResendOTPView(APIView):
    """POST /api/auth/resend-otp/ {email} -- a fresh code for a signup
    that's still pending verification. Issues a new EmailOTP row (the
    prior one is simply out-ranked by "newest code wins" -- see
    EmailOTP.issue's docstring) rather than mutating the old one, so any
    in-flight verification attempt against the old code still fails
    cleanly instead of racing.
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get('email') or '').strip()
        if not email:
            return Response({'detail': 'email is required.'}, status=400)

        generic_ok = Response({'detail': 'If that email has a pending signup, a new code has been sent.'})

        User = get_user_model()
        user = User.objects.filter(email__iexact=email, is_active=False).order_by('-date_joined').first()
        if user is None:
            # Don't reveal whether that email is registered/already verified.
            return generic_ok

        last = EmailOTP.objects.filter(user=user).order_by('-created_at').first()
        if last is not None:
            wait_left = EmailOTP.RESEND_COOLDOWN_SECONDS - (timezone.now() - last.created_at).total_seconds()
            if wait_left > 0:
                return Response({'detail': f'Please wait {int(wait_left)}s before requesting another code.'}, status=429)

        otp = EmailOTP.issue(user)
        send_otp_email(user, otp.code)
        return generic_ok


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        worker = getattr(user, 'worker', None)
        # A branch manager gets exactly the same "full access within my
        # own branch" authority an owner has -- see
        # core.permissions.is_owner's docstring, which is the actual
        # source of truth every API permission check already uses. This
        # flag has to agree with it: it's what the frontend's isOwner
        # comes from (RequireOwner, the Reports/Liabilities/Workers/
        # Settings nav items, the Inventory "Add item" button, ...), and
        # a mismatch here would mean a branch manager who the backend
        # already lets do all of that has no way to reach any of it.
        is_owner_flag = user.is_staff or user.is_superuser or (worker and worker.role in ('owner', 'branch_manager'))
        shop = worker.shop if worker else None
        org = shop.organization if (shop and shop.organization) else get_organization_for_user(user)
        return Response({
            'id': user.id,
            'username': user.username,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'is_owner': bool(is_owner_flag),
            'is_ceo': is_ceo(user),
            # The worker's actual role string (owner/branch_manager/seller/
            # reception/technician/attendant/other) -- NOT collapsed to
            # 'owner' for a branch manager just because is_owner_flag is
            # also true for them. Only a platform admin account with no
            # Worker record at all (is_staff/is_superuser, no `worker`)
            # falls back to the 'owner' label.
            'role': worker.role if worker else 'owner',
            # Per-capability booleans (delete_sale, mark_attendance, ...) —
            # see core.capabilities. Always all-True for owner/branch_manager/
            # CEO; for everyone else, whatever the Control Center has set for
            # their role, or that capability's built-in default. The frontend
            # keys every capability-gated button/nav-item off this instead of
            # re-deriving role logic client-side.
            'capabilities': capabilities_for_user(user),
            'full_name': worker.full_name if worker else user.username,
            'shop_id': str(shop.id) if shop else None,
            'shop_name': (shop.name if shop else (org.name if org else None)) or 'My Shop',
            'business_type': org.business_type if org else 'general',
            'business_type_label': dict(Organization.BUSINESS_TYPE_CHOICES).get(org.business_type if org else 'general'),
            # Single source of truth for the category picker on every
            # client (web/mobile Inventory, desktop Products) — replaces
            # each of them hard-coding their own gadgets/pharmacy category
            # lists, which is how a clothing or general shop used to end
            # up being offered "Laptop", and a gadgets shop "Medicine".
            'available_categories': InventoryItem.category_choices_for(org.business_type if org else 'general'),
            'service_enabled': org.service_enabled if org else False,
            'pharmacy_enabled': org.pharmacy_enabled if org else False,
            'shop_address': shop.address if shop else '',
            'shop_phone': shop.phone if shop else '',
            'shop_email': shop.email if shop else '',
            'shop_logo_url': shop.logo_url if shop else '',
            'shop_receipt_footer_note': shop.receipt_footer_note if shop else '',
        })


class ControlCenterView(APIView):
    """GET/PUT /api/control-center/ -- the Control Center screen in
    Settings: which of CONFIGURABLE_ROLES (seller, technician, attendant,
    reception, other) may use which CAPABILITIES (delete a sale, mark
    attendance, ...) at every branch in this CEO's organization.

    CEO-only (IsCeo), not just owner: this is organization-WIDE policy
    (see core.models.RolePermission), and a branch manager's authority is
    deliberately scoped to their own branch's day-to-day data, not to
    changing what an entire role can do everywhere. A solo shop owner is
    always their own org's CEO too (see core.utils.get_organization_for_user
    / RegisterView), so this is never out of reach for the common
    one-branch case -- it only ever excludes a *branch manager* who isn't
    also the org owner.
    """
    permission_classes = [IsCeo]

    def get(self, request):
        org = get_organization_for_user(request.user)
        return Response({
            'roles': CONFIGURABLE_ROLES,
            'capabilities': effective_role_permissions(org),
        })

    def put(self, request):
        from .models import RolePermission
        org = get_organization_for_user(request.user)
        changes = request.data.get('changes') or []
        for change in changes:
            role = change.get('role')
            capability = change.get('capability')
            allowed = change.get('allowed')
            if role not in CONFIGURABLE_ROLES or capability not in CAPABILITIES or not isinstance(allowed, bool):
                return Response(
                    {'detail': f'Invalid change: {change!r}. Need role, capability, and a boolean allowed.'},
                    status=400,
                )
            RolePermission.objects.update_or_create(
                organization=org, role=role, capability=capability, defaults={'allowed': allowed},
            )
        return Response({
            'roles': CONFIGURABLE_ROLES,
            'capabilities': effective_role_permissions(org),
        })


class ChangePasswordView(APIView):
    """POST /api/auth/change-password/ -- self-service password reset for
    whoever is currently logged in: owner, CEO, seller, or branch manager
    alike. Requires the current password rather than anything email/SMS
    based, because this backend has no email service configured (see
    settings.py) -- there's no inbox to send a reset link to. If someone
    has genuinely forgotten their password and can't get in, an
    owner/CEO resets it for them instead, from Settings -> Branches (a
    branch manager's login) or from Workers (anyone else's) -- see
    BranchSerializer._sync_manager_login and WorkerSerializer.update.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        current_password = request.data.get('current_password') or ''
        new_password = request.data.get('new_password') or ''

        if not current_password or not new_password:
            return Response({'detail': 'current_password and new_password are required.'}, status=400)
        if not request.user.check_password(current_password):
            return Response({'detail': 'Current password is incorrect.'}, status=400)
        if len(new_password) < 8:
            return Response({'detail': 'New password must be at least 8 characters.'}, status=400)
        if request.user.check_password(new_password):
            return Response({'detail': 'New password must be different from your current password.'}, status=400)

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])
        return Response({'detail': 'Password updated.'})


class DashboardStatsView(APIView):
    """Aggregated numbers for the dashboard screen -- this shop's numbers
    only. Every query below is scoped to `shop`; before multi-tenancy,
    these were global across every shop in the database (a real
    cross-tenant leak now that a second shop can exist).

    Uses get_shops_for_user (not get_shop_for_user) precisely because a CEO
    with more than one branch is a normal, everyday case here, not an error
    to reject: with no branch selected they get every branch's numbers
    combined ("All branches"), and with X-Branch-ID set they get just that
    one -- either way this always returns 200. get_shop_for_user would
    instead raise PermissionDenied the moment a CEO's second branch exists
    and no header is sent, which is exactly what was crashing the
    dashboard the moment "All branches" was selected.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shops = get_shops_for_user(request.user, branch_id=request.headers.get('X-Branch-ID') or None)

        items = InventoryItem.objects.filter(shop__in=shops, is_deleted=False)
        stock_value = sum(i.quantity * i.cost_price for i in items)
        low_stock = [i for i in items if i.is_low_stock]

        active_service_tickets = RepairTicket.objects.filter(shop__in=shops, is_deleted=False).exclude(status='collected').count()

        today = timezone.localdate()

        # Batches expiring within 30 days (or already expired), still with
        # stock on hand. Meaningful for any business type that keeps
        # expiry dates on stock, but this is the figure a pharmacy owner
        # actually needs to see at a glance on the dashboard, the same way
        # a gadget shop needs "active repairs" front and center — see
        # ExpiringInventoryView in reports/views.py for the detailed list
        # this count summarizes.
        expiring_soon_count = StockBatch.objects.filter(
            shop__in=shops, quantity_remaining__gt=0, expiry_date__isnull=False,
            expiry_date__lte=today + timedelta(days=30), is_deleted=False,
        ).count()
        todays_sales = Sale.objects.filter(shop__in=shops, date__date=today, is_deleted=False)
        today_revenue = sum(s.total for s in todays_sales)
        today_profit = sum(s.profit for s in todays_sales)

        todays_repair_payments = RepairPayment.objects.filter(shop__in=shops, date__date=today)
        service_revenue_today = sum(p.amount for p in todays_repair_payments)
        total_collected_today = today_revenue + service_revenue_today

        return Response({
            'stock_value': stock_value,
            'low_stock_count': len(low_stock),
            'active_service_tickets': active_service_tickets,
            'expiring_soon_count': expiring_soon_count,
            'today_revenue': today_revenue,
            'today_profit': today_profit,
            'service_revenue_today': service_revenue_today,
            'total_collected_today': total_collected_today,
        })


class RecentActivityView(APIView):
    """A merged, timestamp-sorted feed of the latest restocks, tickets, and
    sales -- for this shop only (see DashboardStatsView's note on why that
    scoping matters now). Same get_shops_for_user reasoning as
    DashboardStatsView above: a multi-branch CEO viewing "All branches"
    gets a merged feed across all of them, not a 403."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shops = get_shops_for_user(request.user, branch_id=request.headers.get('X-Branch-ID') or None)
        events = []

        for i in InventoryItem.objects.filter(shop__in=shops, is_deleted=False).order_by('-updated_at')[:10]:
            events.append({
                'type': 'inventory',
                'text': f'{i.name} updated — {i.quantity} in stock',
                'timestamp': i.updated_at,
            })

        for r in RepairTicket.objects.filter(shop__in=shops, is_deleted=False).order_by('-date_in')[:10]:
            events.append({
                'type': 'repair',
                'text': f'New ticket {r.ticket_no} — {r.device}',
                'timestamp': r.date_in,
            })
            if r.date_out:
                events.append({
                    'type': 'repair',
                    'text': f'{r.ticket_no} collected by {r.customer_name}',
                    'timestamp': r.date_out,
                })

        for p in RepairPayment.objects.filter(shop__in=shops).select_related('ticket').order_by('-date')[:10]:
            events.append({
                'type': 'service',
                'text': f'Payment received on {p.ticket.ticket_no} — {p.amount}',
                'timestamp': p.date,
            })

        for s in Sale.objects.filter(shop__in=shops, is_deleted=False).prefetch_related('items').order_by('-date')[:10]:
            first_item = s.items.first()
            label = first_item.item_name if first_item else 'items'
            extra = s.items.count() - 1
            if extra > 0:
                label += f' +{extra} more'
            events.append({
                'type': 'sale',
                'text': f'Sold {label} to {s.customer_name or "walk-in"} — {s.total}',
                'timestamp': s.date,
            })

        events.sort(key=lambda e: e['timestamp'], reverse=True)
        return Response(events[:10])


class CeoShopStatusView(APIView):
    """The single check the Android CEO app calls right after login (and
    periodically while open) to decide what to show -- see architecture
    doc Sec8. Distinguishes the two states Django can actually observe:

    - `subscription_expired`: the subscription itself has lapsed. Takes
      priority over desktop connectivity -- an expired subscription cuts
      off cloud/remote monitoring regardless of whether the desktop
      happens to be online at this exact moment.
    - `desktop_offline`: subscription is fine, but the desktop hasn't sent
      a heartbeat recently enough to be considered connected.
    - `ok`: both fine -- the CEO app can show live data with confidence.

    A THIRD state from the architecture doc -- "cloud/database unavailable"
    -- deliberately has no representation here: if the cloud backend or its
    database were actually down, this view would never run to produce a
    response at all. That state is the Android app's own responsibility to
    detect, by catching a request timeout / connection failure / 5xx on
    this very call and showing "Shop temporarily unavailable" itself,
    rather than expecting a 200 response to ever say so.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        shop = get_shop_for_user(request.user, branch_id=request.headers.get('X-Branch-ID') or None)
        sub = get_or_create_subscription(shop.organization)
        effective = sub.effective_status

        desktop = Device.objects.filter(
            shop=shop, device_type='desktop', is_deleted=False
        ).order_by('-last_seen_at').first()
        now = timezone.now()
        desktop_connected = bool(
            desktop and desktop.last_seen_at and now - desktop.last_seen_at <= ONLINE_THRESHOLD
        )

        if effective == 'expired':
            state = 'subscription_expired'
            message = 'Subscription expired. Please renew your subscription to restore cloud synchronization.'
        elif not desktop_connected:
            state = 'desktop_offline'
            message = "Unable to connect to desktop. Please check the shop's internet connection."
        else:
            state = 'ok'
            message = None

        return Response({
            'state': state,
            'message': message,
            'subscription_status': effective,
            'subscription_in_grace': effective == 'grace',
            'desktop_connected': desktop_connected,
            'desktop_last_seen': desktop.last_seen_at if desktop else None,
            'desktop_name': desktop.name if desktop else None,
        })
