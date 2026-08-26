"""Chunked upload finish for the composer.

The composer has its own upload endpoint (``upload_media``) that takes the file
in one request, creates a ``MediaAsset`` and answers with a rendered partial. One
request means the proxy's body cap applies, so a large video never arrives: with
Cloudflare in front the connection is simply reset, which in the browser looks
like a progress bar that fills and then goes red.

The parts themselves go through the media library's chunked endpoints, which are
workspace-scoped and know nothing about posts. Only the last step differs: the
composer has to attach the asset and answer with the same HTML and the same
headers as ``upload_media``, so the existing front-end can treat both the same
way.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.media_library.models import PendingUpload
from apps.media_library.multipart_views import complete_and_register
from apps.media_library.storage import is_s3_backend

from .models import Post
from .views import _attach_asset_for_composer, _get_workspace, _revert_approved_to_review


@login_required
@require_POST
def multipart_finish(request, workspace_id, pending_id=None, post_id=None):
    """Finish a chunked composer upload: create the asset and attach it.

    Mirrors the tail of ``upload_media`` exactly, including the two response
    headers the composer reads to set a YouTube thumbnail. Anything that differs
    here would show up as a thumbnail that does not appear, which is a confusing
    way to find out.
    """
    if not is_s3_backend():
        return JsonResponse(
            {"error": "Chunked upload needs the S3 storage backend."}, status=409
        )

    workspace = _get_workspace(request, workspace_id)
    pending = get_object_or_404(
        PendingUpload, pk=pending_id, workspace=workspace, organization=workspace.organization
    )

    if pending.finalized_at and pending.media_asset_id is None:
        return JsonResponse(
            {"error": "This upload was already finalized; its media asset no longer exists."},
            status=409,
        )
    if not pending.finalized_at and pending.expires_at < timezone.now():
        return JsonResponse({"error": "This upload expired; start a new one."}, status=410)

    if pending.finalized_at:
        # Idempotent replay: reuse the asset instead of minting a second one.
        asset = pending.media_asset
    else:
        asset, fout = complete_and_register(request, workspace, pending)
        if fout is not None:
            return fout

    try:
        body = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        body = {}
    post_id = post_id or body.get("post_id")

    if post_id:
        post = get_object_or_404(Post, id=post_id, workspace=workspace)
        attachment = _attach_asset_for_composer(request, workspace, asset, post)
        # Same rule as the single-request path: changing media on an approved post
        # sends it back for re-approval.
        _revert_approved_to_review(post)
        response = render(
            request,
            "composer/partials/media_list.html",
            {
                "media_attachments": [attachment],
                "post": post,
                "workspace": workspace,
            },
        )
    else:
        _attach_asset_for_composer(request, workspace, asset)
        response = render(
            request,
            "composer/partials/media_list_pending.html",
            {
                "pending_assets": [asset],
                "workspace": workspace,
            },
        )

    response["X-Uploaded-Asset-Id"] = str(asset.id)
    response["X-Uploaded-Asset-Url"] = asset.file.url
    return response
