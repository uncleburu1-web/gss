from django.contrib import admin
from .models import Worker, AttendanceRecord


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'role', 'phone', 'is_active', 'can_login', 'hire_date')
    list_filter = ('role', 'is_active')
    search_fields = ('full_name', 'phone')


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """Support/debug view only — the Attendance page is how reception/an
    owner actually use this day to day."""
    list_display = ('worker', 'date', 'status', 'marked_by', 'shop')
    list_filter = ('status', 'date')
    search_fields = ('worker__full_name',)
