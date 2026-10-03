from django.contrib import admin
<<<<<<< HEAD
from .models import Liability
=======
from .models import Liability, LiabilityPayment


class LiabilityPaymentInline(admin.TabularInline):
    model = LiabilityPayment
    extra = 0
    fields = ('amount', 'payment_method', 'paid_at', 'recorded_by', 'notes')
    readonly_fields = ('paid_at',)
>>>>>>> 6f155c9 (Add expense support)


@admin.register(Liability)
class LiabilityAdmin(admin.ModelAdmin):
<<<<<<< HEAD
    list_display = ('name', 'category', 'amount', 'due_date', 'status')
    list_filter = ('category', 'status')
    search_fields = ('name',)
=======
    list_display = ('name', 'category', 'owed_to', 'amount', 'status', 'due_date')
    list_filter = ('category', 'status')
    search_fields = ('name', 'owed_to')
    inlines = [LiabilityPaymentInline]


@admin.register(LiabilityPayment)
class LiabilityPaymentAdmin(admin.ModelAdmin):
    list_display = ('liability', 'amount', 'payment_method', 'paid_at', 'recorded_by')
    list_filter = ('payment_method',)
    search_fields = ('liability__name',)
>>>>>>> 6f155c9 (Add expense support)
