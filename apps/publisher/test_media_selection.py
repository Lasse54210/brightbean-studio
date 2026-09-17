"""An account publishes its own files when it has them (Blue Monkey Media fork).

Pinned down: without a list the post's attachments come back untouched, with a
list the listed assets come back in list order, a vanished id is skipped, an
asset from another organisation is refused, and a list that resolves to nothing
falls back to the post's attachments rather than publishing an empty post.
"""

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.organizations.models import Organization
from apps.publisher.media_selection import resolve_attachments, selected_asset_ids
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace


class ResolveAttachmentsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="o@example.com", password="x", tos_accepted_at=timezone.now())
        self.org = Organization.objects.create(name="Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="WS")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="acct",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hi")
        self.pp = PlatformPost.objects.create(post=self.post, social_account=self.account)
        self.wide = self._asset("wide.mp4", "video")
        self.tall = self._asset("tall.mp4", "video")
        self.square = self._asset("square.jpg", "image")
        PostMedia.objects.create(post=self.post, media_asset=self.wide, position=0, alt_text="wide alt")
        PostMedia.objects.create(post=self.post, media_asset=self.square, position=1)

    def _asset(self, filename, media_type, workspace=None, org=None):
        return MediaAsset.objects.create(
            workspace=workspace or self.workspace,
            organization=org or self.org,
            filename=filename,
            media_type=media_type,
        )

    def _assets(self, attachments):
        return [a.media_asset for a in attachments]

    def test_without_a_list_the_post_attachments_come_back_as_they_were(self):
        attachments = resolve_attachments(self.pp)
        self.assertEqual(self._assets(attachments), [self.wide, self.square])
        # The very rows, so anything else the engine might read off them is intact.
        self.assertIsInstance(attachments[0], PostMedia)

    def test_a_list_wins_and_its_order_is_the_carousel_order(self):
        self.pp.platform_specific_media = [str(self.square.id), str(self.tall.id)]
        attachments = resolve_attachments(self.pp)
        self.assertEqual(self._assets(attachments), [self.square, self.tall])
        self.assertEqual([a.position for a in attachments], [0, 1])

    def test_alt_text_follows_a_file_that_is_also_on_the_post(self):
        self.pp.platform_specific_media = [str(self.wide.id)]
        (attachment,) = resolve_attachments(self.pp)
        self.assertEqual(attachment.alt_text, "wide alt")

    def test_a_vanished_id_is_skipped_not_fatal(self):
        gone = self._asset("gone.mp4", "video")
        self.pp.platform_specific_media = [str(gone.id), str(self.tall.id)]
        gone.delete()
        self.assertEqual(self._assets(resolve_attachments(self.pp)), [self.tall])

    def test_another_organisations_file_is_refused(self):
        other_org = Organization.objects.create(name="Other")
        other_ws = Workspace.objects.create(organization=other_org, name="Other WS")
        foreign = self._asset("foreign.mp4", "video", workspace=other_ws, org=other_org)
        self.pp.platform_specific_media = [str(foreign.id)]
        # Nothing resolves, so the post's own attachments are what gets published.
        self.assertEqual(self._assets(resolve_attachments(self.pp)), [self.wide, self.square])

    def test_garbage_in_the_column_is_ignored(self):
        self.pp.platform_specific_media = {"not": "a list"}
        self.assertEqual(selected_asset_ids(self.pp), [])
        self.assertEqual(self._assets(resolve_attachments(self.pp)), [self.wide, self.square])
