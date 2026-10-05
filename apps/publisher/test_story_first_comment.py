"""A Story gets no first comment (Blue Monkey Media fork).

Neither Instagram nor Facebook takes comments on a Story through the API, so
queueing one only produces a failed comment next to a published post.
"""

from unittest.mock import patch

from django.test import TestCase

from apps.composer.models import PlatformPost, Post
from apps.organizations.models import Organization
from apps.publisher.engine import PublishEngine
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class StoryFirstCommentTest(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Org")
        workspace = Workspace.objects.create(organization=org, name="WS")
        self.account = SocialAccount.objects.create(
            workspace=workspace,
            platform="facebook",
            account_platform_id="page-1",
            account_name="Test Page",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        post = Post.objects.create(workspace=workspace, caption="hi", first_comment="Read more")
        self.platform_post = PlatformPost.objects.create(
            post=post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHED,
            platform_post_id="story-1",
            platform_extra={"post_type": "story"},
        )

    def test_a_story_is_not_given_a_first_comment(self):
        with patch("apps.publisher.engine._post_first_comment_task") as task:
            PublishEngine()._maybe_schedule_first_comment(self.platform_post)

        task.assert_not_called()

    def test_a_reel_still_is(self):
        self.platform_post.platform_extra = {"post_type": "reel"}
        self.platform_post.save(update_fields=["platform_extra"])

        with patch("apps.publisher.engine._post_first_comment_task") as task:
            PublishEngine()._maybe_schedule_first_comment(self.platform_post)

        task.assert_called_once()
