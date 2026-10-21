"""Who may use the restaurant API (docs/restaurant/API_SPECIFICATION.md, conventions).

  * The branch is resolved exactly like every other write in Benchline (core.utils.get_shop_for_user):
    a branch user is pinned to their branch whatever they send; a CEO must name one of their own
    branches with X-Branch-ID. No client input can reach another business's branch.
  * Reading needs the restaurant module switched on for that branch (403 module_disabled).
  * Writing also needs an owner / branch manager / CEO (core.permissions.is_owner) and a package that
    includes restaurant features (403 not_in_package).
"""
from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.permissions import SAFE_METHODS, BasePermission

from core import modules
from core.permissions import is_owner
from core.utils import get_shop_for_user

RESTAURANT_FEATURE = 'restaurant.tables'


class CodedDenied(APIException):
    status_code = status.HTTP_403_FORBIDDEN

    def __init__(self, message, code):
        super().__init__(detail={'detail': message, 'code': code})


class ModuleDisabled(CodedDenied):
    def __init__(self):
        super().__init__('This branch does not use the restaurant module. Switch it on in Settings → Branches.', 'module_disabled')


class NotInPackage(CodedDenied):
    def __init__(self):
        super().__init__("Your business's package does not include restaurant features.", 'not_in_package')


def resolve_branch(request):
    """The one branch this request acts on (cached on the request)."""
    if not hasattr(request, '_restaurant_branch'):
        request._restaurant_branch = get_shop_for_user(request.user, branch_id=request.headers.get('X-Branch-ID') or None)
    return request._restaurant_branch


class RestaurantAccess(BasePermission):
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        branch = resolve_branch(request)
        if not modules.has_module(branch, modules.RESTAURANT):
            raise ModuleDisabled()
        if request.method in SAFE_METHODS:
            return True
        if not is_owner(request.user):
            return False
        if not modules.has_feature(branch.organization, RESTAURANT_FEATURE):
            raise NotInPackage()
        return True
