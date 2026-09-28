from rest_framework.permissions import BasePermission, SAFE_METHODS


def is_owner(user):
    """Full access WITHIN the current branch context: a Django
    superuser/staff account, a Worker explicitly given the 'owner' or
    'branch_manager' role, or a CEO (owns the Organization) acting on
    their own org's branch(es). This is branch/org-scoped authority, not
    "can do literally anything" -- see is_ceo for the narrower
    org-wide-only capabilities (create branch, manage billing)."""
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    worker = getattr(user, 'worker', None)
    if worker and worker.role in ('owner', 'branch_manager'):
        return True
    return is_ceo(user)


def is_ceo(user):
    """Organization-wide authority: create/edit/suspend branches, move
    staff between branches, manage the org's subscription/enterprise
    plan, see every branch's data. True only for the User set as
    Organization.owner -- NOT the same as a branch-level Worker(role=
    'owner'), which only ever sees its own single branch."""
    if not user or not user.is_authenticated:
        return False
    from .models import Organization
    return Organization.objects.filter(owner=user).exists()


class IsOwner(BasePermission):
    """Full access only for the shop owner/admin — used for Workers,
    Liabilities, and Reports."""

    def has_permission(self, request, view):
        return is_owner(request.user)


class IsOwnerOrReadOnly(BasePermission):
    """Anyone signed in (owner or seller) can read; only the owner can
    write. Used for Inventory items and batches."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return is_owner(request.user)


class IsCeo(BasePermission):
    """Organization-wide-only actions — creating/managing branches,
    enterprise billing. See is_ceo's docstring for the distinction from
    IsOwner."""

    def has_permission(self, request, view):
        return is_ceo(request.user)


def HasCapability(capability):
    """Factory, not a class — use as `HasCapability('delete_sale')` inside
    a `permission_classes`/`get_permissions()` list, same call shape DRF
    expects it to instantiate on its own. Needed (instead of a single
    reusable class) because which capability id to check varies per
    action -- see sales.views.SaleViewSet.get_permissions and
    staff.views.AttendanceRecordViewSet for the two call sites.

    Delegates entirely to core.capabilities.has_capability, which already
    grants owner/branch_manager/CEO everything unconditionally -- this is
    only ever the *deciding* check for the configurable staff roles.
    """
    class _HasCapability(BasePermission):
        def has_permission(self, request, view):
            from .capabilities import has_capability
            return has_capability(request.user, capability)

    return _HasCapability
