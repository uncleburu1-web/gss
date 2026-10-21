"""Audit trail for sensitive actions. Use audit.record(...) right next to the action."""


def record(action, *, shop=None, organization=None, user=None, target=None, target_type='', target_id='',
           summary='', reason='', data=None, source='web', device=None, actor_name=None):
    """Write one AuditLog line. Never raises into the caller's business logic for bad metadata."""
    from inventory.ledger import actor_name_for
    from .models import AuditLog

    from core.utils import json_safe
    data = json_safe(data or {})  # Decimal/UUID/dates -> JSON-safe
    if target is not None:
        target_type = target_type or target._meta.model_name
        target_id = target_id or str(target.pk)
    if organization is None and shop is not None:
        organization = getattr(shop, 'organization', None)
    return AuditLog.objects.create(
        action=action[:60],
        shop=shop,
        organization=organization,
        user=user if getattr(user, 'is_authenticated', False) else None,
        actor_name=(actor_name if actor_name is not None else actor_name_for(user if getattr(user, 'is_authenticated', False) else None))[:150],
        target_type=str(target_type)[:40],
        target_id=str(target_id or '')[:64],
        summary=(summary or '')[:255],
        reason=(reason or '')[:255],
        data=data or {},
        source=source,
        device=device,
    )


def changes(before, after, fields):
    """{'field': [before, after]} for the fields that actually changed (values as strings)."""
    out = {}
    for f in fields:
        a, b = before.get(f), after.get(f)
        if str(a) != str(b):
            out[f] = [None if a is None else str(a), None if b is None else str(b)]
    return out
