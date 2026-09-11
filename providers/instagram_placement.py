"""Put the composer's placement choice into the Instagram container payload.

New file, shared by ``providers/instagram.py`` and
``providers/instagram_login.py``: both build the same ``POST /media`` container
and both were dropping everything in ``content.extra`` on the floor.

The allowlist below is the point of this module. Instagram does not ignore a
parameter that does not apply to the chosen media type -- it rejects the
container, and the error comes back as a generic container failure that tells
you nothing. So each key names the placements it is allowed on, and anything
else is left out.

Verified on 2026-09-11 against the Graph API reference for
``POST /{ig-user-id}/media``.
"""

from urllib.parse import urlsplit

# What Instagram accepts as video. Anything else in ``media_urls`` is sent as an
# image, which is also what upstream did; the difference is *where* the
# extension is read from, see ``is_video_url``.
VIDEO_EXTENSIONS = (".mp4", ".mov")


def is_video_url(url):
    """Whether ``url`` points at a video, judged on the path and not the URL.

    Upstream did ``url.endswith(".mp4")`` on the whole string. That holds only
    while media URLs are unsigned: a presigned S3 URL ends in
    ``?X-Amz-Signature=...`` and then every video reads as an image, so a
    Story video goes out as ``image_url`` and the container fails minutes
    later with nothing useful in the error. Reading the path makes both URL
    shapes behave the same, which matters because switching the storage to
    presigned is a two-line change in the stack's ``.env``.
    """
    if not url:
        return False
    return urlsplit(url).path.lower().endswith(VIDEO_EXTENSIONS)


# key -> the media_type values it is valid on. "" is a feed image (Instagram's
# container takes no media_type for that case).
_ALLOWED_ON = {
    "share_to_feed": ("REELS",),
    "cover_url": ("REELS",),
    "thumb_offset": ("REELS",),
    "audio_name": ("REELS",),
    "collaborators": ("REELS", "CAROUSEL", ""),
    "user_tags": ("REELS", "STORIES", ""),
    "location_id": ("REELS", "STORIES", ""),
    "alt_text": ("",),
}

# Keys the composer never sends today. They are here because the allowlist is
# the documentation of what may be sent, and a later UI should not have to
# rediscover which placement each one belongs to. See "Wat we niet doen" in
# bmm/instagram-plaatsingen-plan.md.
NO_UI_YET = ("audio_name", "collaborators", "user_tags", "location_id", "alt_text")


def apply_placement(payload, content):
    """Copy the allowed extras from ``content.extra`` into ``payload``.

    ``payload`` is the container dict the provider has just built, with
    ``media_type`` already set (absent for a feed image). It is mutated in
    place and also returned, so the call site can be a single line.

    Nothing here decides the placement: that is ``post_type``, which the engine
    resolved before the provider was called. This only adds the parameters that
    hang off it.
    """
    extra = getattr(content, "extra", None) or {}
    media_type = payload.get("media_type", "")

    for key, allowed in _ALLOWED_ON.items():
        if key not in extra:
            continue
        if media_type not in allowed:
            continue
        value = extra[key]
        if value is None:
            continue
        # A false share_to_feed is meaningful (keep the Reel out of the grid),
        # so booleans are passed through as-is rather than treated as empty.
        if value == "" or value == []:
            continue
        payload[key] = value

    return payload
