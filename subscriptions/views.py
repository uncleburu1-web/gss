import hashlib
import hmac
import uuid

from django.conf import settings
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from core.models import Organization
from core.permissions import is_owner, is_ceo
from core.utils import get_shop_for_user, get_organization_for_user
from .models import get_or_create_subscription, record_successful_payment, PRICING_NGN
from .paystack import initialize_transaction, verify_transaction, PaystackError
from .serializers import SubscriptionStatusSerializer


def _organization_for_request_user(user):
    """Every billing endpoint ultimately needs "this user's organization" —
    resolved the same way regardless of whether they're the CEO (owns it
    directly) or a branch-level owner/manager (their branch's org)."""
    org = get_organization_for_user(user)
    if org is not None:
        return org
    shop = get_shop_for_user(user)
    return shop.organization


class SubscriptionStatusView(APIView):
    """Both clients check this: the desktop to decide whether to attempt
    sync at all, the Android app as one input into the combined
    shop-status check (see core.views.CeoShopStatusView). One subscription
    per ORGANIZATION — every branch under it shares this same status."""

    def get(self, request):
        org = _organization_for_request_user(request.user)
        sub = get_or_create_subscription(org)
        data = SubscriptionStatusSerializer(sub).data
        data['pricing'] = PRICING_NGN
        data['is_ceo'] = is_ceo(request.user)
        data['organization'] = {'id': str(org.id), 'name': org.name}
        return Response(data)


class SubscriptionCheckoutView(APIView):
    """POST /api/subscription/checkout/ {billing_cycle: 'monthly'|'yearly',
    enterprise: bool} -- only the owner/CEO can trigger a charge for the
    organization. Starts a Paystack transaction for whatever
    Subscription.total_price_ngn currently computes (base + any additional
    branches, or the flat enterprise rate) and hands back the hosted
    checkout URL for the frontend to redirect the browser to."""

    def post(self, request):
        if not is_owner(request.user):
            return Response({'detail': 'Only the owner can manage billing.'}, status=403)

        org = _organization_for_request_user(request.user)
        sub = get_or_create_subscription(org)
        enterprise = bool(request.data.get('enterprise'))
        cycle = request.data.get('billing_cycle', sub.billing_cycle)
        if cycle not in ('monthly', 'yearly'):
            return Response({'detail': 'billing_cycle must be "monthly" or "yearly".'}, status=400)

        if enterprise:
            amount_kobo = PRICING_NGN['enterprise'] * 100
        else:
            tier = PRICING_NGN[cycle]
            amount_kobo = (tier['base'] + sub.additional_branches * tier['additional_branch']) * 100

        reference = f'sub_{org.id.hex[:8]}_{uuid.uuid4().hex[:12]}'
        callback_url = request.data.get('callback_url') or request.build_absolute_uri('/')

        try:
            data = initialize_transaction(
                email=request.user.email or f'{request.user.username}@example.com',
                amount_kobo=amount_kobo,
                reference=reference,
                callback_url=callback_url,
                # The ONE source of truth both the webhook and the verify
                # path resolve "which org this paid for" from.
                metadata={'organization_id': str(org.id), 'billing_cycle': cycle, 'enterprise': enterprise},
            )
        except PaystackError as exc:
            return Response({'detail': str(exc)}, status=502)

        return Response({'authorization_url': data['authorization_url'], 'reference': data['reference']})


class SubscriptionVerifyView(APIView):
    """GET /api/subscription/verify/?reference=xxx -- called by the page
    Paystack redirects back to after checkout, so the owner/CEO sees
    confirmation immediately rather than waiting on the webhook (still the
    authoritative path -- this is a fast-feedback duplicate of it, safe
    because record_successful_payment is idempotent either way)."""

    def get(self, request):
        reference = request.query_params.get('reference', '')
        if not reference:
            return Response({'detail': 'reference query param is required.'}, status=400)

        try:
            data = verify_transaction(reference)
        except PaystackError as exc:
            return Response({'detail': str(exc)}, status=502)

        if data.get('status') != 'success':
            return Response({'detail': 'Payment was not successful.', 'paystack_status': data.get('status')}, status=402)

        meta = data.get('metadata') or {}
        org = Organization.objects.filter(id=meta.get('organization_id')).first()
        if org is None:
            # Shouldn't happen -- checkout always sets metadata.organization_id
            # -- but never silently credit the wrong org if it somehow does.
            return Response({'detail': 'Could not determine which organization this payment belongs to.'}, status=409)

        _payment, applied = record_successful_payment(
            org, reference, data['amount'],
            billing_cycle=meta.get('billing_cycle'),
            enterprise=meta.get('enterprise'),
        )
        sub = get_or_create_subscription(org)
        return Response({
            'applied': applied,  # False just means it was already recorded (e.g. webhook got there first) -- still a success
            'subscription': SubscriptionStatusSerializer(sub).data,
        })


@method_decorator(csrf_exempt, name='dispatch')
class PaystackWebhookView(APIView):
    """POST /api/subscription/paystack/webhook/ -- Paystack calls this
    directly, with no user session, so it's AllowAny; security comes from
    verifying the signature below, NOT from Django auth. Never trust an
    unverified payload -- signature verification is not optional here.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.headers.get('x-paystack-signature', '')
        expected = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), request.body, hashlib.sha512
        ).hexdigest()
        if not settings.PAYSTACK_SECRET_KEY or not hmac.compare_digest(signature, expected):
            # Wrong secret configured, or someone POSTing without a valid
            # signature -- reject outright rather than guessing intent.
            return Response(status=401)

        event = request.data.get('event')
        if event == 'charge.success':
            data = request.data.get('data', {})
            reference = data.get('reference')
            amount = data.get('amount')
            meta = data.get('metadata') or {}
            org = Organization.objects.filter(id=meta.get('organization_id')).first()
            if reference and amount and org is not None:
                record_successful_payment(
                    org, reference, amount,
                    billing_cycle=meta.get('billing_cycle'), enterprise=meta.get('enterprise'),
                )
        # Every other event type (failed charge, refund, etc.) is
        # acknowledged but ignored -- a 200 tells Paystack not to retry.
        return Response(status=200)
