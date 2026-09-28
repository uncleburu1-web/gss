"""Sending the signup verification email via Resend
(https://resend.com/docs/api-reference/emails/send-email) — a plain HTTP
call, no SDK needed, using `requests` (already a dependency for Paystack
verification -- see subscriptions/views.py).
"""
import logging

import requests
from django.conf import settings

from core.models import EmailOTP

logger = logging.getLogger(__name__)

RESEND_URL = 'https://api.resend.com/emails'


def send_otp_email(user, code):
    """Email `code` to `user.email`. Returns True if Resend accepted the
    request, False if delivery was skipped or failed -- callers should NOT
    fail the request that triggered this (registration, resend) just
    because the email didn't go out; the code is always logged too, so it
    can still be read off the server logs while RESEND_API_KEY isn't set
    (e.g. local dev) or if Resend itself is briefly down.
    """
    logger.info('OTP for %s: %s (expires in %s min)', user.email, code, EmailOTP.LIFETIME_MINUTES)

    if not settings.RESEND_API_KEY:
        logger.warning('RESEND_API_KEY is not set -- OTP email to %s was not sent, only logged above.', user.email)
        return False

    name = user.first_name or user.username
    text_body = (
        f'Hi {name},\n\n'
        f'Your verification code is: {code}\n\n'
        f'This code expires in 10 minutes. Enter it on the verification page to activate your account.\n\n'
        f"If you didn't request this, you can safely ignore this email."
    )
    html_body = f"""\
<div style="font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 480px; margin: 0 auto; color: #1a1a1a;">
  <h2 style="margin-bottom: 4px;">Confirm your email</h2>
  <p style="color: #555;">Hi {name}, use this code to activate your account:</p>
  <p style="font-size: 34px; font-weight: 700; letter-spacing: 8px; margin: 24px 0;">{code}</p>
  <p style="color: #555;">This code expires in <strong>10 minutes</strong>.</p>
  <p style="color: #999; font-size: 13px; margin-top: 32px;">
    If you didn't request this, you can safely ignore this email.
  </p>
</div>
"""

    try:
        response = requests.post(
            RESEND_URL,
            headers={
                'Authorization': f'Bearer {settings.RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'from': settings.EMAIL_FROM,
                'to': [user.email],
                'subject': 'Your verification code',
                'html': html_body,
                'text': text_body,
            },
            timeout=10,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception('Resend delivery failed for %s -- see logged OTP above as a fallback.', user.email)
        return False
