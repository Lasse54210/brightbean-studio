"""Tests for the cover image choice in the composer (Blue Monkey Media fork)."""

from django.test import RequestFactory, TestCase

from apps.accounts.models import User
from apps.composer.models import PlatformPost, Post
from apps.composer.video_cover import cover_preview, facebook_cover_error, facebook_cover_extra
from apps.media_library.models import MediaAsset
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ACC = "11111111-1111-1111-1111-111111111111"
ASSET = "22222222-2222-2222-2222-222222222222"


def _post(fields=None):
    return RequestFactory().post("/", fields or {})


class FacebookCoverExtraTests(TestCase):
    def test_a_cover_is_stored_on_a_single_video(self):
        extra = facebook_cover_extra(_post({f"fb_cover_asset_id_{ACC}": ASSET}), ACC, {}, True)

        self.assertEqual(extra["cover_asset_id"], ASSET)

    def test_an_empty_field_clears_an_earlier_cover(self):
        extra = facebook_cover_extra(_post({f"fb_cover_asset_id_{ACC}": ""}), ACC, {"cover_asset_id": ASSET}, True)

        self.assertNotIn("cover_asset_id", extra)

    def test_without_exactly_one_video_the_cover_goes(self):
        # Swapping the video for an image must not leave a cover behind that a
        # later post (a duplicate, a recurrence) would inherit.
        extra = facebook_cover_extra(
            _post({f"fb_cover_asset_id_{ACC}": ASSET}), ACC, {"cover_asset_id": ASSET, "post_type": "reel"}, False
        )

        self.assertNotIn("cover_asset_id", extra)
        self.assertEqual(extra["post_type"], "reel")

    def test_a_value_that_is_not_an_id_is_dropped(self):
        extra = facebook_cover_extra(_post({f"fb_cover_asset_id_{ACC}": "nope"}), ACC, {}, True)

        self.assertNotIn("cover_asset_id", extra)


class FacebookCoverErrorAndPreviewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Cover Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Cover Workspace")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="page-1",
            account_name="Testpagina",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.acc_id = str(self.account.id)

    def _asset(self, media_type, workspace=None):
        return MediaAsset.objects.create(
            workspace=workspace or self.workspace,
            organization=self.org,
            filename=f"cover.{media_type}",
            media_type=media_type,
        )

    def test_an_image_passes(self):
        cover = self._asset("image")

        error = facebook_cover_error(
            _post({f"fb_cover_asset_id_{self.acc_id}": str(cover.id)}), self.workspace, [self.acc_id]
        )

        self.assertIsNone(error)

    def test_a_video_is_rejected_and_names_the_account(self):
        cover = self._asset("video")

        error = facebook_cover_error(
            _post({f"fb_cover_asset_id_{self.acc_id}": str(cover.id)}), self.workspace, [self.acc_id]
        )

        self.assertIn("Testpagina", error)
        self.assertIn("has to be an image", error)

    def test_an_image_from_another_workspace_is_rejected(self):
        other = Workspace.objects.create(organization=self.org, name="Other")
        cover = self._asset("image", workspace=other)

        error = facebook_cover_error(
            _post({f"fb_cover_asset_id_{self.acc_id}": str(cover.id)}), self.workspace, [self.acc_id]
        )

        self.assertIn("no longer in the media library", error)

    def test_preview_returns_the_stored_cover_for_facebook_and_instagram(self):
        user = User.objects.create_user(email="o@example.com", password="testpass123")
        post = Post.objects.create(workspace=self.workspace, author=user, caption="hi")
        ig = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="ig",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        cover = self._asset("image")
        fb_pp = PlatformPost.objects.create(
            post=post, social_account=self.account, platform_extra={"cover_asset_id": str(cover.id)}
        )
        ig_pp = PlatformPost.objects.create(
            post=post, social_account=ig, platform_extra={"cover_asset_id": str(cover.id), "post_type": "reel"}
        )

        preview = cover_preview([fb_pp, ig_pp], self.workspace)

        self.assertEqual(preview[self.acc_id][0], str(cover.id))
        self.assertEqual(preview[str(ig.id)][0], str(cover.id))
