from rest_framework import serializers

from .models import (SERVICE_TYPES, DiningArea, KitchenStation, MenuCategory, MenuItem, MenuItemModifierGroup,
                     ModifierGroup, ModifierOption, RestaurantSettings, RestaurantTable, TaxRule)

READ_ONLY = ('id', 'created_at', 'updated_at')


class BranchScopedSerializer(serializers.ModelSerializer):
    """Every foreign key a client sends must point at a row of the SAME branch. A row from another
    branch or business is answered as if it did not exist (it never reveals that it does)."""
    branch_fields = ()
    # Fields that must be unique among the branch's (not deleted) rows, optionally within a parent:
    # e.g. (('name',),) or (('group', 'name'),). Checked here so a duplicate is a clear 400.
    unique_within_branch = ()

    def validate(self, attrs):
        branch = self.context['branch']
        for field in self.branch_fields:
            obj = attrs.get(field)
            if obj is not None and (obj.shop_id != branch.id or obj.is_deleted):
                raise serializers.ValidationError({field: 'Not found in this branch.'})
        model = self.Meta.model
        for fields in self.unique_within_branch:
            values = {f: attrs.get(f, getattr(self.instance, f, None)) for f in fields}
            qs = model.objects.filter(shop=branch, is_deleted=False, **values)
            if self.instance is not None:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({fields[-1]: 'This branch already has one with that name.'})
        return attrs


class RestaurantSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RestaurantSettings
        fields = ['id', 'service_charge_rate', 'service_charge_types',
                  'require_served_before_payment', 'order_number_prefix', 'business_day_starts_at',
                  'hub_device', 'tax_state', 'vat_registered', 'tax_treatment_confirmed_at', 'updated_at']
        # The hub and the tax confirmation have their own endpoints with their own checks.
        read_only_fields = ['id', 'hub_device', 'tax_state', 'vat_registered', 'tax_treatment_confirmed_at', 'updated_at']

    def validate_service_charge_types(self, value):
        if not isinstance(value, list) or any(v not in SERVICE_TYPES for v in value):
            raise serializers.ValidationError(f'Choose from {", ".join(SERVICE_TYPES)}.')
        return sorted(set(value))


class DiningAreaSerializer(BranchScopedSerializer):
    unique_within_branch = (('name',),)
    class Meta:
        model = DiningArea
        fields = ['id', 'name', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY


class RestaurantTableSerializer(BranchScopedSerializer):
    unique_within_branch = (('label',),)
    branch_fields = ('area',)

    class Meta:
        model = RestaurantTable
        fields = ['id', 'area', 'label', 'seats', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY


class KitchenStationSerializer(BranchScopedSerializer):
    unique_within_branch = (('name',),)
    class Meta:
        model = KitchenStation
        fields = ['id', 'name', 'prints', 'shows_on_screen', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY

    def validate(self, attrs):
        attrs = super().validate(attrs)
        prints = attrs.get('prints', getattr(self.instance, 'prints', True))
        shows = attrs.get('shows_on_screen', getattr(self.instance, 'shows_on_screen', False))
        if not (prints or shows):
            raise serializers.ValidationError('A station must print its tickets, show them on a screen, or both.')
        return attrs


class MenuCategorySerializer(BranchScopedSerializer):
    unique_within_branch = (('name',),)
    branch_fields = ('station',)

    class Meta:
        model = MenuCategory
        fields = ['id', 'name', 'station', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY


class MenuItemSerializer(BranchScopedSerializer):
    unique_within_branch = (('name',),)
    branch_fields = ('category', 'station')
    modifier_groups = serializers.SerializerMethodField()
    effective_station = serializers.SerializerMethodField()

    class Meta:
        model = MenuItem
        fields = ['id', 'category', 'name', 'description', 'price', 'station', 'effective_station', 'tax_class',
                  'is_available', 'image_url', 'sort', 'is_active', 'modifier_groups', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY

    def get_modifier_groups(self, obj):
        return [str(link.group_id) for link in obj.modifier_links.all() if not link.is_deleted]

    def get_effective_station(self, obj):
        station = obj.effective_station
        return str(station.id) if station else None

    def validate_tax_class(self, value):
        value = (value or '').strip().lower()
        if not value:
            raise serializers.ValidationError('Give the item a tax class, e.g. food or drink.')
        return value


class ModifierGroupSerializer(BranchScopedSerializer):
    unique_within_branch = (('name',),)
    class Meta:
        model = ModifierGroup
        fields = ['id', 'name', 'min_select', 'max_select', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY

    def validate(self, attrs):
        attrs = super().validate(attrs)
        low = attrs.get('min_select', getattr(self.instance, 'min_select', 0))
        high = attrs.get('max_select', getattr(self.instance, 'max_select', 1))
        if low > high:
            raise serializers.ValidationError('The minimum number of choices cannot be more than the maximum.')
        return attrs


class ModifierOptionSerializer(BranchScopedSerializer):
    unique_within_branch = (('group', 'name'),)
    branch_fields = ('group',)

    class Meta:
        model = ModifierOption
        fields = ['id', 'group', 'name', 'price_delta', 'is_available', 'sort', 'is_active', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY


class MenuItemModifierGroupSerializer(BranchScopedSerializer):
    unique_within_branch = (('menu_item', 'group'),)
    branch_fields = ('menu_item', 'group')

    class Meta:
        model = MenuItemModifierGroup
        fields = ['id', 'menu_item', 'group', 'sort', 'created_at', 'updated_at']
        read_only_fields = READ_ONLY


class TaxRuleSerializer(BranchScopedSerializer):
    class Meta:
        model = TaxRule
        fields = ['id', 'name', 'rate', 'inclusive', 'tax_classes', 'applies_to_service_charge', 'valid_from',
                  'valid_to', 'is_active', 'activated_at', 'overlap_confirmed', 'created_at', 'updated_at']
        # A rule is switched ON only by the activation endpoint (REQUIREMENTS §4); it can be switched
        # OFF with a normal update.
        read_only_fields = READ_ONLY + ('activated_at', 'overlap_confirmed')

    def validate_tax_classes(self, value):
        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
            raise serializers.ValidationError('A list of tax classes, e.g. ["food", "drink"], or empty for every item.')
        return sorted({v.strip().lower() for v in value})

    def validate_is_active(self, value):
        if value and not (self.instance and self.instance.is_active):
            raise serializers.ValidationError('Activate taxes with the activation step, which records the checks.')
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        start = attrs.get('valid_from', getattr(self.instance, 'valid_from', None))
        end = attrs.get('valid_to', getattr(self.instance, 'valid_to', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'valid_to': 'Must be after the start date.'})
        if self.instance is not None and self.instance.is_active:
            changed = [f for f in TaxRule.AMOUNT_FIELDS if f in attrs and attrs[f] != getattr(self.instance, f)]
            if changed:
                raise serializers.ValidationError({
                    'detail': 'An active tax cannot change its rate, base or dates. Switch it off and add a new '
                              'rule from the date the change applies, so past records stay true.',
                    'code': 'active_rule_locked'})
        return attrs
