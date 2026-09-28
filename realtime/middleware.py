"""
Channels has no equivalent of REST_FRAMEWORK's JWTAuthentication for the
WebSocket handshake, so this is that: reads the same access token the
REST API already uses, resolves the user the same way SimpleJWT does.

The token travels as ?token=... in the URL, not an Authorization header
-- a browser's native WebSocket API cannot attach custom headers to the
handshake request, so a query-string token is the standard workaround
(same approach most JWT+WebSocket integrations use). It's sent over
wss:// in production, same as any other credential in a URL over TLS.
"""
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from core.utils import get_shops_for_user


@database_sync_to_async
def _resolve_user_and_shops(token_str, branch_id):
    try:
        token = AccessToken(token_str)
        user = get_user_model().objects.get(id=token['user_id'])
    except (TokenError, KeyError, get_user_model().DoesNotExist):
        return None, []
    try:
        # THE tenant-isolation boundary for WebSockets: which broadcast
        # group(s) (see realtime.events.shop_group_name) this connection
        # joins is resolved from the authenticated user, exactly like
        # every cross-branch REST view does via get_shops_for_user.
        #
        # A branch-scoped worker (seller, branch manager, non-CEO owner)
        # always gets just their one shop. A CEO gets EVERY branch in
        # their org by default -- real-time cross-branch monitoring is
        # the entire point of the CEO app (see the architecture note
        # about "CEO Android app" below), or exactly one branch if
        # ?branch=<id> narrows it, the same idea as the HTTP API's
        # X-Branch-ID header.
        #
        # Getting this wrong doesn't just mis-scope one HTTP response --
        # it means shop A's sales/product changes would stream live into
        # shop B's browser for as long as the connection stays open. A
        # previous version of this resolved a single shop via
        # get_shop_for_user(), which actively raises for a CEO with more
        # than one branch -- meaning a multi-branch CEO's WebSocket
        # connection was silently refused outright (consumers.py closes
        # with code 4001 when scope['shops'] is empty). That's the
        # architecture's own "CEO Android app" requirement failing
        # before a single event could ever reach it.
        shops = list(get_shops_for_user(user, branch_id=branch_id))
    except PermissionDenied:
        return user, []
    return user, shops


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query_string = scope.get('query_string', b'').decode()
        params = parse_qs(query_string)
        token = params.get('token', [None])[0]
        branch_id = params.get('branch', [None])[0]
        if token:
            scope['user'], scope['shops'] = await _resolve_user_and_shops(token, branch_id)
        else:
            scope['user'], scope['shops'] = None, []
        return await super().__call__(scope, receive, send)
