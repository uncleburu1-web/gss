"""Who may see which sales.

Owners, branch managers, the CEO, and any role the owner grants 'view_all_sales' in the Control
Center see every seller's sales. Everyone else sees only the sales they made themselves: the ones
recorded against their worker record, plus older rows with no worker that carry their name.
"""
from django.db.models import Q

from core.capabilities import has_capability


def sees_all_sales(user):
    return has_capability(user, 'view_all_sales')


def own_sales_q(user, prefix=''):
    worker = getattr(user, 'worker', None)
    names = {n for n in (getattr(worker, 'full_name', ''), getattr(user, 'username', '')) if n}
    q = Q(**{f'{prefix}worker__user': user})
    if names:
        q |= Q(**{f'{prefix}worker__isnull': True, f'{prefix}staff_name__in': names})
    return q


def visible_sales(qs, user, prefix=''):
    """`qs` narrowed to what `user` may see. `prefix` for querysets reached through a relation
    (e.g. 'sale__' on SaleItem)."""
    return qs if sees_all_sales(user) else qs.filter(own_sales_q(user, prefix))
