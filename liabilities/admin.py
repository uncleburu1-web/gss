from django.contrib import admin
from .models import Liability


@admin.register(Liability)
class LiabilityAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'amount', 'due_date', 'status')
    list_filter = ('category', 'status')
    search_fields = ('name',)
