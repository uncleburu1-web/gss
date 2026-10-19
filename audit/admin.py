from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('at', 'actor_name', 'action', 'summary', 'shop', 'source')
    list_filter = ('action', 'source')
    search_fields = ('actor_name', 'summary', 'target_id')

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
