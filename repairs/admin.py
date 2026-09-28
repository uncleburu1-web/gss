from django.contrib import admin
from .models import RepairTicket, RepairPart


class RepairPartInline(admin.TabularInline):
    model = RepairPart
    extra = 0
    readonly_fields = ('unit_cost', 'added_at')


@admin.register(RepairTicket)
class RepairTicketAdmin(admin.ModelAdmin):
    list_display = ('ticket_no', 'customer_name', 'device', 'status', 'priority', 'technician', 'cost', 'date_in', 'date_out')
    list_filter = ('status', 'priority')
    search_fields = ('ticket_no', 'customer_name', 'device')
    inlines = [RepairPartInline]
