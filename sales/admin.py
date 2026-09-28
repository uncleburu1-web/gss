from django.contrib import admin
from .models import Sale, SaleItem, SaleAllocation


class SaleAllocationInline(admin.TabularInline):
    model = SaleAllocation
    extra = 0


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'id', 'customer_name', 'total', 'profit', 'status', 'payment_method', 'date')
    list_filter = ('status', 'payment_method')
    search_fields = ('customer_name', 'staff_name', 'items__item_name')
    inlines = [SaleItemInline]


@admin.register(SaleItem)
class SaleItemAdmin(admin.ModelAdmin):
    list_display = ('item_name', 'quantity', 'unit_price', 'total', 'profit', 'category', 'sale')
    list_filter = ('category',)
    search_fields = ('item_name',)
    inlines = [SaleAllocationInline]
