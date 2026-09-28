from django.contrib.auth.models import User
from rest_framework import serializers

from core.models import Shop
from staff.models import Worker


class BranchSerializer(serializers.ModelSerializer):
    manager = serializers.PrimaryKeyRelatedField(queryset=Worker.objects.all(), required=False, allow_null=True)
    manager_name = serializers.CharField(source='manager.full_name', read_only=True, default=None)
    manager_username = serializers.SerializerMethodField()
    is_operational = serializers.ReadOnlyField()

    # Write-only -- stand up (or reset the login for) this branch's own
    # manager in the SAME request as the branch, the same way RegisterView
    # creates an owner's login alongside their first branch. Mutually
    # exclusive with `manager` (an existing worker): either point at a
    # worker who already exists, or fill these in to create a brand-new
    # branch manager. See _sync_manager_login for the actual create/reset
    # logic, which mirrors staff.serializers.WorkerSerializer.
    manager_login_full_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    manager_login_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    manager_login_password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Shop
        fields = [
            'id', 'name', 'branch_code', 'address', 'phone', 'email', 'logo_url', 'receipt_footer_note',
            'manager', 'manager_name', 'manager_username', 'status', 'is_operational', 'opening_date',
            'timezone', 'currency', 'tax_rate_default', 'description', 'created_at', 'updated_at',
            'manager_login_full_name', 'manager_login_username', 'manager_login_password',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_manager_username(self, obj):
        return obj.manager.user.username if obj.manager_id and obj.manager.user_id else None

    def validate(self, attrs):
        # The DB-level UniqueConstraint (organization, branch_code) is the
        # real guarantee, but hitting it directly means an IntegrityError
        # crashes as a raw 500 instead of a clean validation message —
        # catch it here first so a duplicate code is just an ordinary 400.
        code = attrs.get('branch_code', '').strip()
        if code:
            request = self.context.get('request')
            org = getattr(request, '_resolved_organization', None)
            if org is not None:
                qs = org.branches.filter(branch_code=code)
                if self.instance is not None:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError(
                        {'branch_code': f'"{code}" is already used by another branch in this organization.'}
                    )

        full_name = attrs.get('manager_login_full_name')
        username = attrs.get('manager_login_username')
        password = attrs.get('manager_login_password')
        existing_manager = self.instance.manager if self.instance else None

        if attrs.get('manager') and (full_name or username or password):
            raise serializers.ValidationError(
                'Pick an existing worker as this branch\'s manager, or set up a new manager login below — not both.'
            )

        already_has_login = bool(existing_manager and existing_manager.user_id)

        if username:
            qs = User.objects.filter(username=username)
            if already_has_login:
                qs = qs.exclude(id=existing_manager.user_id)
            if qs.exists():
                raise serializers.ValidationError({'manager_login_username': 'That username is already taken.'})
            if not password and not already_has_login:
                raise serializers.ValidationError(
                    {'manager_login_password': 'Set a password for the branch manager\'s login.'}
                )
        elif password and not already_has_login:
            raise serializers.ValidationError(
                {'manager_login_username': 'Set a username for the branch manager\'s login.'}
            )

        return attrs

    def validate_manager(self, worker):
        # A branch's manager must be a worker somewhere in the SAME
        # organization -- never let one CEO assign another org's staff
        # member (or vice versa) as a manager just by guessing a Worker ID.
        request = self.context.get('request')
        org = getattr(request, '_resolved_organization', None)
        if worker is not None and org is not None and (worker.shop_id is None or worker.shop.organization_id != org.id):
            raise serializers.ValidationError('That worker is not part of this organization.')
        return worker

    def _sync_manager_login(self, branch, full_name, username, password):
        """Create this branch's manager (if there isn't one yet), give an
        existing manager their first login, or reset an existing manager's
        password -- whichever applies. No-ops if neither a username nor a
        password was actually submitted."""
        if not username and not password:
            return

        manager = branch.manager
        if manager is None:
            user = User.objects.create_user(username=username, password=password)
            branch.manager = Worker.objects.create(
                shop=branch, user=user, full_name=full_name or username, role='branch_manager',
            )
            branch.save(update_fields=['manager'])
        elif manager.user_id is None:
            user = User.objects.create_user(username=username, password=password)
            manager.user = user
            if full_name:
                manager.full_name = full_name
            manager.save(update_fields=['user', 'full_name'] if full_name else ['user'])
        else:
            # Already has a login -- changing the username of an existing
            # account isn't supported here (that's what they already know
            # to log in with); this path only ever resets the password.
            if password:
                manager.user.set_password(password)
                manager.user.save(update_fields=['password'])
            if full_name:
                manager.full_name = full_name
                manager.save(update_fields=['full_name'])

    def create(self, validated_data):
        full_name = validated_data.pop('manager_login_full_name', '')
        username = validated_data.pop('manager_login_username', '')
        password = validated_data.pop('manager_login_password', '')
        branch = Shop.objects.create(**validated_data)
        self._sync_manager_login(branch, full_name, username, password)
        return branch

    def update(self, instance, validated_data):
        full_name = validated_data.pop('manager_login_full_name', '')
        username = validated_data.pop('manager_login_username', '')
        password = validated_data.pop('manager_login_password', '')

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        self._sync_manager_login(instance, full_name, username, password)
        return instance
