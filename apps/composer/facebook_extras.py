"""Facebook placement choice from the composer form.

New file (Blue Monkey Media fork), modelled on ``instagram_extras.py``. Until
now the composer had one Facebook choice, a "Publish as" select between a
regular Page video and a Reel, and it only showed with exactly one video
attached. The panel in ``_facebook_settings.html`` replaces it with a row per
account: Automatic, Post, Reel and Story.

What gets stored, all in ``platform_extra``:

- **Automatic**: nothing. The publisher derives the format from the media, as
  it always did: a video becomes a Page video, photos a photo post, no media a
  text or link post.
- **Post**: ``placement = "post"`` and no ``post_type``. The publisher derives
  the same way as Automatic; the difference is that the choice is remembered,
  and that the composer refuses media Facebook will not take in one post.
- **Reel**: ``post_type = "reel"``, the hint upstream already wrote, so posts
  saved before this fork come back as Reel.
- **Story**: ``post_type = "story"``, one photo or one video, published by
  providers/facebook_stories.py. No caption, no first comment, no cover.

A submit without the row (an API save, a half-rendered form) keeps what is
stored, the same rule as Instagram and TikTok.
"""

from providers.facebook import FACEBOOK_MAX_ATTACHED_MEDIA

from .facebook_specs import check_media
from .instagram_extras import _media_records
from .instagram_specs import blocking_message

FIELD = "fb_placement_{acc_id}"

# Choices the server accepts.
FACEBOOK_PLACEMENTS = ("auto", "post", "reel", "story")

# The key that remembers "Post". Not ``post_type``: a stored "video" or
# "image" hint outlives the media it described (see _hint_matches_media in
# apps/publisher/engine.py), and the derivation already gets it right.
PLACEMENT_KEY = "placement"


def apply_facebook_placement(request, acc_id, extra, stored, post):
    """Write the placement choice into ``extra`` (mutated and returned).

    Called from the Facebook branch of ``_sync_platform_posts`` after
    upstream's own handling, which by then has dropped ``post_type`` because
    its select is gone. ``stored`` is the platform_extra before this save, so a
    submit without a usable value can put the earlier choice back. ``post`` is
    saved by then, so its attachments are what will be published.
    """
    stored = stored or {}
    field = FIELD.format(acc_id=acc_id)
    if field not in request.POST:
        return extra
    kinds = [record["kind"] for record in _media_records(post, None)]
    placement = (request.POST.get(field, "") or "").strip().lower()
    if placement not in FACEBOOK_PLACEMENTS:
        # Unknown or empty: keep the earlier choice. Falling back to a default
        # would turn a Reel into a Page video on the next save.
        placement = stored_placement(stored)

    extra.pop("post_type", None)
    extra.pop(PLACEMENT_KEY, None)
    if placement == "reel" and kinds == ["video"]:
        extra["post_type"] = "reel"
    elif placement == "story" and len(kinds) == 1:
        extra["post_type"] = "story"
        # Facebook shows a Story full screen, without a thumbnail to replace.
        extra.pop("cover_asset_id", None)
    elif placement == "post":
        extra[PLACEMENT_KEY] = "post"
    # A Reel or Story whose media no longer fit falls back to Automatic, as
    # upstream did for the Reel.
    return extra


def stored_placement(extra):
    """The choice the row shows for a stored platform_extra."""
    extra = extra or {}
    if extra.get("post_type") in ("reel", "story"):
        return extra["post_type"]
    if extra.get(PLACEMENT_KEY) == "post":
        return "post"
    return "auto"


def placement_media_error(placement, kinds):
    """Why ``placement`` cannot be published with ``kinds``, or None.

    Pure. Mirrored in ``_facebook_settings.html`` with the same wording, so the
    composer greys the option out before the server refuses the save. The rules
    are the ones the provider enforces at publish time anyway
    (``_publish_multi_photo``, ``_publish_reel``), moved forward to where they
    can still be fixed.
    """
    if placement == "reel":
        if kinds != ["video"]:
            return "A Reel needs exactly one video and nothing else."
        return None
    if placement == "story":
        if len(kinds) != 1:
            return "A Story takes exactly one photo or one video."
        return None
    if placement == "post":
        videos = kinds.count("video")
        if videos and len(kinds) > 1:
            return "A Facebook post takes photos or one video, not both. Remove the extra files or pick Automatic."
        if len(kinds) > FACEBOOK_MAX_ATTACHED_MEDIA:
            return f"A Facebook post holds {FACEBOOK_MAX_ATTACHED_MEDIA} photos at most. Remove {len(kinds) - FACEBOOK_MAX_ATTACHED_MEDIA}."
        return None
    return None


def facebook_placement_error(request, post, workspace, selected_ids, session_media_ids=()):
    """First placement problem across the selected Facebook accounts, or None."""
    if not selected_ids:
        return None

    from apps.social_accounts.models import SocialAccount

    accounts = [
        account
        for account in SocialAccount.objects.filter(id__in=selected_ids, workspace=workspace, platform="facebook")
        if FIELD.format(acc_id=account.id) in request.POST
    ]
    if not accounts:
        return None

    records = _media_records(post, workspace, session_media_ids)
    kinds = [record["kind"] for record in records]
    for account in accounts:
        placement = (request.POST.get(FIELD.format(acc_id=account.id), "") or "").strip().lower()
        # Kind first, then measurements, as for Instagram. A Reel and a Story
        # publish one file, so only that one is measured.
        message = placement_media_error(placement, kinds)
        if not message and records:
            first = records[0]
            message = blocking_message(
                check_media(first["width"], first["height"], first["duration"], placement, first["kind"])
            )
        if message:
            return f"{account.account_name}: {message}"
    return None
