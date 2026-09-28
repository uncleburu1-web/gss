import uuid

from rest_framework.exceptions import PermissionDenied

from .models import Organization, Shop

# Fixed, well-known ID for the ORIGINAL single-shop deployment (Everyday
# Wine Store) -- kept as a fallback below so that account, script, or test
# that predates real multi-tenancy keeps resolving to the same shop it
# always has. New shops (via /api/auth/register/ or branch creation) get a
# normal random UUID like any other Shop and are never confused with this
# one.
DEFAULT_SHOP_ID = uuid.UUID('00000000-0000-0000-0000-000000000001')


def get_default_shop():
    from .models import Organization
    org, _ = Organization.objects.get_or_create(
        id=DEFAULT_SHOP_ID,  # reusing the same well-known UUID for both — this legacy row is 1 org : 1 branch
        defaults={'name': 'Everyday Wine Store', 'owner': None},
    )
    shop, _ = Shop.objects.get_or_create(
        id=DEFAULT_SHOP_ID,
        defaults={'name': 'Everyday Wine Store', 'organization': org},
    )
    return shop


def get_organization_for_user(user):
    """None if `user` isn't a CEO (doesn't own an Organization)."""
    return Organization.objects.filter(owner=user).first()


def get_shops_for_user(user, branch_id=None):
    """The full set of branches `user` is allowed to see -- used for READ
    paths (list/dashboard/reports), where a CEO legitimately needs
    cross-branch aggregation, unlike a single-shop write (see
    get_shop_for_user below).

    - A CEO (owns an Organization) is checked FIRST, even though they also
      hold a legacy Worker record at their first branch (for is_owner()
      backward-compatibility) -- checking Worker before Organization here
      would silently lock every CEO to their first branch forever,
      ignoring X-Branch-ID entirely. A CEO sees every branch in their org
      by default ("All Branches"), or exactly one if `branch_id` names a
      branch that's actually theirs -- an unrecognized/foreign branch_id
      is simply ignored rather than trusted, falling back to "all".
    - A genuinely branch-scoped user (a Worker with no Organization of
      their own -- a seller, branch manager, or non-CEO branch owner)
      ALWAYS sees just their one branch, full stop -- `branch_id` is
      ignored for them. This is the actual isolation boundary the spec
      calls "must NOT work by changing an ID in the URL": their allowed
      set is never influenced by client input.
    - A Django superuser/staff account with neither (platform admin, or
      legacy tooling/tests) falls back to the original default shop.
    """
    org = get_organization_for_user(user)
    if org is not None:
        qs = Shop.objects.filter(organization=org)
        if branch_id:
            scoped = qs.filter(id=branch_id)
            if scoped.exists():
                return scoped
        return qs

    worker = getattr(user, 'worker', None)
    if worker is not None and worker.shop_id:
        return Shop.objects.filter(id=worker.shop_id)

    if user.is_superuser or user.is_staff:
        return Shop.objects.filter(id=get_default_shop().id)

    raise PermissionDenied('This account is not linked to any branch.')


def get_shop_for_user(user, branch_id=None):
    """Resolves exactly ONE branch -- for WRITE paths, where a new record
    (a sale, a product, ...) has to belong to a single, unambiguous
    branch. Built on get_shops_for_user, so the same isolation rules
    apply; the difference is just collapsing "allowed set" down to "the
    one to write into."

    - Branch-scoped users: trivially their one branch.
    - A CEO with exactly one branch: that one, automatically -- a
      single-branch business never has to think about branch selection
      at all, which matters since that's the common case.
    - A CEO with several branches: `branch_id` must name one of theirs
      (e.g. via the X-Branch-ID header the frontend's branch switcher
      sets) -- with none selected, there's no single correct branch to
      write into, so this raises rather than silently guessing one.
    """
    shops = get_shops_for_user(user, branch_id=branch_id)
    count = shops.count()
    if count == 1:
        return shops.first()
    if count == 0:
        raise PermissionDenied('This account is not linked to any branch.')
    raise PermissionDenied(
        'You manage multiple branches -- select one (X-Branch-ID) before creating or editing records.'
    )


def resolve_branch_for_device_login(user, branch_id=None):
    """Which single branch a DESKTOP login is "for" -- used only by the
    device-pairing check in core.auth_serializers, never by the web app
    (which uses get_shop_for_user/X-Branch-ID for each request instead of
    pinning a whole physical machine to one branch).

    - A branch-scoped Worker (seller, branch manager, non-CEO owner):
      trivially their one branch, same as get_shop_for_user.
    - A CEO: their home branch (the Worker(role='owner') row registration
      always creates on their FIRST branch) by default -- covers the
      overwhelmingly common "I run one shop" case with zero extra steps.
      If they're setting a device up for a DIFFERENT branch, the desktop
      login screen can send `branch_id` (must be one of their own) to
      target that branch instead.
    - Returns None if the account isn't linked to any branch at all
      (shouldn't normally happen -- registration always creates one).
    """
    org = get_organization_for_user(user)
    if org is not None:
        if branch_id:
            shop = org.branches.filter(id=branch_id).first()
            if shop is not None:
                return shop
        worker = getattr(user, 'worker', None)
        if worker is not None and worker.shop_id and worker.shop.organization_id == org.id:
            return worker.shop
        return org.branches.order_by('name').first()

    worker = getattr(user, 'worker', None)
    return worker.shop if worker is not None else None


def user_may_use_branch_device(user, device_shop):
    """True if `user` is allowed to log in on a desktop already paired to
    `device_shop` -- either they work AT that exact branch, or they're
    the CEO of the organization that branch belongs to (a CEO can walk
    into any of their own branches and use that till; a worker can only
    ever use the one till they were hired for).
    """
    org = get_organization_for_user(user)
    if org is not None and device_shop.organization_id == org.id:
        return True
    worker = getattr(user, 'worker', None)
    return bool(worker is not None and worker.shop_id == device_shop.id)
