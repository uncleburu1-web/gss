from django.urls import path
from . import views

urlpatterns = [
    path('sales-summary/', views.SalesSummaryView.as_view(), name='report-sales-summary'),
    path('sales-by-item/', views.SalesByItemView.as_view(), name='report-sales-by-item'),
    path('best-selling/', views.BestSellingView.as_view(), name='report-best-selling'),
    path('sales-by-category/', views.SalesByCategoryView.as_view(), name='report-sales-by-category'),
    path('sales-by-staff/', views.SalesByStaffView.as_view(), name='report-sales-by-staff'),
    path('payment-method/', views.PaymentMethodView.as_view(), name='report-payment-method'),
    path('sales-by-customer/', views.SalesByCustomerView.as_view(), name='report-sales-by-customer'),
    path('tax/', views.TaxReportView.as_view(), name='report-tax'),
    path('expiring-inventory/', views.ExpiringInventoryView.as_view(), name='report-expiring-inventory'),
    path('inventory-valuation/', views.InventoryValuationView.as_view(), name='report-inventory-valuation'),
    path('net-worth/', views.NetWorthView.as_view(), name='report-net-worth'),
]
