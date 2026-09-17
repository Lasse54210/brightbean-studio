"""Abandoned multipart uploads get aborted (Blue Monkey Media fork).

The storage client is faked at the same seam the chunked-upload tests use
(``apps.media_library.multipart``), so this runs against the local backend and
still exercises the decision the sweep makes for every open upload: abort
when old and unclaimed, keep when a live upload session names the key, keep
when younger than the threshold, and touch nothing in a dry run.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.media_library import multipart, multipart_sweep
from apps.media_library.models import PendingUpload
from apps.organizations.models import Organization
from apps.workspaces.models import Workspace


class FakePaginator:
    def __init__(self, uploads):
        self.uploads = uploads

    def paginate(self, **kwargs):
        # Two pages, to prove the sweep reads past the first one.
        half = max(1, len(self.uploads) // 2)
        yield {"Uploads": self.uploads[:half]}
        yield {"Uploads": self.uploads[half:]}


class FakeClient:
    def __init__(self, uploads):
        self.uploads = uploads
        self.aborted = []

    def get_paginator(self, name):
        assert name == "list_multipart_uploads"
        return FakePaginator(self.uploads)

    def abort_multipart_upload(self, **kwargs):
        # boto3 keyword spelling, hence no lowercase names here.
        self.aborted.append((kwargs["Key"], kwargs["UploadId"]))


def _upload(key, *, hours_ago, upload_id="u"):
    return {"Key": key, "UploadId": upload_id, "Initiated": timezone.now() - timedelta(hours=hours_ago)}


@pytest.fixture
def fake_client(monkeypatch):
    holder = {}

    def install(uploads):
        client = FakeClient(uploads)
        monkeypatch.setattr(multipart, "_client_and_bucket", lambda: (client, "bucket"))
        # The local backend has no LOCATION prefix to apply; the sweep only
        # needs the two sides to spell a key the same way.
        monkeypatch.setattr(multipart, "_normalize", lambda key: key)
        holder["client"] = client
        return client

    return install


@pytest.fixture
def workspace(db):
    org = Organization.objects.create(name="Org")
    return Workspace.objects.create(organization=org, name="WS")


def _pending(workspace, key, *, expires_in):
    return PendingUpload.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        created_by=None,
        storage_key=key,
        declared_content_type="video/mp4",
        declared_filename=key.rsplit("/", 1)[-1],
        max_bytes=10,
        expires_at=timezone.now() + expires_in,
    )


def test_old_unclaimed_uploads_are_aborted_and_young_ones_kept(fake_client, db):
    client = fake_client(
        [
            _upload("media_library/2026/08/old-a.mp4", hours_ago=30, upload_id="a"),
            _upload("media_library/2026/08/old-b.mp4", hours_ago=200, upload_id="b"),
            _upload("media_library/2026/09/fresh.mp4", hours_ago=2, upload_id="c"),
        ]
    )

    summary = multipart_sweep.abort_stale_multipart_uploads()

    assert client.aborted == [
        ("media_library/2026/08/old-a.mp4", "a"),
        ("media_library/2026/08/old-b.mp4", "b"),
    ]
    assert summary == {"open": 3, "aborted": 2, "kept_live": 0, "kept_young": 1, "dry_run": False}


def test_an_upload_with_a_live_session_is_never_aborted(fake_client, workspace):
    key = "media_library/2026/09/inflight.mp4"
    _pending(workspace, key, expires_in=timedelta(hours=1))
    client = fake_client([_upload(key, hours_ago=48)])

    summary = multipart_sweep.abort_stale_multipart_uploads()

    assert client.aborted == []
    assert summary["kept_live"] == 1


def test_an_expired_session_no_longer_protects_its_upload(fake_client, workspace):
    key = "media_library/2026/09/gaveup.mp4"
    _pending(workspace, key, expires_in=timedelta(hours=-1))
    client = fake_client([_upload(key, hours_ago=48, upload_id="z")])

    multipart_sweep.abort_stale_multipart_uploads()

    assert client.aborted == [(key, "z")]


def test_a_finalized_row_does_not_count_as_live(fake_client, workspace):
    # Finalised means the object exists; an open upload under that key can
    # only be a leftover from a retry and is fair game.
    key = "media_library/2026/09/done.mp4"
    row = _pending(workspace, key, expires_in=timedelta(hours=5))
    row.finalized_at = timezone.now()
    row.save(update_fields=["finalized_at"])
    client = fake_client([_upload(key, hours_ago=48)])

    multipart_sweep.abort_stale_multipart_uploads()

    assert len(client.aborted) == 1


def test_dry_run_reports_but_aborts_nothing(fake_client, db):
    client = fake_client([_upload("media_library/2026/08/old.mp4", hours_ago=72)])
    lines = []

    summary = multipart_sweep.abort_stale_multipart_uploads(dry_run=True, log=lines.append)

    assert client.aborted == []
    assert summary["aborted"] == 1 and summary["dry_run"] is True
    assert any(line.startswith("would abort") for line in lines)


def test_the_threshold_is_adjustable(fake_client, db):
    client = fake_client([_upload("media_library/2026/09/sixhours.mp4", hours_ago=6)])

    multipart_sweep.abort_stale_multipart_uploads(older_than=timedelta(hours=1))

    assert len(client.aborted) == 1


def test_management_command_refuses_politely_without_s3(db):
    out = StringIO()
    call_command("abort_stale_multipart_uploads", "--dry-run", stdout=out)
    assert "not S3" in out.getvalue()


def test_management_command_runs_the_sweep_on_s3(fake_client, db, monkeypatch):
    client = fake_client([_upload("media_library/2026/08/old.mp4", hours_ago=72, upload_id="q")])
    import apps.media_library.management.commands.abort_stale_multipart_uploads as command_module

    monkeypatch.setattr(command_module, "is_s3_backend", lambda: True)
    out = StringIO()

    call_command("abort_stale_multipart_uploads", stdout=out)

    assert client.aborted == [("media_library/2026/08/old.mp4", "q")]
    assert "1 open, 1 aborted" in out.getvalue()
