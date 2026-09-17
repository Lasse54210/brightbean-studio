"""Abort multipart uploads that nobody is going to finish.

New file (Blue Monkey Media fork), companion to ``multipart.py``.

An S3 multipart upload that is started and never completed or aborted keeps
every part it received. Those parts do not appear in a normal bucket listing,
they are not objects, no ``MediaAsset`` points at them, and yet they occupy
space and travel along in every bucket backup. A browser tab closed halfway
through a 2 GB video leaves 2 GB behind, silently, forever.

Upstream's ``sweep_pending_uploads`` deletes the expired ``PendingUpload`` row
and calls ``delete_object`` on its key, which does nothing for an upload that
never became an object. The parts stay. This module is the missing half: it
lists the open uploads in the bucket and aborts the ones that are both old and
unclaimed.

"Unclaimed" means no live ``PendingUpload`` row (not finalised, not expired)
names the key. "Old" is ``STALE_AFTER``, and it is deliberately longer than the
12-hour upload session (``multipart_views.SESSION_TTL``): an upload that is
still inside its session is left alone even if no row is found for it, so a
transfer in flight is never cut off by a sweep running at the wrong moment.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from . import multipart

#: An open upload older than this, with no live PendingUpload row, is aborted.
STALE_AFTER = timedelta(hours=24)


def list_open_uploads() -> list[dict]:
    """Every multipart upload the bucket currently has open.

    Each entry: ``{"key", "upload_id", "initiated"}``. ``initiated`` is what the
    storage reports and is timezone-aware.
    """
    client, bucket = multipart._client_and_bucket()
    paginator = client.get_paginator("list_multipart_uploads")
    found = []
    for page in paginator.paginate(Bucket=bucket):
        for upload in page.get("Uploads", []) or []:
            found.append(
                {
                    "key": str(upload.get("Key", "")),
                    "upload_id": str(upload.get("UploadId", "")),
                    "initiated": _aware(upload.get("Initiated")),
                }
            )
    return found


def _aware(value) -> datetime | None:
    if value is None:
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.utc)
    return value


def _live_keys() -> set[str]:
    """Storage keys an unfinished but still valid upload session claims."""
    from .models import PendingUpload

    rows = PendingUpload.objects.filter(finalized_at__isnull=True, expires_at__gt=timezone.now())
    return {multipart._normalize(key) for key in rows.values_list("storage_key", flat=True)}


def abort_stale_multipart_uploads(*, older_than: timedelta = STALE_AFTER, dry_run: bool = False, log=None) -> dict:
    """Abort every open upload that is older than ``older_than`` and unclaimed.

    Returns a summary: how many uploads were open, how many were aborted (or
    would be, with ``dry_run``), and how many were kept and why. ``log`` is an
    optional callable for one line per decision; the management command hands
    it ``self.stdout.write`` and the background task hands it the logger.
    """
    log = log or (lambda message: None)
    cutoff = timezone.now() - older_than
    live = _live_keys()

    summary = {"open": 0, "aborted": 0, "kept_live": 0, "kept_young": 0, "dry_run": dry_run}
    for upload in list_open_uploads():
        summary["open"] += 1
        key, upload_id, initiated = upload["key"], upload["upload_id"], upload["initiated"]
        if key in live:
            summary["kept_live"] += 1
            log(f"keep  {key}  (upload session still valid)")
            continue
        if initiated is None or initiated > cutoff:
            summary["kept_young"] += 1
            log(f"keep  {key}  (started {initiated:%Y-%m-%d %H:%M} UTC, younger than {older_than})")
            continue
        verb = "would abort" if dry_run else "abort"
        log(f"{verb}  {key}  (started {initiated:%Y-%m-%d %H:%M} UTC, upload {upload_id})")
        if not dry_run:
            multipart.abort_multipart(key, upload_id)
        summary["aborted"] += 1
    return summary
