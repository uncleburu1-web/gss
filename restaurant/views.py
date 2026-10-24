"""Restaurant configuration API (docs/restaurant/API_SPECIFICATION.md §1).

Every request acts on ONE branch (restaurant.permissions.resolve_branch). Every change is audited
(`restaurant.config.changed`) and announced to the branch (`restaurant.config_changed`) so its hub
pulls the new configuration. Deleting is a soft delete, so past orders keep their references.
"""
import uuid

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

import audit
from devices.models import Device
from realtime.events import broadcast

from . import routing
from .models import KitchenStation, MenuItem, RestaurantSettings, TaxRule
from .permissions import RestaurantAccess, resolve_branch
from .serializers import (DiningAreaSerializer, KitchenStationSerializer, MenuCategorySerializer,
                          MenuItemModifierGroupSerializer, MenuItemSerializer, ModifierGroupSerializer,
                          ModifierOptionSerializer, RestaurantSettingsSerializer, RestaurantTableSerializer,
                          TaxRuleSerializer)


def _uuids(values):
    """Only well-formed ids reach the database (a malformed one would otherwise raise a 500)."""
    out = []
    for v in values if isinstance(values, list) else []:
        try:
            out.append(str(uuid.UUID(str(v))))
        except (ValueError, TypeError, AttributeError):
            continue
    return out


def _error(message, code, http=status.HTTP_400_BAD_REQUEST):
    return Response({'detail': message, 'code': code}, status=http)


def announce(branch, user, entity, obj, verb, summary=''):
    audit.record('restaurant.config.changed', shop=branch, user=user, target=obj,
                 summary=summary or f'{entity} {verb}: {obj}', data={'entity': entity, 'verb': verb})
    broadcast(branch, 'restaurant.config_changed', {'entity': entity, 'id': str(obj.pk), 'verb': verb})


class ConfigViewSet(viewsets.ModelViewSet):
    permission_classes = [RestaurantAccess]
    entity = ''
    select = ()

    @property
    def branch(self):
        return resolve_branch(self.request)

    def get_queryset(self):
        qs = self.serializer_class.Meta.model.objects.filter(shop=self.branch, is_deleted=False)
        if self.select:
            qs = qs.select_related(*self.select)
        if self.request.query_params.get('active') in ('1', 'true') and hasattr(self.serializer_class.Meta.model, 'is_active'):
            qs = qs.filter(is_active=True)
        return qs

    def get_serializer_context(self):
        return {**super().get_serializer_context(), 'branch': self.branch}

    def perform_create(self, serializer):
        try:
            with transaction.atomic():
                obj = serializer.save(shop=self.branch)
                announce(self.branch, self.request.user, self.entity, obj, 'created')
        except IntegrityError:  # two saves racing past the uniqueness check
            raise ValidationError({'detail': 'This branch already has one with that name.', 'code': 'duplicate'})

    def perform_update(self, serializer):
        try:
            with transaction.atomic():
                obj = serializer.save()
                announce(self.branch, self.request.user, self.entity, obj, 'changed')
        except IntegrityError:
            raise ValidationError({'detail': 'This branch already has one with that name.', 'code': 'duplicate'})

    def perform_destroy(self, instance):
        with transaction.atomic():
            instance.is_deleted = True
            instance.save(update_fields=['is_deleted', 'updated_at'])
            announce(self.branch, self.request.user, self.entity, instance, 'deleted')


class DiningAreaViewSet(ConfigViewSet):
    serializer_class = DiningAreaSerializer
    entity = 'dining_area'


class RestaurantTableViewSet(ConfigViewSet):
    serializer_class = RestaurantTableSerializer
    entity = 'restaurant_table'
    select = ('area',)


class KitchenStationViewSet(ConfigViewSet):
    serializer_class = KitchenStationSerializer
    entity = 'kitchen_station'


class MenuCategoryViewSet(ConfigViewSet):
    serializer_class = MenuCategorySerializer
    entity = 'menu_category'


class MenuItemViewSet(ConfigViewSet):
    serializer_class = MenuItemSerializer
    entity = 'menu_item'
    select = ('category__station', 'station')

    def get_queryset(self):
        return super().get_queryset().prefetch_related('modifier_links')


class ModifierGroupViewSet(ConfigViewSet):
    serializer_class = ModifierGroupSerializer
    entity = 'modifier_group'


class ModifierOptionViewSet(ConfigViewSet):
    serializer_class = ModifierOptionSerializer
    entity = 'modifier_option'


class MenuItemModifierGroupViewSet(ConfigViewSet):
    serializer_class = MenuItemModifierGroupSerializer
    entity = 'menu_item_modifier_group'


class TaxRuleViewSet(ConfigViewSet):
    serializer_class = TaxRuleSerializer
    entity = 'tax_rule'


def settings_for(branch):
    obj, _ = RestaurantSettings.objects.get_or_create(shop=branch, is_deleted=False,
                                                      defaults={'service_charge_types': ['dine_in']})
    return obj


class RestaurantSettingsView(APIView):
    """GET / PUT /api/restaurant/settings/"""
    permission_classes = [RestaurantAccess]

    def get(self, request):
        return Response(RestaurantSettingsSerializer(settings_for(resolve_branch(request))).data)

    def put(self, request):
        branch = resolve_branch(request)
        serializer = RestaurantSettingsSerializer(settings_for(branch), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            obj = serializer.save()
            announce(branch, request.user, 'restaurant_settings', obj, 'changed', 'Restaurant settings changed')
        return Response(serializer.data)


class HubDeviceView(APIView):
    """PUT /api/restaurant/settings/hub/ {"device_id": …}: make one of this branch's tills the hub.
    Only one hub per branch, so two tills can never both run the restaurant."""
    permission_classes = [RestaurantAccess]

    def put(self, request):
        branch = resolve_branch(request)
        ids = _uuids([request.data.get('device_id')])
        device = Device.objects.filter(id=ids[0], shop=branch, is_deleted=False).first() if ids else None
        if device is None:
            return _error('That till is not paired to this branch.', 'unknown_device', status.HTTP_404_NOT_FOUND)
        if device.device_type != 'desktop':
            return _error('Only a desktop till can be the restaurant hub.', 'not_a_till')
        with transaction.atomic():
            obj = settings_for(branch)
            previous = obj.hub_device_id
            obj.hub_device = device
            obj.save(update_fields=['hub_device', 'updated_at'])
            announce(branch, request.user, 'restaurant_settings', obj, 'changed',
                     f'Restaurant hub set to {device.name or device.id}' + (' (replacing another till)' if previous and previous != device.id else ''))
        return Response(RestaurantSettingsSerializer(obj).data)


class TaxActivationView(APIView):
    """POST /api/restaurant/tax-rules/activate/ — the only way a tax starts being charged
    (owner's decision 4; REQUIREMENTS §4):

      * the owner records the branch's state and VAT registration and confirms the treatment was
        checked; without that confirmation nothing is activated (400 confirm_treatment);
      * if two active rules would apply to the same item on the same day, activation is refused
        (409 overlapping_taxes) unless the owner explicitly confirms both apply.
    """
    permission_classes = [RestaurantAccess]

    def post(self, request):
        branch = resolve_branch(request)
        data = request.data
        ids = _uuids(data.get('rule_ids'))
        state = (data.get('state') or '').strip()
        if data.get('treatment_confirmed') is not True:
            return _error('Confirm the tax treatment was checked (with your accountant) before charging tax.', 'confirm_treatment')
        if not state:
            return _error('Say which state this branch is in.', 'state_required')
        if not isinstance(data.get('vat_registered'), bool):
            return _error('Say whether the business is registered for VAT.', 'vat_registration_required')
        rules = list(TaxRule.objects.filter(shop=branch, is_deleted=False, id__in=ids))
        if not ids or len(rules) != len(set(ids)) or len(ids) != len(data.get('rule_ids') or []):
            return _error('Choose tax rules of this branch to activate.', 'unknown_tax_rule', status.HTTP_404_NOT_FOUND)

        others = list(TaxRule.objects.filter(shop=branch, is_deleted=False, is_active=True).exclude(id__in=[r.id for r in rules]))
        pool = others + rules
        clashes = sorted({tuple(sorted((a.name, b.name))) for a in rules for b in pool if a.overlaps(b)})
        if clashes and data.get('overlap_confirmed') is not True:
            return Response({
                'detail': 'These taxes would both apply to the same items: ' + '; '.join(' + '.join(c) for c in clashes)
                          + '. Confirm that both legally apply before activating.',
                'code': 'overlapping_taxes', 'overlaps': [list(c) for c in clashes]}, status=status.HTTP_409_CONFLICT)

        now = timezone.now()
        with transaction.atomic():
            for rule in rules:
                rule.is_active = True
                rule.activated_by = request.user
                rule.activated_at = now
                rule.overlap_confirmed = bool(clashes)
                rule.save(update_fields=['is_active', 'activated_by', 'activated_at', 'overlap_confirmed', 'updated_at'])
            obj = settings_for(branch)
            obj.tax_state = state
            obj.vat_registered = data['vat_registered']
            obj.tax_treatment_confirmed_by = request.user
            obj.tax_treatment_confirmed_at = now
            obj.save(update_fields=['tax_state', 'vat_registered', 'tax_treatment_confirmed_by', 'tax_treatment_confirmed_at', 'updated_at'])
            audit.record('restaurant.tax_activated', shop=branch, user=request.user, target=obj,
                         summary='Taxes activated: ' + ', '.join(str(r) for r in rules)
                                 + (' (overlap confirmed: ' + '; '.join(' + '.join(c) for c in clashes) + ')' if clashes else ''),
                         data={'rules': [str(r.id) for r in rules], 'state': state, 'vat_registered': data['vat_registered'],
                               'overlaps': [list(c) for c in clashes]})
            broadcast(branch, 'restaurant.config_changed', {'entity': 'tax_rule', 'verb': 'activated'})
        return Response({'activated': [str(r.id) for r in rules], 'overlap_confirmed': bool(clashes)})


class RoutingPreviewView(APIView):
    """POST /api/restaurant/routing/preview/ {"items": [{"menu_item_id": …, "quantity": 2}]}: which
    station gets what, the same rules the hub uses (restaurant.routing). Read-only; used by the
    settings screen to check the kitchen set-up before service."""
    permission_classes = [RestaurantAccess]

    def post(self, request):
        branch = resolve_branch(request)
        wanted = request.data.get('items') or []
        if not isinstance(wanted, list):
            return _error('Send a list of items.', 'invalid')
        menu = {str(m.id): m for m in MenuItem.objects.filter(shop=branch, is_deleted=False,
                                                              id__in=_uuids([i.get('menu_item_id') for i in wanted if isinstance(i, dict)]))
                .select_related('category__station', 'station')}
        stations = {str(s.id): s for s in KitchenStation.objects.filter(shop=branch, is_deleted=False)}
        active = [sid for sid, st in stations.items() if st.is_active]
        lines = []
        for i, line in enumerate(wanted):
            item = menu.get(str(line.get('menu_item_id'))) if isinstance(line, dict) else None
            if item is None:
                return _error(f'Item {i + 1} is not on this branch\'s menu.', 'unknown_menu_item', status.HTTP_404_NOT_FOUND)
            station_id = routing.station_for(str(item.station_id) if item.station_id else None,
                                             str(item.category.station_id) if item.category.station_id else None, active)
            lines.append({'id': str(i), 'station_id': station_id})
        result = routing.route(lines)
        names = [menu[str(line['menu_item_id'])].name for line in wanted]
        for ticket in result['tickets']:
            ticket['station_name'] = stations[ticket['station_id']].name
            ticket['item_names'] = [names[int(i)] for i in ticket['items']]
        result['unrouted_names'] = [names[int(i)] for i in result['unrouted']]
        return Response(result)
