from django.contrib import admin

from .models import RealtimeEvent


@admin.register(RealtimeEvent)
class RealtimeEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'shop', 'sequence', 'created_at')
    list_filter = ('event_type',)
    search_fields = ('shop__name', 'event_type')
    ordering = ('-created_at',)
    readonly_fields = ('id', 'shop', 'sequence', 'event_type', 'payload', 'created_at')

    def has_add_permission(self, request):
        # This table is a log, not something anyone should hand-create
        # from the admin -- entries only ever come from events.broadcast().
        return False
