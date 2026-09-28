from django.urls import path
from .views import SubscriptionStatusView, SubscriptionCheckoutView, SubscriptionVerifyView, PaystackWebhookView

urlpatterns = [
    path('subscription/status/', SubscriptionStatusView.as_view(), name='subscription-status'),
    path('subscription/checkout/', SubscriptionCheckoutView.as_view(), name='subscription-checkout'),
    path('subscription/verify/', SubscriptionVerifyView.as_view(), name='subscription-verify'),
    path('subscription/paystack/webhook/', PaystackWebhookView.as_view(), name='paystack-webhook'),
]
