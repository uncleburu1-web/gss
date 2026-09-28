from django.contrib import admin
from .models import Device


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'device_type', 'shop', 'last_seen_at', 'is_online')
    list_filter = ('device_type',)
    search_fields = ('name',)
