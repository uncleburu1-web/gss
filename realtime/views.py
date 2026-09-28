"""
GET /api/realtime/status/ -- returns the current event sequence for
every shop the caller can see: {"<shop_id>": <sequence>, ...}. This is
the exact same "hello" baseline the WebSocket consumer sends on every
connect (see consumers.py), exposed over plain HTTP for the moments a
client needs it without a live socket -- right after login before the
WebSocket has connected yet, or a periodic sanity check a client can
run entirely on its own schedule regardless of connection state.

A client compares this against whatever it last saw for each shop; any
shop whose sequence here is higher than what it remembers means
"something happened while I wasn't watching." The client's response to
that is just to refetch (bump its resource "version" counters) -- the
exact same thing a live event would have told it to do. There's no
event replay to get right and no partial-payload reducer to maintain --
this only ever tells a client "you're stale," never what specifically
changed; the existing REST endpoints remain the only source of truth
for that.
"""
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.utils import get_shops_for_user


class RealtimeStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        shops = get_shops_for_user(request.user)
        return Response({str(shop.id): shop.last_event_sequence for shop in shops})
