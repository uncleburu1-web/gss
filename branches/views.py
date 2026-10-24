import uuid

from django.db import transaction
from rest_framework import viewsets
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import audit
from core import modules
from core.models import BranchModule, Shop
from core.permissions import is_ceo
from core.utils import get_shops_for_user

from core.permissions import IsCeo
from core.utils import get_organization_for_user
from realtime.events import broadcast
from subscriptions.models import get_or_create_subscription
from .serializers import BranchSerializer


class SubscriptionRequired(PermissionDenied):
    """A distinct exception (rather than a bare PermissionDenied) purely
    for readability at the raise site below — the frontend doesn't need to
    parse this apart from any other 403, because it already checks
    subscription status itself and routes to billing before ever calling
    this endpoint (see BranchesSection.handleCreateClick on both web and
    mobile). This is the server-side backstop for someone hitting the API
    directly, not the primary UX path."""
    default_detail = 'Adding another branch needs an active plan — the first branch is included in your trial.'


class NotInPackage(PermissionDenied):
    """403 {"detail", "code": "not_in_package"}: the branch type needs a module the business's package
    does not include (a restaurant branch on a retail-only package)."""

    def __init__(self, shop_type):
        label = dict(Shop._meta.get_field('shop_type').choices).get(shop_type, shop_type)
        super().__init__({'detail': f"Your business's package does not include what a {label.split(' (')[0].lower()} "
                                    "branch needs. Change the plan under Billing first.",
                          'code': 'not_in_package'})


class BranchViewSet(viewsets.ModelViewSet):
    """CEO-only: create/list/update/archive branches under their own
    organization. NOT for regular business data (sales, products, ...) --
    those stay on ShopScopedMixin/get_shops_for_user. This view IS the
    boundary that decides which branches exist in the first place.

    Deliberately does not support hard delete -- see perform_destroy.
    """
    serializer_class = BranchSerializer
    permission_classes = [IsCeo]

    def initial(self, request, *args, **kwargs):
        # Resolve the organization BEFORE anything else runs, including
        # serializer validation -- get_serializer_context() and validate()
        # both need request._resolved_organization to already be set, or
        # e.g. the duplicate-branch_code check in BranchSerializer.validate
        # silently no-ops and a bad request falls through to a raw DB
        # IntegrityError instead of a clean 400 (caught exactly that way
        # while testing this).
        super().initial(request, *args, **kwargs)
        request._resolved_organization = self.get_organization()

    def get_organization(self):
        org = get_organization_for_user(self.request.user)
        if org is None:
            raise PermissionDenied('This account does not own an organization.')
        return org

    def get_queryset(self):
        return self.request._resolved_organization.branches.all().order_by('name')

    def perform_create(self, serializer):
        # Trial orgs get their one signup branch free; adding another is a
        # paid-plan feature (every branch past the first is a billed
        # add-on — see subscriptions.models.Subscription.total_price_ngn).
        # The frontend already routes the "Create branch" button to the
        # billing page first when it can see the org is on trial; this is
        # the same rule enforced server-side, since a client-side check
        # alone is just a UI nicety, not security.
        org = self.request._resolved_organization
        sub = get_or_create_subscription(org)
        if sub.effective_status == 'trial':
            raise SubscriptionRequired()
        # Each branch is its own kind of shop (owner's decision 13); not named, it is the sign-up type.
        shop_type = serializer.validated_data.get('shop_type') or org.business_type
        if modules.missing_features_for_type(org, shop_type):
            raise NotInPackage(shop_type)
        # organization_id is NEVER accepted from the client (see spec §2) —
        # it's always the authenticated CEO's own organization. Wrapped in
        # a transaction because a "create branch" request can also create
        # the branch manager's login account in the same call (see
        # BranchSerializer._sync_manager_login) -- either both rows land
        # or neither does.
        with transaction.atomic():
            branch = serializer.save(organization=org, shop_type=shop_type)
        # Subscription lives on the ORGANIZATION now, not per-branch — a new
        # branch just becomes another "additional branch" the org's ONE
        # subscription already covers billing for (see subscriptions.models
        # .Subscription.total_price_ngn); nothing new to create here beyond
        # the org's subscription existing at all, which registration already
        # guaranteed.
        broadcast(branch, 'branch.created', {'id': str(branch.id), 'name': branch.name})

    def perform_update(self, serializer):
        old_type = serializer.instance.shop_type
        new_type = serializer.validated_data.get('shop_type') or old_type
        if new_type != old_type:
            # Only the modules the new type adds need the package; switching off is always allowed.
            on_now = modules.branch_modules(serializer.instance)
            if [m for m in modules.missing_features_for_type(serializer.instance.organization, new_type) if m not in on_now]:
                raise NotInPackage(new_type)
        with transaction.atomic():
            branch = serializer.save()
            changes = {}
            if new_type != old_type:
                changes = modules.apply_type_modules(branch, self.request.user)
                labels = dict(Shop._meta.get_field('shop_type').choices)
                audit.record('branch.type_changed', shop=branch, user=self.request.user, target=branch,
                             summary=f'{labels.get(old_type, old_type)} → {labels.get(new_type, new_type)}',
                             data={'from': old_type, 'to': new_type, 'modules': changes})
        broadcast(branch, 'branch.updated', {'id': str(branch.id), 'name': branch.name})
        if changes:
            broadcast(branch, 'branch.modules_changed', {'id': str(branch.id), 'changes': changes})

    def perform_destroy(self, instance):
        # Never hard-delete a branch — it's the anchor for every historical
        # sale/expense/customer that ever happened there (spec §10/§12).
        # "Delete" from the CEO's point of view means archive.
        instance.status = 'archived'
        instance.save(update_fields=['status', 'updated_at'])
        broadcast(instance, 'branch.updated', {'id': str(instance.id), 'status': 'archived'})


class BranchModulesView(APIView):
    """GET / PUT /api/branches/{id}/modules/ — which modules a branch uses (retail, restaurant,
    service). The CEO, or an owner / branch manager OF THAT BRANCH, may change them. Switching one
    on needs the business's package to include it (403 not_in_package); switching off is always
    allowed. Every change is audited. See docs/architecture/TARGET_ARCHITECTURE.md §1."""
    permission_classes = [IsAuthenticated]

    def _branch(self, request, pk):
        try:
            shop = get_shops_for_user(request.user).filter(id=uuid.UUID(str(pk))).first()
        except ValueError:
            shop = None
        if shop is None:
            raise NotFound('Branch not found.')
        return shop

    @staticmethod
    def _may_change(user, shop):
        if is_ceo(user) and shop.organization.owner_id == user.id:
            return True
        worker = getattr(user, 'worker', None)
        return bool(worker and worker.shop_id == shop.id and worker.role in ('owner', 'branch_manager'))

    @staticmethod
    def _state(shop):
        on = modules.branch_modules(shop)
        return {'branch_id': str(shop.id), 'modules': {m: (m in on) for m in modules.MODULES},
                'available': {m: modules.can_enable(shop, m) for m in modules.MODULES}}

    def get(self, request, pk):
        return Response(self._state(self._branch(request, pk)))

    def put(self, request, pk):
        shop = self._branch(request, pk)
        if not self._may_change(request.user, shop):
            raise PermissionDenied('Only the CEO or an owner or manager of this branch can change its modules.')
        wanted = request.data.get('modules')
        if not isinstance(wanted, dict) or any(k not in modules.MODULES or not isinstance(v, bool) for k, v in wanted.items()):
            return Response({'detail': f'Send {{"modules": {{"<module>": true/false}}}} with modules from {", ".join(modules.MODULES)}.',
                             'code': 'invalid'}, status=400)
        current = modules.branch_modules(shop)
        changes = {m: v for m, v in wanted.items() if v != (m in current)}
        for module, on in changes.items():
            if on and not modules.can_enable(shop, module):
                return Response({'detail': f"Your business's package does not include {module}.", 'code': 'not_in_package'}, status=403)
        with transaction.atomic():
            for module, on in changes.items():
                row, _ = BranchModule.objects.get_or_create(shop=shop, module=module, defaults={'enabled': on, 'changed_by': request.user})
                row.enabled, row.changed_by = on, request.user
                row.save(update_fields=['enabled', 'changed_by', 'changed_at'])
            if changes:
                audit.record('branch.modules_changed', shop=shop, user=request.user, target=shop,
                             summary=', '.join(f'{m} {"on" if v else "off"}' for m, v in changes.items()),
                             data={'changes': changes})
                broadcast(shop, 'branch.modules_changed', {'id': str(shop.id), 'changes': changes})
        return Response(self._state(shop))
