from django.contrib.auth.models import User
from rest_framework import serializers
from core.permissions import is_ceo
from .models import Worker, AttendanceRecord


class WorkerSerializer(serializers.ModelSerializer):
    can_login = serializers.ReadOnlyField()
    username = serializers.SerializerMethodField()

    # Write-only, used only when creating/updating a login-enabled worker.
    login_username = serializers.CharField(write_only=True, required=False, allow_blank=True)
    login_password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Worker
        fields = [
            'id', 'full_name', 'role', 'phone', 'salary', 'hire_date', 'is_active',
            'notes', 'can_login', 'username', 'login_username', 'login_password', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']

    def get_username(self, obj):
        return obj.user.username if obj.user else None

    def validate_role(self, role):
        # This endpoint is reached by anyone is_owner() lets through — an
        # owner AND a branch manager (see core.permissions.is_owner) — so
        # without this, a branch manager could hand themselves or a peer
        # 'owner'/'branch_manager' via a plain PATCH, even though the UI
        # never offers either option. 'owner' is never created here at
        # all (only at signup); 'branch_manager' stays reserved for the
        # dedicated flow (Settings -> Branches / branches.serializers
        # .BranchSerializer), which only the org's actual CEO can use.
        if role == 'owner':
            raise serializers.ValidationError('An owner account is created at signup, not from Workers.')
        if role == 'branch_manager':
            request = self.context.get('request')
            if not (request and is_ceo(request.user)):
                raise serializers.ValidationError(
                    'Only the organization owner can assign a branch manager — do this from Settings \u2192 Branches.'
                )
        return role

    def validate(self, attrs):
        username = attrs.get('login_username')
        password = attrs.get('login_password')
        if username and not password and not self.instance:
            raise serializers.ValidationError('Set a password for this worker\'s login.')
        if username:
            qs = User.objects.filter(username=username)
            if self.instance and self.instance.user_id:
                qs = qs.exclude(id=self.instance.user_id)
            if qs.exists():
                raise serializers.ValidationError({'login_username': 'That username is already taken.'})
        return attrs

    def create(self, validated_data):
        username = validated_data.pop('login_username', '')
        password = validated_data.pop('login_password', '')
        worker = Worker.objects.create(**validated_data)
        if username and password:
            user = User.objects.create_user(username=username, password=password)
            worker.user = user
            worker.save(update_fields=['user'])
        return worker

    def update(self, instance, validated_data):
        username = validated_data.pop('login_username', '')
        password = validated_data.pop('login_password', '')

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if username and password and not instance.user_id:
            user = User.objects.create_user(username=username, password=password)
            instance.user = user
            instance.save(update_fields=['user'])
        elif instance.user_id and password:
            instance.user.set_password(password)
            instance.user.save(update_fields=['password'])

        if instance.user_id:
            # Keep the login account's active state in sync with the worker record.
            if instance.user.is_active != instance.is_active:
                instance.user.is_active = instance.is_active
                instance.user.save(update_fields=['is_active'])

        return instance


class AttendanceRecordSerializer(serializers.ModelSerializer):
    worker_name = serializers.CharField(source='worker.full_name', read_only=True)
    worker_role = serializers.CharField(source='worker.role', read_only=True)
    marked_by_name = serializers.CharField(source='marked_by.full_name', read_only=True, default=None)

    class Meta:
        model = AttendanceRecord
        fields = [
            'id', 'worker', 'worker_name', 'worker_role', 'date', 'status',
            'check_in_time', 'check_out_time', 'marked_by_name', 'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_worker(self, worker):
        # Never let a request name a worker from a DIFFERENT branch — a
        # raw API call could otherwise POST any worker id at all, and
        # marking someone else's shop's attendance would slip through
        # silently since AttendanceRecord's own `shop` is set separately
        # by the view (see AttendanceRecordViewSet.perform_create).
        shop = self.context.get('shop')
        if shop is not None and worker.shop_id != shop.id:
            raise serializers.ValidationError('That worker is not part of this branch.')
        return worker
