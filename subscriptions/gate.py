"""Cloud services stop the moment a subscription ends (no grace period).

Every /api/ request from a signed-in account whose organization's subscription is no longer
active gets 402 {"code": "subscription_expired"} -- except the few endpoints needed to see the
status, sign in and pay. What that means per client:

  * desktop till: sync answers 402, the till shows "paused" and KEEPS SELLING locally; its queue
    uploads as soon as the subscription is renewed (nothing is lost);
  * web / Android: the app shows a small "subscription ended" card (subscribe, or ask the owner).

Platform staff are never blocked. Requests that are not signed in pass straight through to the
view, which answers 401 as before.
"""
from django.http import JsonResponse
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

ALWAYS_ALLOWED_PREFIXES = (
    '/api/auth/',               # sign in / refresh / register / password
    '/api/me/',                 # who am I (the app needs it to draw the card)
    '/api/health/',
    '/api/subscription/',       # status, checkout, verify, Paystack webhook
)

_jwt = JWTAuthentication()


def _organization_for(user):
    from core.utils import get_organization_for_user
    org = get_organization_for_user(user)
    if org is not None:
        return org
    worker = getattr(user, 'worker', None)
    shop = getattr(worker, 'shop', None)
    return getattr(shop, 'organization', None)


def subscription_blocks(user):
    """The Subscription that stops this user's cloud access right now, or None."""
    if user is None or not user.is_authenticated or user.is_staff or user.is_superuser:
        return None
    org = _organization_for(user)
    if org is None:
        return None
    from .models import get_or_create_subscription
    sub = get_or_create_subscription(org)
    return None if sub.cloud_services_enabled else sub


class SubscriptionGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if request.method == 'OPTIONS' or not path.startswith('/api/') or path.startswith(ALWAYS_ALLOWED_PREFIXES):
            return self.get_response(request)
        try:
            authenticated = _jwt.authenticate(request)
        except (InvalidToken, TokenError):
            authenticated = None  # the view will answer 401 exactly as before
        except Exception:
            authenticated = None
        user = authenticated[0] if authenticated else getattr(request, 'user', None)
        sub = subscription_blocks(user)
        if sub is None:
            return self.get_response(request)
        status = sub.effective_status
        was_trial = sub.status == 'trial'
        return JsonResponse({
            'code': 'subscription_expired',
            'subscription_status': status,
            'trial_ended': was_trial,
            'detail': ('Your free trial has ended. Subscribe to keep using Benchline online.'
                       if was_trial else 'Your subscription has ended. Renew to keep using Benchline online.'),
        }, status=402)
