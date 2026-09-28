from django.db import transaction
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied

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
        sub = get_or_create_subscription(self.request._resolved_organization)
        if sub.effective_status == 'trial':
            raise SubscriptionRequired()
        # organization_id is NEVER accepted from the client (see spec §2) —
        # it's always the authenticated CEO's own organization. Wrapped in
        # a transaction because a "create branch" request can also create
        # the branch manager's login account in the same call (see
        # BranchSerializer._sync_manager_login) -- either both rows land
        # or neither does.
        with transaction.atomic():
            branch = serializer.save(organization=self.request._resolved_organization)
        # Subscription lives on the ORGANIZATION now, not per-branch — a new
        # branch just becomes another "additional branch" the org's ONE
        # subscription already covers billing for (see subscriptions.models
        # .Subscription.total_price_ngn); nothing new to create here beyond
        # the org's subscription existing at all, which registration already
        # guaranteed.
        broadcast(branch, 'branch.created', {'id': str(branch.id), 'name': branch.name})

    def perform_update(self, serializer):
        with transaction.atomic():
            branch = serializer.save()
        broadcast(branch, 'branch.updated', {'id': str(branch.id), 'name': branch.name})

    def perform_destroy(self, instance):
        # Never hard-delete a branch — it's the anchor for every historical
        # sale/expense/customer that ever happened there (spec §10/§12).
        # "Delete" from the CEO's point of view means archive.
        instance.status = 'archived'
        instance.save(update_fields=['status', 'updated_at'])
        broadcast(instance, 'branch.updated', {'id': str(instance.id), 'status': 'archived'})
