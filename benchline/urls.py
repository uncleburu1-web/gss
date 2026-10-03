from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import TokenRefreshView

admin.site.site_header = 'Benchline'
admin.site.site_title = 'Benchline Admin'
admin.site.index_title = 'Shop Management'

from core.views import RegisterView, DeviceAwareLoginView, ChangePasswordView, VerifyOTPView, ResendOTPView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Auth
    path('api/auth/register/', RegisterView.as_view(), name='register'),
    path('api/auth/verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('api/auth/resend-otp/', ResendOTPView.as_view(), name='resend-otp'),
    path('api/auth/login/', DeviceAwareLoginView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/change-password/', ChangePasswordView.as_view(), name='change-password'),

    # App routes
    path('api/', include('core.urls')),
    path('api/', include('staff.urls')),
    path('api/', include('suppliers.urls')),
    path('api/inventory/', include('inventory.urls')),
    # URL says "service" (what every client calls it) even though the app
    # underneath is still named `repairs` — same reasoning as Shop/Branch
    # in core.models: renaming the Django app itself would mean rewriting
    # migration history and DB table names for a purely cosmetic reason.
    path('api/service/', include('repairs.urls')),
    path('api/', include('sales.urls')),
    path('api/', include('liabilities.urls')),
    path('api/', include('expenses.urls')),
    path('api/reports/', include('reports.urls')),
    path('api/', include('devices.urls')),
    path('api/', include('subscriptions.urls')),
    path('api/', include('sync.urls')),
    path('api/', include('customers.urls')),
    path('api/', include('branches.urls')),
    path('api/realtime/', include('realtime.urls')),
]
