"""
The Control Center's data model: a small, named set of CAPABILITIES an
owner/CEO can grant to or withhold from each *configurable* staff role,
plus has_capability() — the one function everything else in the backend
calls to ask "can this person do X right now?"

Deliberately NOT used for owner / branch_manager / CEO: those roles keep
their existing unconditional access via core.permissions.is_owner() /
is_ceo() exactly as before this module existed (see has_capability's
short-circuit below). This registry only ever governs the roles a shop
actually hires day-to-day — seller, technician, attendant, reception,
other — which is why CONFIGURABLE_ROLES deliberately excludes 'owner'
and 'branch_manager': granting or revoking capabilities for either of
those through this system would be a confusing second way to change
something that already has a clear, direct one (Workers / Settings ->
Branches).

Adding a new capability later is a one-entry addition to CAPABILITIES
below -- nothing else needs to change to make it toggleable from the
Control Center.
"""
from staff.models import Worker

CONFIGURABLE_ROLES = [r for r, _ in Worker.ROLE_CHOICES if r not in ('owner', 'branch_manager')]

# id -> {label, description, default_roles}. `default_roles` is which of
# CONFIGURABLE_ROLES get this capability out of the box, before an
# owner/CEO has touched the Control Center at all -- see RolePermission
# for how an explicit toggle overrides this.
CAPABILITIES = {
    'delete_sale': {
        'label': 'Delete a sale',
        'description': (
            'Permanently void a completed or outstanding sale and restore its stock. '
            'Off by default — most shops only want an owner or branch manager reversing a sale.'
        ),
        'default_roles': set(),
    },
    'edit_sale': {
        'label': 'Edit a sale after checkout',
        'description': (
            'Swap an item already rung up on a sale for a different one. Off by default, '
            'for the same reason as deleting a sale.'
        ),
        'default_roles': set(),
    },
    'mark_attendance': {
        'label': 'Mark staff attendance',
        'description': 'Record who is present, late, absent, or on leave today.',
        'default_roles': {'reception'},
    },
    'view_attendance': {
        'label': 'View everyone\u2019s attendance',
        'description': (
            'See the attendance history for every worker at this branch, not just their own.'
        ),
        'default_roles': {'reception'},
    },
}


def capability_choices():
    return list(CAPABILITIES.keys())


def _default_allowed(role, capability_id):
    spec = CAPABILITIES.get(capability_id)
    return bool(spec and role in spec['default_roles'])


def has_capability(user, capability_id):
    """True if `user` may use `capability_id` right now.

    - Not signed in -> False.
    - Owner / branch manager / platform staff / the org's CEO -> True,
      always -- see core.permissions.is_owner, which already encodes
      exactly this "full access within the current branch" rule.
    - Anyone else (a Worker on one of CONFIGURABLE_ROLES) -> whatever the
      owner/CEO has set for their role in the Control Center
      (RolePermission), falling back to the capability's built-in
      default if nothing's been explicitly configured. An inactive
      worker (is_active=False) never gets a capability, login or not.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False

    from .permissions import is_owner  # local import: permissions imports this module too

    if is_owner(user):
        return True

    worker = getattr(user, 'worker', None)
    if worker is None or not worker.is_active:
        return False

    if capability_id not in CAPABILITIES:
        return False

    from .models import RolePermission  # local import: avoids a models<->capabilities import cycle

    org = worker.shop.organization if worker.shop_id else None
    if org is not None:
        override = RolePermission.objects.filter(
            organization=org, role=worker.role, capability=capability_id,
        ).first()
        if override is not None:
            return override.allowed

    return _default_allowed(worker.role, capability_id)


def capabilities_for_user(user):
    """{capability_id: bool, ...} for every registered capability -- what
    core.views.MeView hands the frontend so it can show/hide buttons
    (Delete sale, Mark attendance, ...) without re-deriving this logic
    client-side. Always all-True for owner/branch_manager/CEO, same
    short-circuit as has_capability."""
    return {cap_id: has_capability(user, cap_id) for cap_id in CAPABILITIES}


def effective_role_permissions(organization):
    """The full role x capability grid for the Control Center screen:
    [{id, label, description, roles: {role: bool}}, ...], one entry per
    capability, covering every CONFIGURABLE_ROLES -- this IS the payload
    ControlCenterView.get returns."""
    from .models import RolePermission

    overrides = {
        (rp.role, rp.capability): rp.allowed
        for rp in RolePermission.objects.filter(organization=organization)
    }
    grid = []
    for cap_id, spec in CAPABILITIES.items():
        roles = {}
        for role in CONFIGURABLE_ROLES:
            roles[role] = overrides.get((role, cap_id), _default_allowed(role, cap_id))
        grid.append({
            'id': cap_id, 'label': spec['label'], 'description': spec['description'], 'roles': roles,
        })
    return grid
