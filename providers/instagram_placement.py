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
