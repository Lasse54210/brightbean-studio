"""Tests for the chunked (S3 multipart) upload path.

Same approach as ``test_presigned_upload.py``: the bucket lives behind
``apps.media_library.multipart``, so we monkeypatch that seam and exercise the
planning rules and the finish flow without a live bucket. The two things worth
covering are the part plan (an off-by-one there means a truncated file) and that
finish keeps trusting the stored bytes over anything the client declared.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.media_library import multipart
from apps.media_library import storage as ml_storage
from apps.media_library.models import PendingUpload
from apps.media_library.validators import MAX_FILE_SIZES
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace

PART = multipart.PART_SIZE


def _make_pending(*, size, filename="clip.mp4"):
    org = Organization.objects.create(name="Org")
    ws = Workspace.objects.create(organization=org, name="WS")
    return PendingUpload.objects.create(
        organization=org,
        workspace=ws,
        created_by=None,
        storage_key=ml_storage.generate_storage_key(filename),
        declared_content_type="video/mp4",
        declared_filename=filename,
        max_bytes=size,
        expires_at=timezone.now() + timedelta(hours=1),
    )


def test_plan_part_count_rounds_up():
    assert multipart.plan_part_count(1) == 1
    assert multipart.plan_part_count(PART) == 1
    assert multipart.plan_part_count(PART + 1) == 2
    assert multipart.plan_part_count(PART * 3) == 3


def test_plan_part_count_rejects_impossible_sizes():
    for bad in (0, -1):
        with pytest.raises(ValueError):
            multipart.plan_part_count(bad)


def test_plan_part_count_rejects_more_parts_than_s3_allows():
    with pytest.raises(ValueError, match="too many parts"):
        multipart.plan_part_count((multipart.MAX_PARTS + 1) * PART)


def test_only_the_last_part_is_smaller():
    total = PART * 2 + 1234
    assert multipart.expected_part_size(1, total) == PART
    assert multipart.expected_part_size(2, total) == PART
    assert multipart.expected_part_size(3, total) == 1234


def test_part_sizes_sum_to_the_file():
    # The property the strict size check at finish leans on: if every part has
    # the size it should, the total cannot come out different.
    total = PART * 4 + 7
    parts = range(1, multipart.plan_part_count(total) + 1)
    assert sum(multipart.expected_part_size(n, total) for n in parts) == total


def test_expected_part_size_rejects_out_of_range_numbers():
    for bad in (0, 2):
        with pytest.raises(ValueError):
            multipart.expected_part_size(bad, PART)


@pytest.mark.django_db
def test_find_upload_id_matches_the_exact_key(monkeypatch):
    # The UploadId is looked up rather than stored, which is what keeps this
    # feature free of a migration. It must match the whole key and not just the
    # prefix, or a session could pick up a sibling object's upload.
    pending = _make_pending(size=PART + 10)
    normalized = pending.storage_key

    class FakePaginator:
        def paginate(self, **kwargs):
            return [
                {
                    "Uploads": [
                        {"Key": normalized + "-other", "UploadId": "wrong"},
                        {"Key": normalized, "UploadId": "right"},
                    ]
                }
            ]

    class FakeClient:
        def get_paginator(self, name):
            assert name == "list_multipart_uploads"
            return FakePaginator()

    # ``_normalize`` too: it calls ``default_storage._normalize_name``, which only
    # exists on the S3 backend, and the test settings use the local filesystem.
    # Production never hits that because every view guards on ``is_s3_backend``.
    monkeypatch.setattr(multipart, "_normalize", lambda key: key)
    monkeypatch.setattr(multipart, "_client_and_bucket", lambda: (FakeClient(), "bucket"))
    assert multipart.find_upload_id(pending.storage_key) == "right"


@pytest.mark.django_db
def test_find_upload_id_returns_none_without_an_open_upload(monkeypatch):
    pending = _make_pending(size=PART + 10)

    class FakePaginator:
        def paginate(self, **kwargs):
            return [{"Uploads": []}]

    class FakeClient:
        def get_paginator(self, name):
            return FakePaginator()

    # ``_normalize`` too: it calls ``default_storage._normalize_name``, which only
    # exists on the S3 backend, and the test settings use the local filesystem.
    # Production never hits that because every view guards on ``is_s3_backend``.
    monkeypatch.setattr(multipart, "_normalize", lambda key: key)
    monkeypatch.setattr(multipart, "_client_and_bucket", lambda: (FakeClient(), "bucket"))
    assert multipart.find_upload_id(pending.storage_key) is None


@pytest.mark.django_db
def test_complete_uses_the_buckets_etags_not_the_clients(monkeypatch):
    # The ETags that go into CompleteMultipartUpload come from ListParts, so a
    # client cannot influence which bytes get assembled.
    pending = _make_pending(size=PART + 10)
    seen = {}

    class FakeClient:
        def complete_multipart_upload(self, **kwargs):
            seen.update(kwargs)

    # ``_normalize`` too: it calls ``default_storage._normalize_name``, which only
    # exists on the S3 backend, and the test settings use the local filesystem.
    # Production never hits that because every view guards on ``is_s3_backend``.
    monkeypatch.setattr(multipart, "_normalize", lambda key: key)
    monkeypatch.setattr(multipart, "_client_and_bucket", lambda: (FakeClient(), "bucket"))
    multipart.complete_multipart(
        pending.storage_key,
        "upload-1",
        [
            {"part_number": 2, "etag": '"b"', "size": 10},
            {"part_number": 1, "etag": '"a"', "size": PART},
        ],
    )
    # Sorted by part number, because S3 requires that order.
    assert seen["MultipartUpload"]["Parts"] == [
        {"PartNumber": 1, "ETag": '"a"'},
        {"PartNumber": 2, "ETag": '"b"'},
    ]


def test_storage_key_keeps_a_readable_name_without_becoming_unsafe():
    # The download redirects to the object URL, and with a custom storage domain
    # that URL is unsigned, so the last path segment is the filename the browser
    # saves. A bare UUID makes every download unrecognisable, hence the name. It
    # must not cost any of the safety of generate_storage_key.
    key = multipart.storage_key_with_name("Zomercampagne Aflevering 2.mp4")
    assert key.startswith("media_library/")
    assert key.endswith("-Zomercampagne-Aflevering-2.mp4")

    # Traversal and a smuggled second extension cannot survive.
    traversal = multipart.storage_key_with_name("../../etc/passwd")
    assert ".." not in traversal
    assert traversal.startswith("media_library/")

    double = multipart.storage_key_with_name("evil.mp4.exe")
    assert not double.endswith(".exe")

    # A name that sanitizes to nothing still yields a usable key.
    empty = multipart.storage_key_with_name("...")
    assert empty.startswith("media_library/")
    assert not empty.endswith("-")

    # Two uploads of the same filename never collide.
    a = multipart.storage_key_with_name("clip.mp4")
    b = multipart.storage_key_with_name("clip.mp4")
    assert a != b


def test_max_file_size_needs_more_than_one_part():
    # A guard against the feature quietly becoming pointless: if the largest
    # allowed file fits in a single part, nothing is ever chunked.
    assert max(MAX_FILE_SIZES.values()) > PART
