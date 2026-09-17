"""Which post a message hangs off, in the data and on the page.

The complaint this answers: every comment landed in one undifferentiated feed
with no way to see, or filter on, the post it was written under. The link was
already in the database (``InboxMessage.related_post``); nothing showed it.
"""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.inbox.models import InboxMessage
from apps.inbox.post_reference import filter_by_post, post_key, reference_for
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


@pytest.fixture
def workspace(db):
    organization = Organization.objects.create(name="Post Ref Org")
    return Workspace.objects.create(name="Post Ref WS", organization=organization)


@pytest.fixture
def account(db, workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-1",
        account_name="Test Account",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


@pytest.fixture
def member(db, workspace):
    user = User.objects.create_user(email="member@example.com", password="pw", tos_accepted_at=timezone.now())
    OrgMembership.objects.create(user=user, organization=workspace.organization, org_role=OrgMembership.OrgRole.OWNER)
    WorkspaceMembership.objects.create(
        user=user, workspace=workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
    )
    return user


def _platform_post(account, *, caption, platform_post_id):
    post = Post.objects.create(workspace=account.workspace, caption=caption)
    return PlatformPost.objects.create(
        post=post,
        social_account=account,
        status=PlatformPost.Status.PUBLISHED,
        platform_post_id=platform_post_id,
        published_at=timezone.now() - timedelta(days=1),
    )


def _comment(account, *, message_id, extra, related_post=None, body="Nice one", minutes_ago=5):
    return InboxMessage.objects.create(
        workspace=account.workspace,
        social_account=account,
        platform_message_id=message_id,
        message_type=InboxMessage.MessageType.COMMENT,
        sender_name="Commenter",
        body=body,
        extra=extra,
        related_post=related_post,
        received_at=timezone.now() - timedelta(minutes=minutes_ago),
    )


# --- The key ---


def test_the_stripped_id_wins_over_the_prefixed_one_facebook_stores_beside_it():
    # Facebook keeps both; only the stripped form matches PlatformPost.
    assert post_key({"post_id": "page-1_post-9", "stored_post_id": "post-9"}) == "post-9"


def test_youtube_and_linkedin_names_for_the_same_thing_are_read_too():
    assert post_key({"video_id": "vid-3"}) == "vid-3"
    assert post_key({"post_urn": "urn:li:share:7"}) == "urn:li:share:7"


def test_a_message_without_a_post_has_no_key():
    assert post_key({}) == ""
    assert post_key(None) == ""


# --- The reference ---


@pytest.mark.django_db
def test_a_dm_gets_no_post_reference(account):
    dm = InboxMessage.objects.create(
        workspace=account.workspace,
        social_account=account,
        platform_message_id="dm-1",
        message_type=InboxMessage.MessageType.DM,
        sender_name="Someone",
        body="Hi",
        received_at=timezone.now(),
    )
    assert reference_for(dm) is None


@pytest.mark.django_db
def test_a_linked_comment_is_labelled_with_the_caption_and_links_to_the_composer(account):
    platform_post = _platform_post(account, caption="Spring campaign, day one", platform_post_id="media-1")
    message = _comment(
        account,
        message_id="c-1",
        extra={"stored_post_id": "media-1", "post_permalink_url": "https://instagram.com/p/abc/"},
        related_post=platform_post,
    )

    ref = reference_for(message)

    assert ref.is_linked
    assert ref.label == "Spring campaign, day one"
    assert ref.permalink == "https://instagram.com/p/abc/"
    assert ref.post_id == platform_post.post_id
    assert ref.published_at == platform_post.published_at


@pytest.mark.django_db
def test_an_unlinked_comment_shows_the_id_and_claims_nothing_about_where_the_post_came_from(account):
    # Unlinked does not mean "published outside Brightbean": a YouTube comment
    # is unlinked because nothing resolves video_id to a PlatformPost yet.
    message = _comment(account, message_id="c-2", extra={"video_id": "abcdefghijklmnop"})

    ref = reference_for(message)

    assert not ref.is_linked
    assert ref.key == "abcdefghijklmnop"
    assert ref.label == "Post efghijklmnop"
    assert ref.permalink == ""
    assert ref.post_id is None


@pytest.mark.django_db
def test_the_label_is_the_first_line_of_the_caption_and_stays_short(account):
    platform_post = _platform_post(
        account,
        caption="\n\nThe headline everybody reads and then some more words nobody sees\nSecond line",
        platform_post_id="media-2",
    )
    message = _comment(account, message_id="c-3", extra={"stored_post_id": "media-2"}, related_post=platform_post)

    label = reference_for(message).label

    assert label.startswith("The headline everybody reads")
    assert "Second line" not in label
    assert len(label) <= 48


@pytest.mark.django_db
def test_a_caption_less_post_still_gets_a_reference(account):
    platform_post = _platform_post(account, caption="", platform_post_id="media-3")
    message = _comment(account, message_id="c-4", extra={"stored_post_id": "media-3"}, related_post=platform_post)

    ref = reference_for(message)

    assert ref.is_linked
    assert ref.label == "Post media-3"


# --- The filter ---


@pytest.mark.django_db
def test_filtering_on_the_stripped_id_also_finds_a_message_stored_under_the_prefixed_one(account):
    # Two Facebook paths, two spellings of the same post. A filter that reads
    # only one key silently halves the thread.
    _comment(account, message_id="fb-1", extra={"stored_post_id": "post-9", "post_id": "page-1_post-9"})
    _comment(account, message_id="fb-2", extra={"post_id": "post-9"})
    _comment(account, message_id="other", extra={"stored_post_id": "post-8"})

    found = filter_by_post(InboxMessage.objects.all(), "post-9")

    assert {m.platform_message_id for m in found} == {"fb-1", "fb-2"}


@pytest.mark.django_db
def test_an_empty_key_filters_nothing_away(account):
    _comment(account, message_id="c-5", extra={})

    assert filter_by_post(InboxMessage.objects.all(), "").count() == 1


# --- The feed ---


@pytest.mark.django_db
def test_the_feed_names_the_post_each_comment_hangs_off(client, account, member):
    platform_post = _platform_post(account, caption="Spring campaign, day one", platform_post_id="media-1")
    _comment(account, message_id="c-1", extra={"stored_post_id": "media-1"}, related_post=platform_post)
    client.force_login(member)

    response = client.get(reverse("inbox:feed", kwargs={"workspace_id": account.workspace_id}))

    assert response.status_code == 200
    assert "Spring campaign, day one" in response.content.decode()


@pytest.mark.django_db
def test_the_feed_can_be_narrowed_to_one_post(client, account, member):
    wanted = _platform_post(account, caption="The one we are reading", platform_post_id="media-1")
    other = _platform_post(account, caption="Some other post", platform_post_id="media-2")
    _comment(account, message_id="c-1", extra={"stored_post_id": "media-1"}, related_post=wanted, body="First")
    _comment(account, message_id="c-2", extra={"stored_post_id": "media-1"}, related_post=wanted, body="Second")
    _comment(account, message_id="c-3", extra={"stored_post_id": "media-2"}, related_post=other, body="Elsewhere")
    client.force_login(member)

    url = reverse("inbox:feed", kwargs={"workspace_id": account.workspace_id})
    response = client.get(url, {"post": "media-1"})
    page = response.content.decode()

    assert [m.platform_message_id for m in response.context["inbox_messages"]] == ["c-2", "c-1"]
    assert response.context["post_filter"].label == "The one we are reading"
    assert response.context["post_filter_count"] == 2
    assert "Elsewhere" not in page
    # The hidden input is what keeps the post filter alive when another filter
    # fires its HTMX request; without it, picking a status drops back to the
    # full feed.
    assert 'name="post" value="media-1"' in page


@pytest.mark.django_db
def test_a_post_filter_that_matches_nothing_still_names_itself(client, account, member):
    client.force_login(member)

    url = reverse("inbox:feed", kwargs={"workspace_id": account.workspace_id})
    response = client.get(url, {"post": "media-gone"})

    assert response.status_code == 200
    assert response.context["post_filter"].key == "media-gone"
    assert response.context["post_filter_count"] == 0


@pytest.mark.django_db
def test_the_post_filter_does_not_leak_across_workspaces(client, account, member):
    other_workspace = Workspace.objects.create(name="Other WS", organization=account.workspace.organization)
    other_account = SocialAccount.objects.create(
        workspace=other_workspace,
        platform="instagram",
        account_platform_id="ig-2",
        account_name="Other",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    _comment(other_account, message_id="theirs", extra={"stored_post_id": "media-1"}, body="Not yours")
    client.force_login(member)

    url = reverse("inbox:feed", kwargs={"workspace_id": account.workspace_id})
    response = client.get(url, {"post": "media-1"})

    assert list(response.context["inbox_messages"]) == []
    assert "Not yours" not in response.content.decode()


@pytest.mark.django_db
def test_the_count_on_the_notice_ignores_the_other_filters(client, account, member):
    # The notice survives the HTMX swap that the other filters trigger, so a
    # count narrowed by those filters would go stale the moment one changes.
    post = _platform_post(account, caption="Busy post", platform_post_id="media-1")
    _comment(account, message_id="c-1", extra={"stored_post_id": "media-1"}, related_post=post)
    resolved = _comment(account, message_id="c-2", extra={"stored_post_id": "media-1"}, related_post=post)
    resolved.status = InboxMessage.Status.RESOLVED
    resolved.save(update_fields=["status"])
    client.force_login(member)

    url = reverse("inbox:feed", kwargs={"workspace_id": account.workspace_id})
    response = client.get(url, {"post": "media-1", "status": "unread"})

    assert [m.platform_message_id for m in response.context["inbox_messages"]] == ["c-1"]
    assert response.context["post_filter_count"] == 2


@pytest.mark.django_db
def test_the_detail_panel_shows_the_post_with_a_way_back_to_it(client, account, member):
    platform_post = _platform_post(account, caption="The one we are reading", platform_post_id="media-1")
    message = _comment(
        account,
        message_id="c-1",
        extra={"stored_post_id": "media-1", "post_permalink_url": "https://instagram.com/p/abc/"},
        related_post=platform_post,
    )
    client.force_login(member)

    url = reverse(
        "inbox:message_detail",
        kwargs={"workspace_id": account.workspace_id, "message_id": message.id},
    )
    page = client.get(url).content.decode()

    assert "The one we are reading" in page
    assert "https://instagram.com/p/abc/" in page
    assert f"?post={platform_post.platform_post_id}" in page
    assert str(platform_post.post_id) in page  # the composer link
