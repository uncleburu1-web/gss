"""
Django settings for the Benchline shop-management API — serves gadgets,
pharmacy, clothing, and general-retail shops alike (see
core.models.Organization.BUSINESS_TYPE_CHOICES).
"""

from pathlib import Path
from datetime import timedelta
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-me-in-production')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())
INSTALLED_APPS = [
    'daphne',  # must load first — patches `runserver` to be ASGI/WebSocket-aware for local dev
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # third party
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'channels',

    # local apps
    'core',
    'staff',
    'suppliers',
    'inventory',
    'repairs',
    'sales',
    'liabilities',
    'expenses',
    'reports',
    'devices',
    'subscriptions',
    'sync',
    'customers',
    'realtime',
    'branches',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'benchline.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'benchline.wsgi.application'
ASGI_APPLICATION = 'benchline.asgi.application'

# ---------------------------------------------------------------------------
# Channel layer — how WebSocket consumers on different processes talk to
# each other (needed so a broadcast from an HTTP request, handled by a
# gunicorn/WSGI worker, reaches a WebSocket connection open on a *different*
# process). In-memory by default: correct and zero-setup for a single
# process (fine for this shop's current scale), but it does NOT broadcast
# across multiple processes/dynos — set REDIS_URL once running more than
# one worker and this switches to the real (Redis-backed) layer with no
# other code changes needed.
# ---------------------------------------------------------------------------
REDIS_URL = config('REDIS_URL', default='')
if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [REDIS_URL]},
        }
    }
else:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# ---------------------------------------------------------------------------
# Database — Postgres by default, override with env vars (see .env.example).
# Falls back to sqlite only when USE_SQLITE=True, which is handy for quick
# local checks that don't need a running Postgres server.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Database — set DATABASE_URL in .env to point at Postgres (Render, Railway,
# etc.), or set USE_SQLITE=True for a quick local check with no Postgres
# running. No hardcoded fallback connection string on purpose — see the
# comment down at DATABASES for why.
# ---------------------------------------------------------------------------
import dj_database_url
if config('USE_SQLITE', default=False, cast=bool):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': dj_database_url.parse(
            config('DATABASE_URL'),
            conn_max_age=600,
            conn_health_checks=True,   # <-- add this
            ssl_require=config('DB_SSL_REQUIRE', default=True, cast=bool),
        )
    }
    # dj_database_url doesn't expose keepalive OPTIONS directly, so merge them in.
    # Use setdefault/update rather than overwrite — ssl_require already injected
    # OPTIONS['sslmode'], and we don't want to clobber it.
    DATABASES['default'].setdefault('OPTIONS', {}).update({
        'keepalives': 1,
        'keepalives_idle': 30,
        'keepalives_interval': 10,
        'keepalives_count': 5,
    })

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = config('TIME_ZONE', default='Africa/Lagos')
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# DRF + JWT
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=8),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=14),
    'ROTATE_REFRESH_TOKENS': True,
}

# ---------------------------------------------------------------------------
# CORS — allow the React dev server / deployed frontend origin.
# ---------------------------------------------------------------------------
from corsheaders.defaults import default_headers

CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:5173,http://localhost:3000', cast=Csv())
CORS_ALLOW_CREDENTIALS = True
# django-cors-headers only allows its own fixed default header set through
# preflight otherwise -- api/client.js's request interceptor attaches a
# custom `X-Branch-ID` header on every request that has one cached (see its
# comment), which isn't in that default list. Without this, the BROWSER
# rejects the preflight before the request ever reaches Django at all, on
# every endpoint, for any visitor whose browser happens to have a branch ID
# cached from an earlier session -- surfacing to axios as a bare network
# error with no response (no status code, no body), which is exactly what
# produced Signup.jsx's generic "Could not create your account" fallback
# and could equally well have broken a branch user's login the same way.
CORS_ALLOW_HEADERS = list(default_headers) + ['x-branch-id']

# ---------------------------------------------------------------------------
# Paystack — subscription billing (SaaS). Keys come from the environment
# ONLY, never a hardcoded default — see the DATABASE_URL comment above for
# why that matters. Set the real values in your local .env (gitignored)
# and in Railway's environment variables for production; never commit them.
# PAYSTACK_SECRET_KEY is server-side only (charges, webhook verification —
# never sent to a client). PAYSTACK_PUBLIC_KEY is safe to expose to the
# frontend/desktop, since that's what Paystack's own checkout JS expects.
# ---------------------------------------------------------------------------
PAYSTACK_SECRET_KEY = config('PAYSTACK_SECRET_KEY', default='')
PAYSTACK_PUBLIC_KEY = config('PAYSTACK_PUBLIC_KEY', default='')

# ---------------------------------------------------------------------------
# Resend — transactional email for the signup OTP flow (core.email_utils /
# core.views.RegisterView, VerifyOTPView, ResendOTPView). Same pattern as
# Paystack above: real value only from the environment, blank default so
# nothing is ever hardcoded/committed. With RESEND_API_KEY blank (e.g. local
# dev with no account set up yet), send_otp_email() logs the code instead of
# emailing it, so registration/verification still work end-to-end locally.
# EMAIL_FROM must be a sender address on a domain verified with Resend --
# their sandbox "onboarding@resend.dev" works before you've verified your
# own domain, but only delivers to the Resend account's own owner address.
# ---------------------------------------------------------------------------
RESEND_API_KEY = config('RESEND_API_KEY', default='')
EMAIL_FROM = config('EMAIL_FROM', default='GSS <onboarding@resend.dev>')

# ---------------------------------------------------------------------------
# Cloudinary — hosting for the optional product-image feature
# (inventory.cloudinary_utils / InventoryItem.image_url). Same pattern as
# Paystack/Resend above: real values only from the environment, blank
# defaults so nothing is ever hardcoded/committed. Set these in your local
# .env and in Railway's environment variables; get them from your
# Cloudinary dashboard (Settings -> API Keys). With any of these blank,
# uploading a product image will fail (a clear error, not a crash) --
# every other product feature, including creating/editing products with
# no image, keeps working exactly as before regardless.
# ---------------------------------------------------------------------------
CLOUDINARY_CLOUD_NAME = config('CLOUDINARY_CLOUD_NAME', default='')
CLOUDINARY_API_KEY = config('CLOUDINARY_API_KEY', default='')
CLOUDINARY_API_SECRET = config('CLOUDINARY_API_SECRET', default='')
