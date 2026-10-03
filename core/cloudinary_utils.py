"""
Generic Cloudinary helper for any optional image-upload feature on this
backend. inventory.cloudinary_utils predates this and has its own
product-image-specific copy of the same logic — deliberately left alone
here rather than refactored to share this module, so this addition can't
regress that already-shipped feature. Any NEW image upload (expense
receipts now; anything later) should use this one instead of copy-pasting
a third time.

Configuration comes from the environment only (CLOUDINARY_CLOUD_NAME /
CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET, set in settings.py) — same
pattern as inventory.cloudinary_utils and PAYSTACK_SECRET_KEY /
RESEND_API_KEY elsewhere in this project. Uploads are proxied through this
backend (multipart request in -> Cloudinary's upload API), never signed
for a direct browser/Electron/Android upload, so the API secret never has
to leave this server.
"""
import uuid

import cloudinary
import cloudinary.uploader
from django.conf import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
)

ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif'}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB — generous for a photographed receipt, small enough to stay quick on a POS till's connection


class InvalidImage(ValueError):
    """Raised for a file that fails validation before it's ever sent to
    Cloudinary. Callers catch this and turn it into a 400 with str(exc)."""


def validate_image(file_obj):
    """Raises InvalidImage if `file_obj` (a Django UploadedFile) isn't an
    acceptable photo. Checked here — before Cloudinary ever sees the
    bytes — so a bad or malicious upload never burns an API call or eats
    into storage quota. Same checks as inventory.cloudinary_utils'
    validate_product_image, kept identical on purpose."""
    if not file_obj:
        raise InvalidImage('No file was provided.')

    size = getattr(file_obj, 'size', 0) or 0
    if size > MAX_UPLOAD_BYTES:
        raise InvalidImage(f'File is too large ({size // 1024} KB) — the max is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.')
    if size == 0:
        raise InvalidImage('That file appears to be empty.')

    content_type = (getattr(file_obj, 'content_type', '') or '').lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidImage('Unsupported file type — please upload a JPEG, PNG, WEBP, or GIF image.')

    try:
        from PIL import Image
        file_obj.seek(0)
        Image.open(file_obj).verify()
    except ImportError:
        pass
    except Exception:
        raise InvalidImage('That file is not a valid image, or it is corrupted.')
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass


def upload_image(file_obj, folder, previous_public_id=None):
    """Uploads `file_obj` to Cloudinary under `folder` (e.g.
    'expense_receipts'). If `previous_public_id` is given, the upload
    reuses that exact public_id so "replace" overwrites in place rather
    than orphaning the old asset. Returns (secure_url, public_id). Raises
    on failure — the caller reports a clean error rather than a raw
    traceback.

    Capped at 1600x1600 (never upscaled) with automatic quality/format —
    a receipt photo just needs to stay legible, not full camera
    resolution."""
    public_id = previous_public_id or f'{folder}/{uuid.uuid4().hex}'
    result = cloudinary.uploader.upload(
        file_obj,
        public_id=public_id,
        overwrite=True,
        invalidate=True,
        resource_type='image',
        transformation=[
            {'width': 1600, 'height': 1600, 'crop': 'limit'},
            {'quality': 'auto', 'fetch_format': 'auto'},
        ],
    )
    return result['secure_url'], result['public_id']


def delete_image(public_id):
    """Best-effort delete — never blocks the write that triggered it.
    Worst case is one orphaned asset in the Cloudinary account rather
    than a broken request."""
    if not public_id:
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type='image')
    except Exception:
        pass
