"""Views for chunked browser uploads. See ``multipart.py`` for the why.

Four endpoints, all workspace-scoped and all behind ``upload_media``:

    start    open a session, get part size, part count and the first URLs
    urls     (re)issue presigned URLs for named parts, for resume and expiry
    status   which parts already arrived, so the client can skip them
    finish   assemble, validate the stored bytes, create the asset

Deliberately a separate module rather than additions to ``views.py``: this is a
downstream fork that rebases onto a moving upstream, and a new file never
conflicts while an edit inside a 900-line module eventually does.
"""

from __future__ import annotations

import json
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from apps.members.decorators import require_permission

from . import multipart
from .models import MediaAsset, MediaFolder, PendingUpload
from .quotas import StorageQuotaExceededError, enforce_storage_quota
from .services import inspect_uploaded_object, register_uploaded_asset
from .storage import delete_object, is_s3_backend
from .tasks import process_media_asset
from .validators import MAX_FILE_SIZES
from .views import _get_workspace_or_404

LOCAL_MODE_MSG = (
    "Chunked upload needs the S3 storage backend; this deployment stores media on "
    "the local filesystem."
)

#: How long a session may stay open before the cleanup task discards it.
SESSION_TTL = timedelta(hours=12)

#: Presigned URLs handed out per request. Enough to keep three or four parallel
#: transfers busy without minting thousands of URLs that will expire unused.
URL_BATCH = 8


def _json_body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return {}


def _guard_s3():
    if not is_s3_backend():
        return JsonResponse({"error": LOCAL_MODE_MSG}, status=409)
    return None


def _pending_or_404(request, workspace, pending_id):
    return get_object_or_404(
        PendingUpload, pk=pending_id, workspace=workspace, organization=workspace.organization
    )


@login_required
@require_permission("upload_media")
@require_POST
def multipart_start(request, workspace_id):
    """Open a chunked upload and return the plan plus the first part URLs."""
    blocked = _guard_s3()
    if blocked:
        return blocked
    workspace = _get_workspace_or_404(request, workspace_id)
    body = _json_body(request)

    filename = str(body.get("filename") or "").strip()
    if not filename:
        return JsonResponse({"error": "filename is required"}, status=400)
    try:
        size = int(body.get("size"))
    except (TypeError, ValueError):
        return JsonResponse({"error": "size must be an integer number of bytes"}, status=400)

    # The declared size only bounds the plan and the quota reservation. The real
    # size is read back from storage at finish; see multipart.py.
    max_bytes = int(max(MAX_FILE_SIZES.values()))
    if size <= 0 or size > max_bytes:
        return JsonResponse({"error": "File is too large."}, status=413)
    try:
        part_count = multipart.plan_part_count(size)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    # Reserve quota up front. Checking only at finish would let ten parallel
    # sessions each pass a check that none of them can honour together.
    try:
        enforce_storage_quota(workspace.organization, size)
    except StorageQuotaExceededError as exc:
        return JsonResponse(
            {"error": f"Storage quota exceeded: used={exc.used} limit={exc.limit} attempted={exc.attempted}"},
            status=413,
        )

    # Server-chosen key: the client never supplies a path, so it cannot traverse
    # or overwrite somebody else's object.
    storage_key = multipart.storage_key_with_name(filename)
    content_type = str(body.get("content_type") or "application/octet-stream")
    upload_id = multipart.start_multipart(storage_key, content_type)

    pending = PendingUpload.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        created_by=request.user,
        storage_key=storage_key,
        declared_content_type=content_type,
        declared_filename=filename,
        max_bytes=size,
        expires_at=timezone.now() + SESSION_TTL,
    )

    first = [
        {"part_number": n, "url": multipart.presign_part(storage_key, upload_id, n)}
        for n in range(1, min(part_count, URL_BATCH) + 1)
    ]
    return JsonResponse(
        {
            "upload_id": str(pending.id),
            "part_size": multipart.PART_SIZE,
            "part_count": part_count,
            "urls": first,
        }
    )


@login_required
@require_permission("upload_media")
@require_POST
def multipart_urls(request, workspace_id, pending_id):
    """Fresh presigned URLs for the named parts."""
    blocked = _guard_s3()
    if blocked:
        return blocked
    workspace = _get_workspace_or_404(request, workspace_id)
    pending = _pending_or_404(request, workspace, pending_id)
    if pending.finalized_at:
        return JsonResponse({"error": "This upload was already finalized."}, status=409)

    upload_id = multipart.find_upload_id(pending.storage_key)
    if not upload_id:
        return JsonResponse({"error": "This upload is no longer open."}, status=409)

    wanted = _json_body(request).get("parts") or []
    try:
        numbers = sorted({int(n) for n in wanted})[:URL_BATCH]
    except (TypeError, ValueError):
        return JsonResponse({"error": "parts must be a list of part numbers"}, status=400)
    if not numbers:
        return JsonResponse({"error": "parts must not be empty"}, status=400)

    count = multipart.plan_part_count(int(pending.max_bytes))
    if numbers[0] < 1 or numbers[-1] > count:
        return JsonResponse({"error": f"part numbers must be within 1..{count}"}, status=400)

    return JsonResponse(
        {
            "urls": [
                {"part_number": n, "url": multipart.presign_part(pending.storage_key, upload_id, n)}
                for n in numbers
            ]
        }
    )


@login_required
@require_permission("upload_media")
@require_GET
def multipart_status(request, workspace_id, pending_id):
    """Which parts already arrived, so an interrupted upload can resume."""
    blocked = _guard_s3()
    if blocked:
        return blocked
    workspace = _get_workspace_or_404(request, workspace_id)
    pending = _pending_or_404(request, workspace, pending_id)

    upload_id = multipart.find_upload_id(pending.storage_key)
    if not upload_id:
        return JsonResponse({"received": [], "open": False})
    parts = multipart.list_parts(pending.storage_key, upload_id)
    return JsonResponse({"received": [p["part_number"] for p in parts], "open": True})


@login_required
@require_permission("upload_media")
@require_POST
def multipart_finish(request, workspace_id, pending_id):
    """Assemble the parts, validate the stored bytes, create the asset.

    Mirrors the MCP ``finalize_media_upload`` flow on purpose: inspect outside the
    row lock so the lock never spans remote calls, then create and mark finalized
    under a short lock, re-checking idempotency so a racing finish still wins.
    """
    blocked = _guard_s3()
    if blocked:
        return blocked
    workspace = _get_workspace_or_404(request, workspace_id)
    pending = _pending_or_404(request, workspace, pending_id)
    body = _json_body(request)

    if pending.finalized_at:
        if pending.media_asset_id is None:
            return JsonResponse(
                {"error": "This upload was already finalized; its media asset no longer exists."},
                status=409,
            )
        return JsonResponse({"asset_id": str(pending.media_asset_id)})

    if pending.expires_at < timezone.now():
        return JsonResponse({"error": "This upload expired; start a new one."}, status=410)

    folder = None
    folder_id = body.get("folder_id")
    if folder_id:
        folder = get_object_or_404(MediaFolder, pk=folder_id, workspace=workspace)

    upload_id = multipart.find_upload_id(pending.storage_key)
    if not upload_id:
        return JsonResponse({"error": "This upload is no longer open."}, status=409)

    parts = multipart.list_parts(pending.storage_key, upload_id)
    expected = multipart.plan_part_count(int(pending.max_bytes))
    if len(parts) != expected:
        missing = sorted(set(range(1, expected + 1)) - {p["part_number"] for p in parts})
        return JsonResponse(
            {"error": f"Not all parts arrived; missing {missing[:20]}."}, status=409
        )

    multipart.complete_multipart(pending.storage_key, upload_id, parts)

    # From here the object exists. Anything that rejects it has to remove it as
    # well, or it stays in the bucket with nothing referring to it.
    try:
        inspected = inspect_uploaded_object(pending)
    except FileNotFoundError:
        return JsonResponse({"error": "The uploaded object could not be read."}, status=502)
    except StorageQuotaExceededError as exc:
        delete_object(pending.storage_key)
        return JsonResponse(
            {"error": f"Storage quota exceeded: used={exc.used} limit={exc.limit} attempted={exc.attempted}"},
            status=413,
        )
    except ValidationError as exc:
        delete_object(pending.storage_key)
        return JsonResponse(
            {"error": "; ".join(getattr(exc, "messages", [str(exc)]))}, status=415
        )

    # The declared size is the client's word; the stored size is a fact. They must
    # match exactly. This is what closes the gap left by not being able to bound
    # each individual part on a direct upload.
    if int(inspected["size"]) != int(pending.max_bytes):
        delete_object(pending.storage_key)
        return JsonResponse(
            {
                "error": (
                    f"Size mismatch: declared {pending.max_bytes}, stored {inspected['size']}."
                )
            },
            status=400,
        )

    with transaction.atomic():
        locked = PendingUpload.objects.select_for_update().get(
            id=pending.id, workspace_id=workspace.id
        )
        if locked.finalized_at and locked.media_asset_id:
            asset = locked.media_asset
        else:
            asset = register_uploaded_asset(
                pending=locked,
                inspected=inspected,
                uploaded_by=request.user,
                folder=folder,
                alt_text=str(body.get("alt_text") or ""),
                title=str(body.get("title") or ""),
                tags=body.get("tags") or None,
            )
            locked.finalized_at = timezone.now()
            locked.media_asset = asset
            locked.save(update_fields=["finalized_at", "media_asset"])

    assert asset is not None
    if asset.processing_status == MediaAsset.ProcessingStatus.PENDING:
        process_media_asset(str(asset.id))
    return JsonResponse({"asset_id": str(asset.id), "filename": asset.filename})
