"""Device-aware login — the desktop POS's `/api/auth/login/` calls carry
two extra, optional fields the web app and Android app never send:
`device_id` (the UUID desktop/src/main/heartbeat.js generates once, on
first launch, and reuses forever) and `device_type` ('desktop').

The rule this enforces: a desktop till, once ANY account has logged into
it, is permanently paired to that account's branch — the FIRST successful
login for a given device_id is what pairs it (see Device.objects.
get_or_create below); every login after that, from that same physical
machine, is only allowed if the account belongs to the branch it's
already paired to (a branch Worker of that exact branch, or the CEO who
owns it — see core.utils.user_may_use_branch_device). Credentials that
are valid but for a DIFFERENT shop are rejected here even though the
username/password themselves check out fine — this is what stops a
worker's (or a competitor shop's) login from ever pulling a different
shop's catalog, prices, and sales history onto a till they don't belong
to, and it's enforced server-side so clearing local storage can't bypass
it.

Web and Android logins never send device_id, so none of this runs for
them — they're unaffected, exactly as before.
"""
from django.contrib.auth import get_user_model
from rest_framework import exceptions, serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from devices.models import Device
from .utils import resolve_branch_for_device_login, user_may_use_branch_device


class DeviceAwareTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        try:
            data = super().validate(attrs)  # raises AuthenticationFailed first if username/password are wrong
        except exceptions.AuthenticationFailed:
            # Distinguish "wrong username/password" from "correct
            # credentials, but RegisterView's OTP step was never
            # completed" — SimpleJWT's default authenticate() treats both
            # as one generic failure (Django's is_active=False check makes
            # authenticate() return None either way), which otherwise shows
            # a signed-up owner "Incorrect username or password" forever,
            # with no way to tell them to go check their email instead.
            User = get_user_model()
            username = attrs.get(self.username_field, '')
            user = User.objects.filter(**{self.username_field: username}).first()
            if user is not None and not user.is_active and user.check_password(attrs.get('password', '')):
                raise serializers.ValidationError({
                    'detail': 'Please verify your email before logging in — enter the 6-digit code we '
                              'sent you, or request a new one.',
                    'code': 'email_not_verified',
                    'email': user.email,
                })
            raise

        request = self.context.get('request')
        device_id = (request.data.get('device_id') or '').strip() if request else ''
        device_type = (request.data.get('device_type') or '').strip() if request else ''
        branch_id = (request.data.get('branch_id') or '').strip() if request else ''

        if device_type == 'desktop' and device_id:
            user = self.user
            device = Device.objects.filter(id=device_id, is_deleted=False).first()

            if device is None:
                # First-ever login on this physical machine — pair it now.
                # (Device.shop is a required FK, so "device row exists" and
                # "device is paired" are the same thing; there's no
                # in-between unpaired state to represent.)
                shop = resolve_branch_for_device_login(user, branch_id=branch_id or None)
                if shop is None:
                    raise serializers.ValidationError({
                        'detail': 'This account is not linked to any branch, so this device cannot be set up with it.',
                        'code': 'no_branch',
                    })
                Device.objects.create(id=device_id, shop=shop, device_type='desktop')
            elif not user_may_use_branch_device(user, device.shop):
                raise serializers.ValidationError({
                    'detail': f'This device is set up for {device.shop.name}. Log in with an account from '
                              f'that branch, or ask its owner to remove it from Settings → Devices first.',
                    'code': 'device_shop_mismatch',
                })

        return data
