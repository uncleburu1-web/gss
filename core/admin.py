from django.contrib import admin
from .models import Shop, EmailOTP, RolePermission


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    list_display = ('name', 'currency', 'tax_rate_default', 'created_at')
    search_fields = ('name',)


@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    """Support/debug view only — the Control Center screen in Settings is
    how an owner/CEO actually manages these day to day."""
    list_display = ('organization', 'role', 'capability', 'allowed', 'updated_at')
    list_filter = ('role', 'capability', 'allowed')
    search_fields = ('organization__name',)


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    """Support/debug view only -- e.g. to see whether a code was ever
    generated for someone stuck at the verify-email screen, without
    needing shell access."""
    list_display = ('user', 'code', 'created_at', 'expires_at', 'is_used', 'attempts')
    search_fields = ('user__username', 'user__email', 'code')
    list_filter = ('is_used',)
    readonly_fields = ('created_at',)
