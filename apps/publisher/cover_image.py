"""An image of its own as a video cover: Instagram Reels and Facebook videos.

New file (Blue Monkey Media fork). The composer stores the chosen image as a
MediaAsset id in ``platform_extra["cover_asset_id"]`` (see
apps/composer/video_cover.py). This module is the one place that turns it into
``cover_url``, a public JPEG URL, called with one line from ``engine.py``.
Instagram fetches that URL itself; the Facebook provider downloads it and
posts the bytes to the video (providers/facebook_cover.py).

What Instagram accepts (Graph API reference for ``POST /{ig-user-id}/media``,
checked 2026-10-05): JPEG, at most 8 MB, sRGB, 9:16 recommended. Facebook's
``/{video-id}/thumbnails`` takes up to 10 MB, so one file serves both. A different
ratio is not refused but cropped to the middle 9:16, and to the middle square
for the feed grid. The team mostly exports PNG, so anything that is not a
small enough JPEG is converted once and stored next to the original under
``video-covers/``; the next publish of the same asset reuses that file.

A cover that cannot be resolved never stops the publish. The Reel goes out
with the frame cover (``thumb_offset``) or Instagram's default, and the log
says why. Losing a cover is a nuisance; losing the post over it would not be.
"""

from __future__ import annotations

import io
import logging

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

COVER_PLATFORMS = ("instagram", "instagram_login", "facebook")

MAX_BYTES = 8 * 1024 * 1024
# Long side of a converted cover. 1920 is the height of a 1080x1920 Reel, so
# nothing visible is lost, and at quality 90 that stays far below 8 MB.
MAX_SIDE = 1920
COVER_DIR = "video-covers"


def apply_cover_image(extra, platform):
    """Swap ``cover_asset_id`` in ``extra`` for ``cover_url``.

    Mutates ``extra``. The id is always removed, also for other platforms and
    when resolving fails, so it can never travel on to a provider.
    """
    asset_id = extra.pop("cover_asset_id", None)
    if not asset_id or platform not in COVER_PLATFORMS:
        return extra
    try:
        url = cover_url_for(asset_id)
    except Exception:
        logger.exception("Cover image %s could not be prepared; publishing without it", asset_id)
        return extra
    if url:
        extra["cover_url"] = url
    return extra


def cover_url_for(asset_id):
    """A public URL of a JPEG both platforms will take, or None."""
    from apps.media_library.models import MediaAsset

    asset = MediaAsset.objects.filter(id=asset_id).first()
    if asset is None or not asset.file:
        logger.warning("Cover image %s is gone from the media library; publishing without it", asset_id)
        return None
    if asset.media_type != MediaAsset.MediaType.IMAGE:
        logger.warning("Cover image %s is not an image; publishing without it", asset_id)
        return None

    if _is_usable_as_is(asset):
        return _absolute(asset.file.url)

    storage = asset.file.storage
    name = f"{COVER_DIR}/{asset.id}.jpg"
    if not storage.exists(name):
        with asset.file.open("rb") as fh:
            data = to_cover_jpeg(fh)
        name = storage.save(name, ContentFile(data))
    return _absolute(storage.url(name))


def _is_usable_as_is(asset):
    mime = (asset.mime_type or "").lower()
    size = asset.file_size or 0
    return mime in ("image/jpeg", "image/jpg") and 0 < size <= MAX_BYTES


def to_cover_jpeg(fileobj):
    """The bytes of a JPEG version of the image in ``fileobj``.

    Transparency is flattened onto white, the way the media library's own
    thumbnails do it; a JPEG has no alpha and would otherwise show it as black.
    """
    from PIL import Image, ImageOps

    img = Image.open(fileobj)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    for quality in (90, 80, 70):
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= MAX_BYTES:
            return buffer.getvalue()
    return buffer.getvalue()


def _absolute(url):
    if url.startswith("/"):
        return f"{getattr(settings, 'APP_URL', '').rstrip('/')}{url}"
    return url
