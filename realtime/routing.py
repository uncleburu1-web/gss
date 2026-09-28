from django.urls import re_path

from .consumers import ShopEventsConsumer

websocket_urlpatterns = [
    re_path(r'^ws/live/$', ShopEventsConsumer.as_asgi()),
]
