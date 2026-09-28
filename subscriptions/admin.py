from django.contrib import admin
from .models import Subscription, SubscriptionPayment


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('organization', 'is_enterprise', 'billing_cycle', 'status', 'effective_status', 'total_price_ngn', 'current_period_end')
    list_filter = ('is_enterprise', 'billing_cycle', 'status')
    search_fields = ('organization__name',)


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ('organization', 'amount_kobo', 'paystack_reference', 'paid_at')
    search_fields = ('organization__name', 'paystack_reference')
