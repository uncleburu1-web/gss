from django.contrib import admin
from .models import Expense, ExpenseCategory


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'shop', 'is_default')
    list_filter = ('is_default',)
    search_fields = ('name',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    """Support/debug view only — the Expenses page is how this is actually used day to day."""
    list_display = ('category', 'amount', 'payment_method', 'date', 'shop', 'recorded_by')
    list_filter = ('category', 'payment_method', 'date')
    search_fields = ('description', 'notes')
