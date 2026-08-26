"""Chunked (S3 multipart) direct uploads from the browser.

Why this exists: a reverse proxy in front of the app can cap the size of a single
request body. Cloudflare does, at 100 MB on its Free and Pro plans, and it
answers with a 413 the application never sees. The media library posts each file
as one request, so every upload above that cap fails no matter what
``MEDIA_LIBRARY_MAX_VIDEO_SIZE`` allows (1 GB by default).

The cap is per request, not per file. An S3 multipart upload splits the file into
parts and sends each part as its own request, so the limit stops applying. What
has to change is *where* the splitting happens: the browser, not the server.

This module is the storage side of that. It deliberately holds no state of its
own: a chunked upload reuses the existing ``PendingUpload`` row, and the S3
``UploadId`` is recovered from the bucket with ``list_multipart_uploads`` instead
of being stored. That keeps the feature free of a migration, which matters for a
downstream fork that has to rebase onto a moving upstream.

Validation is unchanged and stays where it already was: ``inspect_uploaded_object``
re-derives size and MIME from the stored bytes at finalize time. The client
supplies bytes directly, so nothing it claims is trusted.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.utils import timezone

from .storage import _client_and_bucket, _normalize
from .validators import ALL_ALLOWED_EXTENSIONS

#: Bytes per part. 16 MiB sits well under the 100 MB proxy cap and keeps the
#: request count low. S3 requires at least 5 MiB for every part except the last,
#: so do not lower this below 5 MiB without revisiting that rule.
PART_SIZE = 16 * 1024 * 1024

#: Hard S3 limit. At 16 MiB per part that is roughly 160 GB.
MAX_PARTS = 10_000


def _presign_expiry() -> int:
    """Seconds a presigned part URL stays valid.

    This does not bound the upload: a long transfer asks for fresh URLs for the
    parts it has left.
    """
    return int(getattr(settings, "MEDIA_LIBRARY_PRESIGN_EXPIRE", 900))


def plan_part_count(size: int, part_size: int = PART_SIZE) -> int:
    """Number of parts a file of ``size`` bytes is split into."""
    if not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive integer")
    count = (size + part_size - 1) // part_size
    if count > MAX_PARTS:
        raise ValueError(f"too many parts ({count}); the maximum is {MAX_PARTS}")
    return count


def expected_part_size(part_number: int, total_size: int, part_size: int = PART_SIZE) -> int:
    """Byte count part ``part_number`` must have.

    Only the last part may be smaller. The server cannot enforce this on a direct
    upload, but finalize compares the assembled size against the declared size,
    so any deviation fails there.
    """
    count = plan_part_count(total_size, part_size)
    if part_number < 1 or part_number > count:
        raise ValueError(f"part number {part_number} is outside 1..{count}")
    if part_number < count:
        return part_size
    return total_size - (count - 1) * part_size


def storage_key_with_name(declared_filename: str) -> str:
    """Like ``generate_storage_key``, but keeps a readable basename.

    Same safety: a fresh UUID leads the basename, so nothing the client sends can
    traverse a directory or collide with another object, and the extension is only
    kept when it is in the allowlist.

    The reason to keep the original name at all is the download side. Downloads
    redirect to the object URL, and with a custom storage domain that URL is not
    signed, so there is no place to put a ``Content-Disposition``. The last path
    segment is then the filename the browser saves, and a bare UUID makes every
    downloaded file unrecognisable.
    """
    ext = ""
    stem = declared_filename
    if "." in declared_filename:
        candidate = declared_filename.rsplit(".", 1)[-1].lower()
        if candidate in ALL_ALLOWED_EXTENSIONS:
            ext = f".{candidate}"
            stem = declared_filename.rsplit(".", 1)[0]

    # Only letters, digits, dash and underscore survive. Dots are dropped on
    # purpose: no "..", and no second extension smuggled into the name.
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in stem)
    safe = "-".join(part for part in safe.split("-") if part)[:60].strip("-")

    now = timezone.now()
    prefix = f"media_library/{now:%Y/%m}/{uuid.uuid4().hex}"
    return f"{prefix}-{safe}{ext}" if safe else f"{prefix}{ext}"


def start_multipart(storage_key: str, content_type: str) -> str:
    """Open a multipart upload for a server-chosen key; returns the UploadId."""
    client, bucket = _client_and_bucket()
    resp = client.create_multipart_upload(
        Bucket=bucket,
        Key=_normalize(storage_key),
        ContentType=content_type or "application/octet-stream",
    )
    return str(resp["UploadId"])


def find_upload_id(storage_key: str) -> str | None:
    """The open UploadId for this key, or ``None`` if there is no open upload.

    Looked up rather than stored: ``PendingUpload.storage_key`` is unique, so at
    most one open upload can belong to a row. This is what lets the feature skip
    a migration.
    """
    client, bucket = _client_and_bucket()
    key = _normalize(storage_key)
    paginator = client.get_paginator("list_multipart_uploads")
    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        for upload in page.get("Uploads", []) or []:
            if upload.get("Key") == key:
                return str(upload["UploadId"])
    return None


def presign_part(storage_key: str, upload_id: str, part_number: int) -> str:
    """Presigned PUT URL for one part.

    The URL pins bucket, key, upload and part number, so a client holding it can
    only write the one part it was issued for.
    """
    client, bucket = _client_and_bucket()
    return str(
        client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": bucket,
                "Key": _normalize(storage_key),
                "UploadId": upload_id,
                "PartNumber": int(part_number),
            },
            ExpiresIn=_presign_expiry(),
        )
    )


def list_parts(storage_key: str, upload_id: str) -> list[dict]:
    """Parts already stored, sorted by part number.

    This is what makes resuming cheap: the client asks what arrived and sends
    only the rest. Without it, a dropped connection on a large file means
    starting over, which on a poor connection means never finishing.
    """
    client, bucket = _client_and_bucket()
    parts: list[dict] = []
    paginator = client.get_paginator("list_parts")
    for page in paginator.paginate(
        Bucket=bucket, Key=_normalize(storage_key), UploadId=upload_id
    ):
        for part in page.get("Parts", []) or []:
            parts.append(
                {
                    "part_number": int(part["PartNumber"]),
                    "etag": str(part["ETag"]),
                    "size": int(part.get("Size", 0)),
                }
            )
    parts.sort(key=lambda p: p["part_number"])
    return parts


def complete_multipart(storage_key: str, upload_id: str, parts: list[dict]) -> None:
    """Assemble the parts into the final object.

    ``parts`` comes from :func:`list_parts`, never from the client: the ETags the
    bucket reports are the ones that count.
    """
    client, bucket = _client_and_bucket()
    client.complete_multipart_upload(
        Bucket=bucket,
        Key=_normalize(storage_key),
        UploadId=upload_id,
        MultipartUpload={
            "Parts": [
                {"PartNumber": p["part_number"], "ETag": p["etag"]}
                for p in sorted(parts, key=lambda p: p["part_number"])
            ]
        },
    )


def abort_multipart(storage_key: str, upload_id: str) -> None:
    """Discard an unfinished upload and the parts already stored.

    This has to actually happen. An abandoned multipart upload keeps its parts,
    they do not show up in a normal bucket listing, and they still occupy space
    and land in backups.
    """
    client, bucket = _client_and_bucket()
    client.abort_multipart_upload(
        Bucket=bucket, Key=_normalize(storage_key), UploadId=upload_id
    )
