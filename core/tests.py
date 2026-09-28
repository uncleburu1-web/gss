from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from staff.models import Worker
from .capabilities import has_capability, capabilities_for_user, CONFIGURABLE_ROLES, CAPABILITIES
from .models import Organization, Shop, RolePermission


def make_worker(username, role, org=None, is_org_owner=False):
    """A worker of any role. `is_org_owner=True` makes them the
    Organization's own `owner` FK (is_ceo() True) -- needed for the
    Control Center's CEO-only tests, distinct from just Worker(role='owner'),
    which only grants branch-local authority (is_owner())."""
    user = User.objects.create_user(username=username, password='x')
    if org is None:
        org = Organization.objects.create(
            name=f'{username} Shop', owner=user if is_org_owner else None, business_type='general',
        )
    elif is_org_owner:
        org.owner = user
        org.save(update_fields=['owner'])
    shop = Shop.objects.filter(organization=org).first() or Shop.objects.create(name=org.name, organization=org)
    Worker.objects.create(user=user, full_name=username, role=role, shop=shop)
    return user, org, shop


class HasCapabilityTests(TestCase):
    def setUp(self):
        self.owner, self.org, self.shop = make_worker('owner1', 'owner', is_org_owner=True)
        self.manager, _, _ = make_worker('manager1', 'branch_manager', org=self.org)
        self.seller, _, _ = make_worker('seller1', 'seller', org=self.org)
        self.reception, _, _ = make_worker('reception1', 'reception', org=self.org)

    def test_owner_branch_manager_and_ceo_bypass_everything(self):
        for user in (self.owner, self.manager):
            for cap_id in CAPABILITIES:
                self.assertTrue(has_capability(user, cap_id), f'{user.username} should always have {cap_id}')

    def test_seller_lacks_delete_and_edit_sale_by_default(self):
        self.assertFalse(has_capability(self.seller, 'delete_sale'))
        self.assertFalse(has_capability(self.seller, 'edit_sale'))

    def test_reception_has_attendance_capabilities_by_default(self):
        self.assertTrue(has_capability(self.reception, 'mark_attendance'))
        self.assertTrue(has_capability(self.reception, 'view_attendance'))

    def test_seller_lacks_attendance_capabilities_by_default(self):
        self.assertFalse(has_capability(self.seller, 'mark_attendance'))
        self.assertFalse(has_capability(self.seller, 'view_attendance'))

    def test_override_grants_capability(self):
        RolePermission.objects.create(organization=self.org, role='seller', capability='delete_sale', allowed=True)
        self.assertTrue(has_capability(self.seller, 'delete_sale'))

    def test_override_revokes_default_capability(self):
        RolePermission.objects.create(organization=self.org, role='reception', capability='mark_attendance', allowed=False)
        self.assertFalse(has_capability(self.reception, 'mark_attendance'))

    def test_inactive_worker_has_no_capability_even_if_role_would(self):
        self.reception.worker.is_active = False
        self.reception.worker.save(update_fields=['is_active'])
        self.assertFalse(has_capability(self.reception, 'mark_attendance'))

    def test_unauthenticated_has_no_capability(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(has_capability(AnonymousUser(), 'mark_attendance'))

    def test_capabilities_for_user_shape(self):
        caps = capabilities_for_user(self.seller)
        self.assertEqual(set(caps.keys()), set(CAPABILITIES.keys()))
        self.assertFalse(caps['delete_sale'])
        caps_owner = capabilities_for_user(self.owner)
        self.assertTrue(all(caps_owner.values()))


class ControlCenterViewTests(TestCase):
    def setUp(self):
        self.owner, self.org, self.shop = make_worker('ceo1', 'owner', is_org_owner=True)
        self.manager, _, _ = make_worker('manager2', 'branch_manager', org=self.org)
        self.seller, _, _ = make_worker('seller2', 'seller', org=self.org)

    def test_ceo_can_view(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.get('/api/control-center/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(set(res.data['roles']), set(CONFIGURABLE_ROLES))
        delete_sale = next(c for c in res.data['capabilities'] if c['id'] == 'delete_sale')
        self.assertFalse(delete_sale['roles']['seller'])

    def test_branch_manager_cannot_view_or_edit(self):
        # Org-wide policy stays with the org's actual owner/CEO -- a
        # branch manager's authority is deliberately scoped to their own
        # branch's day-to-day data, not to changing what a role can do
        # everywhere.
        client = APIClient()
        client.force_authenticate(user=self.manager)
        self.assertEqual(client.get('/api/control-center/').status_code, 403)
        self.assertEqual(client.put('/api/control-center/', {'changes': []}, format='json').status_code, 403)

    def test_seller_cannot_view_or_edit(self):
        client = APIClient()
        client.force_authenticate(user=self.seller)
        self.assertEqual(client.get('/api/control-center/').status_code, 403)

    def test_ceo_can_toggle_seller_delete_sale_on(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.put('/api/control-center/', {
            'changes': [{'role': 'seller', 'capability': 'delete_sale', 'allowed': True}],
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(RolePermission.objects.get(organization=self.org, role='seller', capability='delete_sale').allowed)
        delete_sale = next(c for c in res.data['capabilities'] if c['id'] == 'delete_sale')
        self.assertTrue(delete_sale['roles']['seller'])

    def test_rejects_unknown_role_or_capability(self):
        client = APIClient()
        client.force_authenticate(user=self.owner)
        res = client.put('/api/control-center/', {
            'changes': [{'role': 'owner', 'capability': 'delete_sale', 'allowed': True}],
        }, format='json')
        self.assertEqual(res.status_code, 400)
        res = client.put('/api/control-center/', {
            'changes': [{'role': 'seller', 'capability': 'nonexistent', 'allowed': True}],
        }, format='json')
        self.assertEqual(res.status_code, 400)
