from django.db import models
from decimal import Decimal
from core.models import SyncModel


class InventoryItem(SyncModel):
    # One shared list across every business type rather than a separate
    # table per industry — the field itself never hits a hard wall, so a
    # shop that changes business type, or genuinely stocks a mixed
    # catalog, is never blocked from saving an out-of-group category.
    # What DOES change per business type is which subset gets *offered*
    # on the category picker — see CATEGORY_GROUPS/category_choices_for()
    # below, which is the single source of truth every client (web,
    # mobile, desktop) reads from via MeView's `available_categories`
    # instead of each hand-rolling its own per-type list. That's what
    # keeps a clothing shop from being offered "Laptop" and a gadgets
    # shop from being offered "Medicine".
    CATEGORY_CHOICES = [
        ('laptop', 'Laptop'),
        ('part', 'Part'),
        ('accessory', 'Accessory'),
        ('consumable', 'Consumable'),
        ('medicine', 'Medicine / Drug'),
        ('tablet_capsule', 'Tablet / Capsule'),
        ('syrup_liquid', 'Syrup / Liquid'),
        ('injection', 'Injection'),
        ('medical_supply', 'Medical supply'),
        ('equipment', 'Equipment'),
        ('apparel_top', 'Top / Shirt'),
        ('apparel_bottom', 'Trousers / Skirt'),
        ('footwear', 'Footwear'),
        ('outerwear', 'Outerwear'),
        ('clothing_accessory', 'Bag / Fashion accessory'),
        ('grocery', 'Grocery'),
        ('household', 'Household item'),
        ('stationery', 'Stationery'),
        ('beverage', 'Beverage'),
        ('other', 'Other'),
    ]

    # business_type -> the category codes (above) relevant to it, in the
    # order they should be offered. 'other' is deliberately left out of
    # every group here and appended once by category_choices_for() so it
    # doesn't need repeating, and always sorts last.
    CATEGORY_GROUPS = {
        'gadgets': ['laptop', 'part', 'accessory', 'consumable'],
        'pharmacy': ['medicine', 'tablet_capsule', 'syrup_liquid', 'injection', 'medical_supply', 'equipment'],
        'clothing': ['apparel_top', 'apparel_bottom', 'footwear', 'outerwear', 'clothing_accessory'],
        'general': ['grocery', 'household', 'stationery', 'beverage'],
    }

    @classmethod
    def category_choices_for(cls, business_type):
        """Ordered [{value, label}, ...] for the category picker on one
        business type — that type's own group first, 'other' always last.
        This is what every client should render instead of the full
        cross-industry CATEGORY_CHOICES list, so a clothing till is never
        offered "Laptop" and a gadgets till is never offered "Medicine".
        Falls back to just 'other' for an unrecognized business_type
        rather than raising, since new/unknown types should degrade to
        "everything is Other", not error out."""
        labels = dict(cls.CATEGORY_CHOICES)
        codes = cls.CATEGORY_GROUPS.get(business_type, [])
        choices = [{'value': c, 'label': labels[c]} for c in codes]
        choices.append({'value': 'other', 'label': labels['other']})
        return choices

    name = models.CharField(max_length=200)
    short_code = models.CharField(max_length=40, blank=True, help_text='Short label e.g. "Para500"')
    barcode = models.CharField(
        max_length=64, null=True, blank=True, unique=True, db_index=True,
        help_text='Scanned/printed barcode (UPC/EAN or a shop-assigned code). Null (not empty string) when '
                   'unset so multiple barcode-less products never collide on the unique constraint.',
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    brand = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=30, default='PIECE', help_text='e.g. TABLET, PIECE, BOX, PACK')
    spec = models.CharField(max_length=100, blank=True, help_text='e.g. 200mg, 15.6-inch')
    min_stock = models.PositiveIntegerField(default=2, help_text='Reorder alert threshold')
    prescription_required = models.BooleanField(
        default=False,
        help_text='Pharmacy shops only — flags a drug that legally cannot be sold without a prescription. '
                   'Harmless (and hidden in the UI) for every other business type.',
    )
    sell_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        help_text='Current default unit selling price (kept in sync with the latest batch, editable)',
    )
    # Optional product photo. Entirely optional at every layer -- a
    # product with no image is a completely normal, fully-supported state
    # (image_url/cloudinary_public_id just stay null/blank), never a
    # required or partially-filled-in row. Only ever set/cleared through
    # InventoryItemViewSet's `image` action (see views.py) -- deliberately
    # NOT writable on the main serializer, so a client can't point a
    # product at an arbitrary URL instead of an image this backend has
    # actually validated and uploaded itself.
    #
    # No image binary is ever stored in Postgres -- Cloudinary hosts the
    # file, this just keeps the pointer to it (same reasoning as
    # core.models.Shop.logo_url, which predates this and does the same
    # thing for a different reason: no persistent media storage is
    # configured on this backend at all, Railway's filesystem is
    # ephemeral).
    image_url = models.URLField(
        max_length=500, null=True, blank=True,
        help_text='Cloudinary-hosted product photo URL. Null when no image has been uploaded -- a '
                   'perfectly normal, fully-supported state, not an error or an incomplete product.',
    )
    cloudinary_public_id = models.CharField(
        max_length=255, blank=True,
        help_text='Cloudinary asset id backing image_url -- needed to replace/delete the asset later. '
                   'Internal bookkeeping only; never exposed on the public serializer.',
    )
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.quantity} in stock)'

    # --- computed from batches -------------------------------------------------
    @property
    def quantity(self):
        return self.batches.filter(is_deleted=False).aggregate(
            models.Sum('quantity_remaining')
        )['quantity_remaining__sum'] or 0

    @property
    def cost_price(self):
        """Weighted-average cost across remaining batch stock."""
        remaining = [b for b in self.batches.filter(is_deleted=False) if b.quantity_remaining > 0]
        total_qty = sum(b.quantity_remaining for b in remaining)
        if not total_qty:
            return 0
        total_cost = sum(b.quantity_remaining * b.cost_price for b in remaining)
        return round(total_cost / total_qty, 2)

    @property
    def is_low_stock(self):
        return self.quantity <= self.min_stock

    @property
    def stock_value(self):
        return sum(b.quantity_remaining * b.cost_price for b in self.batches.filter(is_deleted=False))

    @property
    def batch_count(self):
        return self.batches.filter(is_deleted=False).count()

    @property
    def has_variants(self):
        return self.variants.filter(is_deleted=False).exists()


class ItemVariant(SyncModel):
    """One size/color (or other attribute) variant of a parent
    InventoryItem — e.g. a "Men's Polo Shirt" item might have separate
    Medium/Blue and Large/Black variants, each tracked as its own stock.
    Exists mainly for clothing & fashion shops, where selling "a shirt"
    without knowing which size left the shelf isn't good enough — but
    nothing here is clothing-specific; any item can have variants if a
    shop finds attribute-level stock useful (e.g. a phone case in several
    colors, for a gadgets shop).

    An item with zero variants behaves exactly as it did before this
    model existed — its batches attach directly to it (StockBatch.variant
    left null) and nothing about that path changed. An item WITH variants
    should have every new batch attached to one of its variants instead;
    that's enforced by the UI/serializer layer; not the DB, so it can
    never invalidate data created before this feature existed.
    """
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='variants')
    size = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=40, blank=True)
    sku = models.CharField(max_length=60, blank=True, help_text='Optional variant-specific code, e.g. for its own barcode label')
    is_active = models.BooleanField(default=True, help_text='Retire a discontinued variant without losing its sale history')
    # created_at / updated_at / is_deleted / shop / id (UUID) come from SyncModel.

    class Meta:
        ordering = ['size', 'color']
        constraints = [
            models.UniqueConstraint(fields=['item', 'size', 'color'], name='unique_variant_per_item'),
        ]

    def __str__(self):
        return f'{self.item.name} — {self.label}'

    @property
    def label(self):
        parts = [p for p in (self.size, self.color) if p]
        return ' / '.join(parts) if parts else (self.sku or 'Variant')

    # --- computed from batches, same shape as InventoryItem's own -------------
    @property
    def quantity(self):
        return self.batches.filter(is_deleted=False).aggregate(
            models.Sum('quantity_remaining')
        )['quantity_remaining__sum'] or 0

    @property
    def is_low_stock(self):
        return self.quantity <= self.item.min_stock


class StockBatch(SyncModel):
    item = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='batches')
    variant = models.ForeignKey(
        ItemVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name='batches',
        help_text='Which size/color this stock is for, on an item that has variants. Left null for an '
                   'item with none — unchanged from how batches worked before variants existed.',
    )
    batch_number = models.CharField(max_length=60)
    quantity_received = models.PositiveIntegerField()
    quantity_remaining = models.PositiveIntegerField()
    cost_price = models.DecimalField(max_digits=12, decimal_places=2)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    expiry_date = models.DateField(null=True, blank=True)
    supplier = models.ForeignKey(
        'suppliers.Supplier', on_delete=models.SET_NULL, null=True, blank=True, related_name='batches'
    )
    supplier_name = models.CharField(max_length=200, blank=True, help_text='Fallback if no linked Supplier record')
    received_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['expiry_date', 'received_date']

    def __str__(self):
        return f'{self.item.name} — batch {self.batch_number}'

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not self.quantity_remaining:
            self.quantity_remaining = self.quantity_received
        super().save(*args, **kwargs)
        if is_new:
            # Keep the item's headline sell price in sync with the newest batch.
            self.item.sell_price = self.selling_price
            self.item.save(update_fields=['sell_price', 'updated_at'])

    @property
    def is_expiring_soon(self):
        if not self.expiry_date:
            return False
        from django.utils import timezone
        days_left = (self.expiry_date - timezone.localdate()).days
        return 0 <= days_left <= 30

    @property
    def is_expired(self):
        if not self.expiry_date:
            return False
        from django.utils import timezone
        return self.expiry_date < timezone.localdate()

