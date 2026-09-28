from django.urls import path

from .views import RealtimeStatusView

urlpatterns = [
    path('status/', RealtimeStatusView.as_view(), name='realtime-status'),
]
