from django.urls import path
from rest_framework.routers import DefaultRouter

from . import reports, views

router = DefaultRouter()
router.register(r'restaurant/areas', views.DiningAreaViewSet, basename='restaurant-area')
router.register(r'restaurant/tables', views.RestaurantTableViewSet, basename='restaurant-table')
router.register(r'restaurant/stations', views.KitchenStationViewSet, basename='restaurant-station')
router.register(r'restaurant/menu/categories', views.MenuCategoryViewSet, basename='restaurant-menu-category')
router.register(r'restaurant/menu/items', views.MenuItemViewSet, basename='restaurant-menu-item')
router.register(r'restaurant/menu/modifier-groups', views.ModifierGroupViewSet, basename='restaurant-modifier-group')
router.register(r'restaurant/menu/modifier-options', views.ModifierOptionViewSet, basename='restaurant-modifier-option')
router.register(r'restaurant/menu/item-modifier-groups', views.MenuItemModifierGroupViewSet, basename='restaurant-item-modifier-group')
router.register(r'restaurant/tax-rules', views.TaxRuleViewSet, basename='restaurant-tax-rule')

urlpatterns = [
    # Before the router, so "activate" is not read as a tax rule id.
    path('restaurant/tax-rules/activate/', views.TaxActivationView.as_view(), name='restaurant-tax-activate'),
    path('restaurant/settings/', views.RestaurantSettingsView.as_view(), name='restaurant-settings'),
    path('restaurant/settings/hub/', views.HubDeviceView.as_view(), name='restaurant-hub'),
    path('restaurant/routing/preview/', views.RoutingPreviewView.as_view(), name='restaurant-routing-preview'),
    path('restaurant/orders/', reports.OrderListView.as_view(), name='restaurant-orders'),
    path('restaurant/orders/<uuid:pk>/', reports.OrderDetailView.as_view(), name='restaurant-order'),
    path('reports/restaurant/summary/', reports.RestaurantSummaryView.as_view(), name='restaurant-summary'),
] + router.urls
