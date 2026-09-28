from realtime.events import broadcast
from .utils import get_shop_for_user, get_shops_for_user


class ShopScopedMixin:
    """Mix into any ModelViewSet whose model now inherits SyncModel.

    Read paths (list/retrieve) use get_shops_for_user -- a CEO sees every
    branch in their organization by default (or one, if X-Branch-ID names
    it), a branch-scoped user always sees just their own branch. Write
    paths (create/update) resolve to exactly ONE branch via
    get_shop_for_user -- see core.utils for why these are different
    functions.

    Also broadcasts every create/update/delete over the shop's WebSocket
    group (realtime.events) -- this is what makes "CEO adds a product, the
    seller sees it without refreshing" work for every resource that uses
    this mixin, with no extra code per-app. A ViewSet with its own custom
    perform_create/perform_destroy (SaleViewSet, which needs FEFO stock
    allocation) isn't covered by this and broadcasts explicitly instead --
    see sales/views.py.
    """

    def _branch_id_header(self):
        return self.request.headers.get('X-Branch-ID') or None

    def get_current_shop(self):
        return get_shop_for_user(self.request.user, branch_id=self._branch_id_header())

    def get_queryset(self):
        # Every model this mixin is used on has a `shop` FK (SyncModel) --
        # scoping the base queryset here, not just create/update, is the
        # other half of tenant isolation: without this, shop A's owner
        # could list/retrieve/edit shop B's rows just by knowing their ID.
        shops = get_shops_for_user(self.request.user, branch_id=self._branch_id_header())
        return super().get_queryset().filter(shop__in=shops)

    def _event_prefix(self, instance):
        return instance._meta.model_name  # e.g. 'inventoryitem', 'customer', 'stockbatch'

    def perform_create(self, serializer):
        serializer.save(shop=self.get_current_shop())
        instance = serializer.instance
        broadcast(instance.shop, f'{self._event_prefix(instance)}.created', {'id': str(instance.pk)})

    def perform_update(self, serializer):
        serializer.save()
        instance = serializer.instance
        broadcast(instance.shop, f'{self._event_prefix(instance)}.updated', {'id': str(instance.pk)})

    def perform_destroy(self, instance):
        # Soft delete: a hard SQL DELETE never reaches the sync queue, so a
        # deletion made on one device would just silently fail to ever
        # reach the others. Flip the tombstone instead -- every read path
        # (viewset querysets, reports) already filters `is_deleted=False`.
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted', 'updated_at'])
        broadcast(instance.shop, f'{self._event_prefix(instance)}.deleted', {'id': str(instance.pk)})
