"""Instagram placement choice from the composer form.

New file on purpose: the fork keeps its own code out of upstream modules so a
rebase stays cheap. ``apps/composer/views.py`` only calls in here.

What this module owns: turning the composer's POST fields into the
``platform_extra`` dict that the publisher stores on a PlatformPost. The
placement itself is not a new concept for the engine -- ``_resolve_post_type``
already reads ``platform_extra["post_type"]`` and validates it against the
``PostType`` enum. Until now nothing ever wrote it for Instagram, so the
automatic derivation (one video -> Reel, several files -> carousel, an image ->
feed image) was the only thing you could get, and a Story was unreachable.
"""

from .instagram_specs import CAROUSEL_MAX_ITEMS, blocking_message, check_media

# The placements you can pick per account. Keys are PostType values, because
# that is what the engine reads back; anything else is silently ignored there,
# so this list and the enum must not drift apart.
INSTAGRAM_PLACEMENTS = ("reel", "story", "carousel", "image")

# The way back. The panel sends this when "Automatic" is picked, and it means
# "store no post_type at all", so the publisher derives the placement the way
# it did before this fork. It is a distinct value rather than an empty string
# because empty already means "the panel said nothing, keep what is stored":
# a half-rendered form must not be able to reset a Story to automatic.
INSTAGRAM_AUTO = "auto"

# Only a Reel carries a cover. The verified parameter table has thumb_offset
# on videos and Reels, and Instagram publishes a standalone video as a Reel, so
# in practice that is one case. A Story is never shown in a grid and has
# nothing to be a thumbnail of; a carousel's cover is its first child.
COVER_PLACEMENTS = ("reel",)

# The platforms this module speaks for: the Graph API app and the newer
# Instagram Login app. They take the same container payload.
INSTAGRAM_PLATFORMS = ("instagram", "instagram_login")


def build_instagram_extra(request, acc_id, existing=None):
    """Build the ``platform_extra`` dict for one Instagram account.

    ``existing`` is the platform_extra already stored on the PlatformPost. It
    matters for one reason: a submit that does not carry the panel (an API
    save, a bulk action, a half-rendered form) must not wipe a choice the user
    made earlier. Same call the TikTok privacy level makes, for the same
    reason.
    """
    existing = existing or {}
    placement = (request.POST.get(f"ig_placement_{acc_id}", "") or "").strip().lower()
    if placement == INSTAGRAM_AUTO:
        # Explicitly back to the derivation: nothing stored, nothing kept.
        placement = ""
    elif placement not in INSTAGRAM_PLACEMENTS:
        # Unknown or empty: keep what was stored. Falling back to a default
        # here would quietly turn a Story back into a Reel on the next save.
        placement = existing.get("post_type", "")

    extra = {}
    if placement:
        extra["post_type"] = placement

    # share_to_feed exists only for Reels. Sending it on anything else is an
    # API error, so it is dropped rather than stored-and-filtered later.
    if placement == "reel":
        extra["share_to_feed"] = request.POST.get(f"ig_share_to_feed_{acc_id}") == "true"

    # The cover is a timestamp in milliseconds, the same shape TikTok uses, so
    # the composer's frame picker is reused as-is. int() is the parse rather
    # than str.isdigit() because isdigit() is true for things like superscripts
    # that are not a number at all. (int() does accept non-ASCII decimal
    # digits, so "\u0661\u0662" parses as 12 -- harmless for a millisecond
    # offset, and not something the picker can produce anyway.)
    if placement in COVER_PLACEMENTS:
        raw = (request.POST.get(f"ig_cover_timestamp_ms_{acc_id}", "") or "").strip()
        if raw:
            try:
                offset = int(raw)
            except ValueError:
                offset = -1
            if offset >= 0:
                extra["thumb_offset"] = offset

    return extra


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# What each placement needs from the attached media. A placement that does not
# match is not a warning but a hard stop: Instagram accepts the container and
# then fails asynchronously, which surfaces as "container failed" minutes later
# in the publish log instead of in the composer.
_NEEDS = {
    "reel": "video",
    "story": "media",
    "carousel": "multiple",
    "image": "image",
}


def _media_records(post, workspace, session_media_ids=()):
    """The media this post will publish with: kind plus measurements, in order.

    Two sources, because the composer manages media out of band: an existing
    post has PostMedia rows, a post that is being created for the first time
    has its uploads parked in the session. Neither is present in the form
    itself, so this cannot be read off request.POST.

    ``width``/``height``/``duration`` are 0 on an asset whose ffprobe task has
    not run yet. That is what apps/composer/instagram_specs.py reads as
    "unknown" and skips, so a fresh upload is never blocked on a measurement
    that does not exist yet.
    """
    # Not ``post.pk``: Post's primary key is a UUID with a default, so an
    # unsaved instance already has one. ``_state.adding`` is the thing that
    # actually distinguishes "not in the database yet", and getting this wrong
    # means a brand new post is judged on an empty attachment list.
    if not post._state.adding:
        attachments = post.media_attachments.select_related("media_asset").order_by("position")
        return [_record(pm.media_asset) for pm in attachments]

    if not session_media_ids:
        return []

    from apps.media_library.models import MediaAsset

    assets = {str(a.id): a for a in MediaAsset.objects.filter(id__in=session_media_ids, workspace=workspace)}
    records = []
    for asset_id in session_media_ids:
        asset = assets.get(str(asset_id))
        if asset is not None:
            records.append(_record(asset))
    return records


def _record(asset):
    return {
        "kind": "video" if asset.is_video else "image",
        "width": asset.width,
        "height": asset.height,
        "duration": asset.duration,
    }


def placement_media_error(placement, kinds):
    """Why ``placement`` cannot be published with ``kinds``, or None if it can.

    Pure: no request, no database. The same rules are mirrored client-side so
    the composer can grey the option out before you schedule, and this is the
    copy that both sides show.
    """
    needs = _NEEDS.get(placement)
    if needs is None:
        return None
    if not kinds:
        return "Attach media before choosing an Instagram placement."
    if needs == "video" and kinds[0] != "video":
        return "A Reel needs a video. Attach one or pick another placement."
    if needs == "image" and kinds[0] != "image":
        return "A feed image needs an image. Instagram publishes a lone video as a Reel."
    if needs == "multiple" and len(kinds) < 2:
        return "A carousel needs at least two files."
    if needs == "multiple" and len(kinds) > CAROUSEL_MAX_ITEMS:
        return f"A carousel holds {CAROUSEL_MAX_ITEMS} files at most. Remove {len(kinds) - CAROUSEL_MAX_ITEMS}."
    return None


def placement_spec_error(placement, records):
    """Why Instagram would refuse this media at this placement, or None.

    The warnings from ``check_media`` are deliberately not returned here. They
    are shown in the panel and must never stop a save: cropping is a choice
    someone is allowed to make, and a composer that refuses to schedule a 16:9
    Reel would be worse than the problem it solves. Only findings the reference
    words as required or as a hard minimum come back through this door.

    A carousel is judged item by item, because its children are separate
    containers and any one of them can fail the whole publish. As the specs
    stand that loop cannot fire -- a carousel item is judged as a feed image
    and no feed image rule blocks -- but it is written this way so that turning
    one of them into a block does not silently leave the second file unchecked.
    """
    # Only a carousel publishes more than one file; every other placement uses
    # the first one and ignores the rest.
    candidates = records if placement == "carousel" else records[:1]

    for record in candidates:
        findings = check_media(
            record["width"],
            record["height"],
            record["duration"],
            placement,
            record["kind"],
        )
        message = blocking_message(findings)
        if message:
            return message
    return None


def instagram_placement_error(request, post, workspace, selected_ids, session_media_ids=()):
    """First placement problem across the selected Instagram accounts, or None.

    Returns the message rather than an HttpResponse: the fork keeps the
    response shape in upstream's hands, so views.py stays a three-line call.
    """
    if not selected_ids:
        return None

    from apps.social_accounts.models import SocialAccount

    accounts = SocialAccount.objects.filter(
        id__in=selected_ids,
        workspace=workspace,
        platform__in=INSTAGRAM_PLATFORMS,
    )
    if not accounts:
        return None

    # What is stored now, so validation sees exactly what the save will store:
    # an invalid submit keeps the previous placement, and that one still has to
    # match the media, which may well have changed since it was chosen.
    from .models import PlatformPost

    stored = {}
    if not post._state.adding:
        stored = {
            str(pp.social_account_id): (pp.platform_extra or {})
            for pp in PlatformPost.objects.filter(post=post, social_account__in=accounts)
        }

    records = _media_records(post, workspace, session_media_ids)
    kinds = [record["kind"] for record in records]
    for account in accounts:
        acc_id = str(account.id)
        if f"ig_placement_{acc_id}" not in request.POST:
            # Panel not in this form: nothing was chosen here, so there is
            # nothing new to reject. Same reasoning as build_instagram_extra.
            continue
        extra = build_instagram_extra(request, acc_id, stored.get(acc_id))
        placement = extra.get("post_type")
        if not placement:
            continue
        # Kind first, then measurements: "a Reel needs a video" is the more
        # useful thing to be told about an image than its aspect ratio is.
        message = placement_media_error(placement, kinds) or placement_spec_error(placement, records)
        if message:
            return f"{account.account_name}: {message}"
    return None
