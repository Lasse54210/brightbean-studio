"""Publish a Facebook Page Story, photo or video.

New file (Blue Monkey Media fork). ``FacebookProvider.publish_post`` calls in
here with one line when ``post_type`` is STORY. Checked against the Page
Stories API reference on 2026-10-05:

- **Photo:** upload the photo unpublished (``POST /{page}/photos`` with
  ``published=false``), then ``POST /{page}/photo_stories`` with its
  ``photo_id``. Answers ``{"success": true, "post_id": ...}``.
- **Video:** the same start/upload/finish protocol as a Reel, on
  ``/{page}/video_stories``. ``start`` gives ``video_id`` and an
  ``upload_url`` on rupload.facebook.com, the upload sends the hosted file as a
  ``file_url`` header, ``finish`` answers ``{"success": true, "post_id": ...}``.

Permissions: ``pages_manage_posts``, ``pages_read_engagement`` and
``pages_show_list``, which the Page login already asks for.

A Story has no caption, so ``content.text`` is not sent. Media rules: one file;
a photo up to 10 MB; a video 9:16, at least 540x960, 24-60 fps. On duration the
reference contradicts itself ("3 to 90 seconds" in the requirements table, "a
video story can not exceed 60 seconds" under limitations), so this keeps to the
stricter 60. A file that was already used in a published post is refused by
Facebook; that is not checkable here.

Everything after a successful publish is best-effort and must never raise: the
engine retries on an exception, and a retry posts the Story a second time.
"""

from __future__ import annotations

import logging

from .exceptions import PublishError
from .types import PublishResult

logger = logging.getLogger(__name__)

STORY_MIN_DURATION_SEC = 3
STORY_MAX_DURATION_SEC = 60


def publish_story(provider, access_token, base_url, page_id, content):
    """Publish ``content`` as a Story on ``page_id`` and return a PublishResult."""
    if len(content.media_urls) != 1:
        raise PublishError("A Facebook Story takes exactly one photo or video", platform=provider.platform_name)
    if content.is_video(0):
        return _publish_video_story(provider, access_token, base_url, page_id, content)
    return _publish_photo_story(provider, access_token, base_url, page_id, content)


def _publish_photo_story(provider, access_token, base_url, page_id, content):
    staged = provider._request(
        "POST",
        f"{base_url}/{page_id}/photos",
        access_token=access_token,
        json={"url": content.media_urls[0], "published": False},
    ).json()
    photo_id = staged.get("id")
    if not photo_id:
        raise PublishError(
            "Facebook did not accept the photo for the Story", platform=provider.platform_name, raw_response=staged
        )

    try:
        data = provider._request(
            "POST",
            f"{base_url}/{page_id}/photo_stories",
            access_token=access_token,
            data={"photo_id": photo_id},
        ).json()
        _raise_unless_published(provider, data, "publish the photo Story")
    except Exception:
        # Not published, so the staged photo is an orphan on the Page. Remove
        # it before the engine retries, which would stage another one.
        provider._delete_staged_photos(access_token, [photo_id])
        raise

    return _result(page_id, data, {"photo_id": photo_id})


def _publish_video_story(provider, access_token, base_url, page_id, content):
    # Fails open on an unknown duration, like the Reel check: the ffprobe that
    # fills it is best-effort and may not have run.
    duration = content.video_duration_sec
    if duration is not None and not (STORY_MIN_DURATION_SEC <= duration <= STORY_MAX_DURATION_SEC):
        raise PublishError(
            f"A Facebook video Story must be {STORY_MIN_DURATION_SEC} to {STORY_MAX_DURATION_SEC} seconds",
            platform=provider.platform_name,
        )

    start = provider._request(
        "POST",
        f"{base_url}/{page_id}/video_stories",
        access_token=access_token,
        data={"upload_phase": "start"},
    ).json()
    video_id = start.get("video_id")
    upload_url = start.get("upload_url")
    if not video_id or not upload_url:
        raise PublishError(
            "Facebook did not create a Story upload session", platform=provider.platform_name, raw_response=start
        )

    # Same as the Reel upload: OAuth rather than Bearer, the hosted file in a
    # header, and a body that can say "failed" behind a 2xx.
    upload = provider._request(
        "POST",
        upload_url,
        headers={"Authorization": f"OAuth {access_token}", "file_url": content.media_urls[0]},
    )
    _raise_unless_published(provider, provider._safe_json(upload), "upload the Story video")

    data = provider._request(
        "POST",
        f"{base_url}/{page_id}/video_stories",
        access_token=access_token,
        data={"upload_phase": "finish", "video_id": video_id},
    ).json()
    _raise_unless_published(provider, data, "publish the video Story")

    return _result(page_id, data, {"video_id": video_id})


def _raise_unless_published(provider, data, action):
    """Fail a step that answered 2xx without succeeding."""
    if not isinstance(data, dict) or data.get("success") is False or "error" in data:
        raise PublishError(f"Facebook failed to {action}", platform=provider.platform_name, raw_response=data)


def _result(page_id, data, ids):
    """The PublishResult of a Story that is live. Never raises.

    Only identifiers go into ``extra``: the engine merges it into
    platform_extra, which duplicates and recurrences copy as input.
    """
    try:
        post_id = str(data.get("post_id") or "")
        stored = post_id.rsplit("_", 1)[-1] if post_id else next(iter(ids.values()))
        return PublishResult(
            platform_post_id=stored,
            url=f"https://www.facebook.com/{page_id}",
            extra={**ids, **({"post_id": post_id} if post_id else {})},
        )
    except Exception:
        logger.warning("Facebook Story on page %s is live; its ids could not be read", page_id, exc_info=True)
        return PublishResult(platform_post_id=str(next(iter(ids.values()), "")), url="", extra={})
