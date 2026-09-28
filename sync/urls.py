from django.urls import path

from .views import SyncPushView, SyncPullView, SyncReconcileView

urlpatterns = [
    path('sync/push/', SyncPushView.as_view(), name='sync-push'),
    path('sync/pull/', SyncPullView.as_view(), name='sync-pull'),
    path('sync/reconcile/', SyncReconcileView.as_view(), name='sync-reconcile'),
]
