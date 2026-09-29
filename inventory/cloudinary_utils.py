"""
Cloudinary integration for optional product images
(InventoryItem.image_url / cloudinary_public_id — see models.py). Kept in
its own module rather than inline in views.py so the SDK import and
upload/delete/validate logic sit in exactly one place.

Configuration comes from the environment ONLY (CLOUDINARY_CLOUD_NAME /
CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET, set in settings.py) -- same
pattern this project already uses for PAYSTACK_SECRET_KEY and
RESEND_API_KEY: never hardcoded, never committed. Uploads are proxied
through this backend (multipart request in -> Cloudinary's upload API),
never signed for a direct browser/Electron/Android upload, so the API
secret never has to leave this server or reach React, Electron, or the
Android app.
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

# --- validation -------------------------------------------------------
ALLOWED_CONTENT_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif'}
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB -- generous for a product photo, small enough to stay quick on a POS till's connection
UPLOAD_FOLDER = 'product_images'


class InvalidProductImage(ValueError):
    """Raised for a file that fails validation before it's ever sent to
    Cloudinary. Views catch this and turn it into a 400 with str(exc) as
    the message."""


def validate_product_image(file_obj):
    """Raises InvalidProductImage if `file_obj` (a Django UploadedFile)
    isn't an acceptable product photo. Checked here -- before Cloudinary
    ever sees the bytes -- so a bad or malicious upload never burns a
    Cloudinary API call or eats into storage quota."""
    if not file_obj:
        raise InvalidProductImage('No image file was provided.')

    size = getattr(file_obj, 'size', 0) or 0
    if size > MAX_UPLOAD_BYTES:
        raise InvalidProductImage(
            f'Image is too large ({size // 1024} KB) -- the max is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB.'
        )
    if size == 0:
        raise InvalidProductImage('That file appears to be empty.')

    content_type = (getattr(file_obj, 'content_type', '') or '').lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise InvalidProductImage('Unsupported file type -- please upload a JPEG, PNG, WEBP, or GIF image.')

    # Belt-and-braces beyond the browser/Electron-supplied content_type
    # (which a client can misreport) -- actually decode the image header
    # with Pillow to catch a renamed/corrupted non-image file. Optional:
    # if Pillow isn't installed this check is skipped rather than ever
    # blocking a legitimate upload -- content_type + size are still
    # enforced regardless.
    try:
        from PIL import Image
        file_obj.seek(0)
        Image.open(file_obj).verify()
    except ImportError:
        pass
    except Exception:
        raise InvalidProductImage('That file is not a valid image, or it is corrupted.')
    finally:
        try:
            file_obj.seek(0)
        except Exception:
            pass


def upload_product_image(file_obj, previous_public_id=None):
    """Uploads `file_obj` to Cloudinary. If `previous_public_id` is given,
    the upload reuses that exact public_id -- Cloudinary overwrites the
    asset in place, so "replace image" never leaves the old one orphaned
    and every client with the old URL cached briefly still resolves
    (until CDN invalidation catches up) rather than 404ing.

    Returns (secure_url, public_id). Raises on failure -- the view
    catches that and reports a clean error rather than a raw traceback.

    A transformation is applied on upload (not a separate step) so a
    large original phone photo doesn't get stored and served at full
    size/weight to a POS till: capped at 1024x1024 (never upscaled),
    automatic quality and format (e.g. WebP/AVIF) chosen per viewer.
    """
    public_id = previous_public_id or f'{UPLOAD_FOLDER}/{uuid.uuid4().hex}'
    result = cloudinary.uploader.upload(
        file_obj,
        public_id=public_id,
        overwrite=True,
        invalidate=True,  # so a replaced image's old CDN-cached copy doesn't linger
        resource_type='image',
        transformation=[
            {'width': 1024, 'height': 1024, 'crop': 'limit'},
            {'quality': 'auto', 'fetch_format': 'auto'},
        ],
    )
    return result['secure_url'], result['public_id']


def delete_product_image(public_id):
    """Best-effort delete of a Cloudinary asset -- called when an image is
    replaced or removed. Swallows errors on purpose: a failed cleanup
    call must never block the product write that triggered it (the
    product's image_url/cloudinary_public_id are already being
    cleared/replaced in Postgres either way); worst case is one orphaned
    asset left in the Cloudinary account rather than a broken request."""
    if not public_id:
        return
    try:
        cloudinary.uploader.destroy(public_id, resource_type='image')
    except Exception:
        pass
