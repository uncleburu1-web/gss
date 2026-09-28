"""
Minimal Paystack client. Deliberately just two functions wrapping the two
calls this app needs (initialize + verify a transaction) rather than a
full SDK — easy to read, easy to mock in tests (patch requests.post /
requests.get), and easy to see exactly what leaves the server.

PAYSTACK_SECRET_KEY must never be sent anywhere except as the Bearer
token to api.paystack.co — see settings.py's comment on where it comes
from (environment only, no hardcoded default).
"""
import requests
from django.conf import settings

PAYSTACK_BASE_URL = 'https://api.paystack.co'


class PaystackError(Exception):
    """Paystack reachable but returned an error, or wasn't reachable at
    all — either way, the caller should NOT treat this as a successful
    payment. Never swallow this into a generic 200 response."""


def _headers():
    return {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type': 'application/json',
    }


def initialize_transaction(email, amount_kobo, reference, callback_url, metadata=None):
    """Starts a Paystack transaction, returns the hosted checkout URL the
    frontend redirects the browser to. `amount_kobo` is the smallest NGN
    unit (₦1 = 100 kobo) — Paystack's API is kobo-denominated throughout."""
    try:
        resp = requests.post(
            f'{PAYSTACK_BASE_URL}/transaction/initialize',
            headers=_headers(),
            json={
                'email': email,
                'amount': amount_kobo,
                'reference': reference,
                'callback_url': callback_url,
                'metadata': metadata or {},
            },
            timeout=15,
        )
        body = resp.json()
    except requests.RequestException as exc:
        raise PaystackError(f'Could not reach Paystack: {exc}') from exc
    except ValueError as exc:
        # Not RequestException — this is what actually fires if something
        # in between (a proxy, a firewall, Paystack itself having a bad
        # day) returns a non-JSON body. Caught this the hard way: it used
        # to crash with a raw 500 instead of a clean PaystackError.
        raise PaystackError(f'Paystack returned a non-JSON response (HTTP {resp.status_code})') from exc
    if not resp.ok or not body.get('status'):
        raise PaystackError(body.get('message', f'Paystack returned {resp.status_code}'))
    return body['data']  # {authorization_url, access_code, reference}


def verify_transaction(reference):
    """Ground truth for "did this payment actually succeed" — used both
    by the webhook handler and by a return-from-checkout page, so a
    missed/delayed webhook is never the only way a payment gets noticed."""
    try:
        resp = requests.get(
            f'{PAYSTACK_BASE_URL}/transaction/verify/{reference}',
            headers=_headers(),
            timeout=15,
        )
        body = resp.json()
    except requests.RequestException as exc:
        raise PaystackError(f'Could not reach Paystack: {exc}') from exc
    except ValueError as exc:
        raise PaystackError(f'Paystack returned a non-JSON response (HTTP {resp.status_code})') from exc
    if not resp.ok or not body.get('status'):
        raise PaystackError(body.get('message', f'Paystack returned {resp.status_code}'))
    return body['data']  # {status: 'success'|'failed'|..., amount, reference, customer: {...}, ...}
