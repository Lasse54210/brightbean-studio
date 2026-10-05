"""Put a cover image on a Facebook video or Reel.

New file (Blue Monkey Media fork). Facebook takes no cover in the publish
calls themselves; the Reels guide sends you to ``POST /{video-id}/thumbnails``
("Add a custom cover photo for your reel"), which takes the image as a file
(``source``, at most 10 MB) plus ``is_preferred=true``. It needs
``pages_read_user_content``, ``pages_manage_engagement`` and
``pages_show_list``, which the Page login already asks for. Checked against
the Graph API reference on 2026-10-05.

``content.extra["cover_url"]`` is the public JPEG that
apps/publisher/cover_image.py prepared. This runs after the video is
published, so it must never raise: an exception here would make the engine
retry, and a retry posts the video a second time. A cover that does not land
is logged and the post stands.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
DOWNLOAD_TIMEOUT = 30.0


def set_video_cover(provider, access_token, base_url, video_id, content):
    """Make the cover image the preferred thumbnail of ``video_id``.

    Returns True when Facebook accepted it, False otherwise (including when
    there is no cover to set).
    """
    cover_url = (getattr(content, "extra", None) or {}).get("cover_url")
    if not cover_url or not video_id:
        return False
    try:
        image = _download(cover_url)
        resp = provider._request(
            "POST",
            f"{base_url}/{video_id}/thumbnails",
            access_token=access_token,
            data={"is_preferred": "true"},
            files={"source": ("cover.jpg", image, "image/jpeg")},
        )
        body = provider._safe_json(resp)
        if body.get("success") is False or "error" in body:
            logger.warning("Facebook refused the cover for video %s: %s", video_id, body)
            return False
        return True
    except Exception:
        logger.warning(
            "Facebook cover for video %s not set; the video is published without it", video_id, exc_info=True
        )
        return False


def _download(url):
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        if len(resp.content) > MAX_BYTES:
            raise ValueError(f"cover image is {len(resp.content)} bytes, Facebook takes at most {MAX_BYTES}")
        return resp.content
