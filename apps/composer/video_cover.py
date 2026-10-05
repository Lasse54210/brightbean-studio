"""An image of its own as the cover of a video: Instagram Reels and Facebook.

New file (Blue Monkey Media fork). Both platforms take a cover image next to
the video, but each in its own way: Instagram fetches ``cover_url`` itself,
Facebook wants the file posted to the video afterwards. What the composer
stores is the same for both, a MediaAsset id in
``platform_extra["cover_asset_id"]``, and apps/publisher/cover_image.py turns
that into what each platform needs.

The panels reuse the composer's thumbnail tools (library picker, upload
button), which write ``thumbnail_asset_id``/``thumbnail_url`` into the Alpine
state per account. An account is one platform, so those keys cannot collide
with a YouTube thumbnail. The form field per account is
``<prefix>_cover_asset_id_<account id>``, with ``ig`` or ``fb`` as prefix.
"""

import uuid

COVER_PLATFORMS = ("instagram", "instagram_login", "facebook")

FACEBOOK_FIELD = "fb_cover_asset_id_{acc_id}"
INSTAGRAM_FIELD = "ig_cover_asset_id_{acc_id}"


def parse_cover_asset_id(raw):
    """The asset id from a form field as a string, or None."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def facebook_cover_extra(request, acc_id, extra, has_exactly_one_video):
    """Put the Facebook cover choice into ``extra`` (mutated and returned).

    Called from the Facebook branch of ``_sync_platform_posts``, which only
    runs when the Facebook panel was in the form. The field sits in that panel
    whether or not a video is attached, so an empty or missing value means "no
    cover image" and clears an earlier one. Without exactly one video there is
    nothing to put a cover on, and a stale id must not linger for a later post.
    """
    asset_id = None
    if has_exactly_one_video:
        asset_id = parse_cover_asset_id(request.POST.get(FACEBOOK_FIELD.format(acc_id=acc_id), ""))
    if asset_id:
        extra["cover_asset_id"] = asset_id
    else:
        extra.pop("cover_asset_id", None)
    return extra


def cover_error(asset_id, workspace):
    """Why the chosen cover image cannot be used, or None."""
    from apps.media_library.models import MediaAsset

    asset = (
        MediaAsset.objects.for_workspace_with_shared(workspace.id, workspace.organization_id)
        .filter(id=asset_id)
        .first()
    )
    if asset is None:
        return "the cover image is no longer in the media library. Pick another one or remove it."
    if asset.media_type != MediaAsset.MediaType.IMAGE:
        return "the cover has to be an image."
    return None


def facebook_cover_error(request, workspace, selected_ids):
    """First problem with a Facebook cover image across the selected accounts.

    Instagram covers are judged in ``instagram_placement_error``, because there
    the cover depends on the placement.
    """
    if not selected_ids:
        return None

    from apps.social_accounts.models import SocialAccount

    for account in SocialAccount.objects.filter(id__in=selected_ids, workspace=workspace, platform="facebook"):
        asset_id = parse_cover_asset_id(request.POST.get(FACEBOOK_FIELD.format(acc_id=account.id), ""))
        if not asset_id:
            continue
        message = cover_error(asset_id, workspace)
        if message:
            return f"{account.account_name}: {message}"
    return None


def cover_preview(platform_post_list, workspace):
    """``{account id: (asset id, url)}`` for every PlatformPost with a cover
    image, so the composer can put it back under the thumbnail keys on edit."""
    wanted = {}
    for pp in platform_post_list:
        if pp.social_account.platform not in COVER_PLATFORMS:
            continue
        asset_id = (pp.platform_extra or {}).get("cover_asset_id")
        if asset_id:
            wanted[str(pp.social_account_id)] = str(asset_id)
    if not wanted:
        return {}

    from apps.media_library.models import MediaAsset

    urls = {}
    for asset in MediaAsset.objects.for_workspace_with_shared(workspace.id, workspace.organization_id).filter(
        id__in=set(wanted.values())
    ):
        if asset.thumbnail:
            urls[str(asset.id)] = asset.thumbnail.url
        elif asset.file:
            urls[str(asset.id)] = asset.file.url
    return {acc_id: (asset_id, urls.get(asset_id, "")) for acc_id, asset_id in wanted.items()}
