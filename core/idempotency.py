"""Idempotency-Key support for create endpoints (see core.models.IdempotencyRecord for the why)."""
import hashlib
import json
import re

from django.db import IntegrityError, transaction
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response

from .models import IdempotencyRecord

HEADER = 'Idempotency-Key'
_KEY_RE = re.compile(r'[A-Za-z0-9_\-]{8,64}')


class _Replay(Exception):
    """This (user, key) already has a stored outcome."""


class _NotStored(Exception):
    """The request ran but did not succeed, so nothing is remembered and the claim is rolled back."""

    def __init__(self, response):
        super().__init__()
        self.response = response


def _fingerprint(request):
    body = json.dumps(request.data, sort_keys=True, default=str, separators=(',', ':'))
    return hashlib.sha256(f'{request.method} {request.path}\n{body}'.encode()).hexdigest()


def run_idempotent(request, perform):
    """Runs `perform()` (which returns a DRF Response) at most once per (user, Idempotency-Key).

    No header -> exactly the old behaviour. With a header the claim and the work share ONE database
    transaction: the claim row is inserted first, so two concurrent requests with the same key are
    serialised by the unique index (the second waits, then replays the first's stored response); and if
    the work fails, the claim rolls back with it, so a corrected retry is never blocked by a failure."""
    key = request.headers.get(HEADER)
    if not key:
        return perform()
    if not _KEY_RE.fullmatch(key):
        return Response({'detail': f'{HEADER} must be 8-64 letters, digits, "-" or "_".'}, status=400)

    fingerprint = _fingerprint(request)
    try:
        with transaction.atomic():
            try:
                record = IdempotencyRecord.objects.create(
                    user=request.user, key=key, fingerprint=fingerprint, status_code=0,
                )
            except IntegrityError:
                raise _Replay()
            response = perform()
            if 200 <= response.status_code < 300:
                record.status_code = response.status_code
                record.response_body = json.loads(JSONRenderer().render(response.data) or b'null')
                record.save(update_fields=['status_code', 'response_body'])
                return response
            raise _NotStored(response)
    except _NotStored as stop:
        return stop.response
    except _Replay:
        existing = IdempotencyRecord.objects.get(user=request.user, key=key)
        if existing.fingerprint != fingerprint:
            return Response({'detail': f'This {HEADER} was already used for a different request.'}, status=422)
        replay = Response(existing.response_body, status=existing.status_code)
        replay['Idempotent-Replay'] = 'true'
        return replay
