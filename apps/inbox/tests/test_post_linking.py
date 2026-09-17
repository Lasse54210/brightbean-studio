"""LinkedIn and YouTube comments link to their post, and older ones can catch up.

The grouping in the inbox already read ``post_urn`` and ``video_id``, but the
lookup that fills ``related_post`` did not, so both providers stored every
comment unlinked while the matching ``PlatformPost`` sat in the same database.
"""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.composer.models import PlatformPost, Post
from apps.inbox.models import InboxMessage
from apps.inbox.tasks import _related_post_key, resolve_related_posts
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def workspace(db):
    organization = Organization.objects.create(name="Linking Org")
    return Workspace.objects.create(name="Linking WS", organization=organization)


def _account(workspace, platform, platform_id):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=platform_id,
        account_name=f"{platform} account",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


def _platform_post(account, platform_post_id):
    post = Post.objects.create(workspace=account.workspace, caption="Caption")
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=PlatformPost.Status.PUBLISHED,
        platform_post_id=platform_post_id,
        published_at=timezone.now() - timedelta(days=1),
    )


def _comment(account, message_id, extra, *, related_post=None):
    return InboxMessage.objects.create(
        workspace=account.workspace,
        social_account=account,
        platform_message_id=message_id,
        message_type=InboxMessage.MessageType.COMMENT,
        sender_name="Commenter",
        body="Nice one",
        extra=extra,
        related_post=related_post,
        received_at=timezone.now(),
    )


class _Incoming:
    """What a provider hands the sync engine: it only needs ``extra`` here."""

    def __init__(self, extra):
        self.extra = extra


# ---------------------------------------------------------------- the key


def test_key_reads_linkedin_urn_and_youtube_video_id():
    assert _related_post_key({"post_urn": "urn:li:share:7"}) == "urn:li:share:7"
    assert _related_post_key({"video_id": "dQw4w9WgXcQ"}) == "dQw4w9WgXcQ"


def test_facebook_keys_still_win_over_the_others():
    """A payload carrying both must resolve the stripped Facebook id."""
    extra = {"stored_post_id": "222", "post_id": "111_222", "post_urn": "urn:li:share:7"}
    assert _related_post_key(extra) == "222"


def test_a_message_without_any_post_id_has_no_key():
    assert _related_post_key({}) == ""
    assert _related_post_key(None) == ""


# ------------------------------------------------------- resolving a batch


@pytest.mark.django_db
def test_linkedin_comment_resolves_to_its_platform_post(workspace):
    account = _account(workspace, "linkedin", "li-1")
    platform_post = _platform_post(account, "urn:li:share:7")

    resolved = resolve_related_posts(account, [_Incoming({"post_urn": "urn:li:share:7"})])

    assert resolved == {"urn:li:share:7": platform_post.id}


@pytest.mark.django_db
def test_youtube_comment_resolves_to_its_platform_post(workspace):
    account = _account(workspace, "youtube", "yt-1")
    platform_post = _platform_post(account, "dQw4w9WgXcQ")

    resolved = resolve_related_posts(account, [_Incoming({"video_id": "dQw4w9WgXcQ"})])

    assert resolved == {"dQw4w9WgXcQ": platform_post.id}


@pytest.mark.django_db
def test_another_accounts_post_with_the_same_id_is_not_resolved(workspace):
    """The lookup is scoped to the account, so a shared id cannot cross over."""
    mine = _account(workspace, "linkedin", "li-mine")
    theirs = _account(workspace, "linkedin", "li-theirs")
    _platform_post(theirs, "urn:li:share:7")

    assert resolve_related_posts(mine, [_Incoming({"post_urn": "urn:li:share:7"})]) == {}


# ------------------------------------------------------------- the backfill


@pytest.mark.django_db
def test_backfill_links_what_arrived_before_the_key_was_read(workspace):
    account = _account(workspace, "linkedin", "li-1")
    platform_post = _platform_post(account, "urn:li:share:7")
    comment = _comment(account, "c1", {"post_urn": "urn:li:share:7"})
    assert comment.related_post_id is None

    call_command("link_inbox_posts", stdout=StringIO())

    comment.refresh_from_db()
    assert comment.related_post_id == platform_post.id


@pytest.mark.django_db
def test_backfill_dry_run_changes_nothing(workspace):
    account = _account(workspace, "linkedin", "li-1")
    _platform_post(account, "urn:li:share:7")
    comment = _comment(account, "c1", {"post_urn": "urn:li:share:7"})

    out = StringIO()
    call_command("link_inbox_posts", "--dry-run", stdout=out)

    comment.refresh_from_db()
    assert comment.related_post_id is None
    assert "1 messages would be linked" in out.getvalue()


@pytest.mark.django_db
def test_backfill_leaves_an_existing_link_alone(workspace):
    """It never unlinks: a message already pointing somewhere is not touched."""
    account = _account(workspace, "linkedin", "li-1")
    first = _platform_post(account, "urn:li:share:7")
    second = _platform_post(account, "urn:li:share:8")
    comment = _comment(account, "c1", {"post_urn": "urn:li:share:8"}, related_post=first)

    call_command("link_inbox_posts", stdout=StringIO())

    comment.refresh_from_db()
    assert comment.related_post_id == first.id
    assert comment.related_post_id != second.id


@pytest.mark.django_db
def test_backfill_reports_a_post_this_instance_never_published(workspace):
    """A comment on someone else's post keeps its id and stays unlinked."""
    account = _account(workspace, "linkedin", "li-1")
    comment = _comment(account, "c1", {"post_urn": "urn:li:share:999"})

    out = StringIO()
    call_command("link_inbox_posts", "--dry-run", stdout=out)

    comment.refresh_from_db()
    assert comment.related_post_id is None
    assert "1 carry a post id this instance never published" in out.getvalue()


@pytest.mark.django_db
def test_backfill_can_be_narrowed_to_one_platform(workspace):
    linkedin = _account(workspace, "linkedin", "li-1")
    youtube = _account(workspace, "youtube", "yt-1")
    _platform_post(linkedin, "urn:li:share:7")
    _platform_post(youtube, "dQw4w9WgXcQ")
    li_comment = _comment(linkedin, "c1", {"post_urn": "urn:li:share:7"})
    yt_comment = _comment(youtube, "c2", {"video_id": "dQw4w9WgXcQ"})

    call_command("link_inbox_posts", "--platform", "linkedin", stdout=StringIO())

    li_comment.refresh_from_db()
    yt_comment.refresh_from_db()
    assert li_comment.related_post_id is not None
    assert yt_comment.related_post_id is None
