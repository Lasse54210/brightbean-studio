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
# bmm/instagram-plaatsingen-plan.md. user_tags and collaborators got their UI
# on 2026-09-29 (see "Taggen" in BMM-FORK.md).
NO_UI_YET = ("audio_name", "location_id", "alt_text")

# Instagram takes at most 20 people tagged on one file.
MAX_USER_TAGS = 20


def user_tags_payload(usernames, media_type):
    """The ``user_tags`` value for one container.

    The composer stores bare usernames, because where a tag may sit depends on
    the placement, and that is only known here. A feed image (and an image in a
    carousel) requires ``x``/``y`` between 0 and 1; a Story makes them optional
    and a Reel or a video has no point to tag, so those get the username only.
    The tags are spread along the middle of the image: nobody sees where a tag
    sits until they tap the photo, and stacking them on one point hides all but
    the top one.

    Entries that already are dicts pass through untouched.
    """
    names = [name for name in (usernames or []) if name][:MAX_USER_TAGS]
    tags = []
    for index, name in enumerate(names):
        if isinstance(name, dict):
            tags.append(name)
            continue
        tag = {"username": name}
        if media_type == "":
            tag["x"] = round((index + 1) / (len(names) + 1), 3)
            tag["y"] = 0.5
        tags.append(tag)
    return tags


def apply_carousel_child_tags(child_payload, content, index):
    """Tag people on the first file of a carousel.

    A carousel container takes no ``user_tags``; its children do. Tagging every
    slide would put the same people on each one, so the tags go on the first,
    which is also the one shown in the feed.
    """
    if index != 0:
        return child_payload
    usernames = (getattr(content, "extra", None) or {}).get("user_tags")
    tags = user_tags_payload(usernames, child_payload.get("media_type", ""))
    if tags:
        child_payload["user_tags"] = tags
    return child_payload


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
        if key == "user_tags":
            value = user_tags_payload(value, media_type)
        payload[key] = value

    return payload
