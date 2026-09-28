from django.contrib import admin

from .models import SyncOperation


@admin.register(SyncOperation)
class SyncOperationAdmin(admin.ModelAdmin):
    list_display = ('id', 'shop', 'entity_type', 'operation', 'entity_id', 'applied_at')
    list_filter = ('entity_type', 'operation', 'shop')
    search_fields = ('id', 'entity_id')
    readonly_fields = [f.name for f in SyncOperation._meta.fields]

    def has_add_permission(self, request):
        return False
