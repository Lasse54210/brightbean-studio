"""Which files a PlatformPost publishes with.

New file (Blue Monkey Media fork). Until now every account on a post published
the same attachments, in the same order. That is fine for a caption and wrong
for a video company: the Story wants the 9:16 cut and the feed wants the 4:5,
and those are two different files made in the edit, not one file cropped by
Instagram.

``PlatformPost.platform_specific_media`` has existed upstream for exactly this
("JSON list of media asset IDs with platform-specific ordering/cropping") and
was written by nobody and read only by the orphan sweep. The composer now
writes it per Instagram account, and this module is the one place that turns
it back into attachments for the publisher.

The rules are short. A list of asset ids on the PlatformPost wins, in list
order (that order is the carousel order). An id that no longer resolves is
skipped rather than fatal: a file deleted from the library must not sink the
publish when the post's own attachments are still there. When nothing on the
list resolves, or there is no list, the post's attachments are used exactly as
before, so every existing post behaves as it did.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SelectedMedia:
    """What the engine reads off an attachment: the asset, and the alt text
    the post has for it if the same file is also attached to the post."""

    media_asset: object
    alt_text: str = ""
    position: int = 0


def selected_asset_ids(platform_post) -> list[str]:
    """The ids stored on the PlatformPost, as strings, or an empty list."""
    stored = platform_post.platform_specific_media
    if not isinstance(stored, list):
        return []
    return [str(value) for value in stored if value]


def resolve_attachments(platform_post) -> list:
    """The attachments this PlatformPost publishes with, in order.

    Returns the post's own ``PostMedia`` rows (the upstream behaviour) unless
    the PlatformPost carries a media list of its own that resolves to at least
    one asset. Both shapes expose ``media_asset``, which is all the engine
    reads.
    """
    post_attachments = list(platform_post.post.media_attachments.select_related("media_asset").order_by("position"))

    wanted = selected_asset_ids(platform_post)
    if not wanted:
        return post_attachments

    from apps.media_library.models import MediaAsset

    workspace = platform_post.post.workspace
    assets = {
        str(asset.id): asset
        for asset in MediaAsset.objects.for_workspace_with_shared(workspace.id, workspace.organization_id).filter(
            id__in=wanted
        )
    }
    alt_texts = {str(pm.media_asset_id): pm.alt_text for pm in post_attachments}

    chosen = []
    for position, asset_id in enumerate(wanted):
        asset = assets.get(asset_id)
        if asset is None:
            continue
        chosen.append(SelectedMedia(media_asset=asset, alt_text=alt_texts.get(asset_id, ""), position=position))

    return chosen or post_attachments
