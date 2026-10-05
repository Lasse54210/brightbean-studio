"""Tests for the Facebook placement row in the composer (Blue Monkey Media fork)."""

from django.test import SimpleTestCase

from apps.composer.facebook_extras import placement_media_error, stored_placement
from apps.composer.models import PlatformPost, PostMedia
from apps.composer.tests.test_account_scope import AccountScopeTestsBase
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount


class PlacementMediaErrorTests(SimpleTestCase):
    def test_automatic_takes_anything(self):
        self.assertIsNone(placement_media_error("auto", ["image", "video"]))

    def test_a_reel_needs_exactly_one_video(self):
        self.assertIsNone(placement_media_error("reel", ["video"]))
        self.assertIn("exactly one video", placement_media_error("reel", []))
        self.assertIn("exactly one video", placement_media_error("reel", ["image"]))
        self.assertIn("exactly one video", placement_media_error("reel", ["video", "image"]))

    def test_a_post_takes_text_photos_or_one_video(self):
        self.assertIsNone(placement_media_error("post", []))
        self.assertIsNone(placement_media_error("post", ["image", "image"]))
        self.assertIsNone(placement_media_error("post", ["video"]))

    def test_a_post_refuses_photos_and_a_video_together(self):
        self.assertIn("not both", placement_media_error("post", ["image", "video"]))
        self.assertIn("not both", placement_media_error("post", ["video", "image"]))
        self.assertIn("not both", placement_media_error("post", ["video", "video"]))

    def test_a_story_takes_one_photo_or_one_video(self):
        self.assertIsNone(placement_media_error("story", ["image"]))
        self.assertIsNone(placement_media_error("story", ["video"]))
        self.assertIn("exactly one", placement_media_error("story", []))
        self.assertIn("exactly one", placement_media_error("story", ["image", "image"]))

    def test_a_post_holds_ten_photos(self):
        self.assertIsNone(placement_media_error("post", ["image"] * 10))
        self.assertIn("Remove 1", placement_media_error("post", ["image"] * 11))


class StoredPlacementTests(SimpleTestCase):
    def test_upstreams_reel_hint_reads_as_reel(self):
        self.assertEqual(stored_placement({"post_type": "reel"}), "reel")

    def test_a_story_reads_as_story(self):
        self.assertEqual(stored_placement({"post_type": "story"}), "story")

    def test_post_and_nothing(self):
        self.assertEqual(stored_placement({"placement": "post"}), "post")
        self.assertEqual(stored_placement({}), "auto")
        self.assertEqual(stored_placement(None), "auto")


class FacebookPlacementSaveTests(AccountScopeTestsBase):
    def setUp(self):
        super().setUp()
        self.facebook = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="fb-page-1",
            account_name="Facebook Page",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.facebook_pp = PlatformPost.objects.create(
            post=self.post, social_account=self.facebook, status=PlatformPost.Status.DRAFT
        )
        self.acc_id = str(self.facebook.id)

    def _attach(self, media_type, position=0):
        asset = MediaAsset.objects.create(
            organization=self.org,
            workspace=self.workspace,
            uploaded_by=self.user,
            file=f"test/file-{position}.{'mp4' if media_type == 'video' else 'jpg'}",
            filename=f"file-{position}",
            media_type=media_type,
        )
        PostMedia.objects.create(post=self.post, media_asset=asset, position=position)
        return asset

    def _save(self, placement=None, **extra_fields):
        fields = {f"facebook_panel_{self.acc_id}": "1", **extra_fields}
        if placement is not None:
            fields[f"fb_placement_{self.acc_id}"] = placement
        return self.client.post(
            self.save_url, data=self._payload(selected_accounts=self.acc_id, account_scope=self.acc_id, **fields)
        )

    def _extra(self):
        self.facebook_pp.refresh_from_db()
        return self.facebook_pp.platform_extra or {}

    def test_reel_on_one_video_is_stored_as_upstreams_hint(self):
        self._attach("video")

        response = self._save("reel")

        self.assertIn(response.status_code, (200, 204, 302))
        self.assertEqual(self._extra().get("post_type"), "reel")

    def test_post_is_remembered_without_a_post_type(self):
        self._attach("video")

        self._save("post")

        self.assertEqual(self._extra().get("placement"), "post")
        self.assertNotIn("post_type", self._extra())

    def test_automatic_clears_an_earlier_choice(self):
        self._attach("video")
        self.facebook_pp.platform_extra = {"post_type": "reel", "audience": "public"}
        self.facebook_pp.save(update_fields=["platform_extra"])

        self._save("auto")

        self.assertEqual(self._extra(), {"audience": "public"})

    def test_an_empty_value_keeps_the_stored_reel(self):
        self._attach("video")
        self.facebook_pp.platform_extra = {"post_type": "reel"}
        self.facebook_pp.save(update_fields=["platform_extra"])

        self._save("")

        self.assertEqual(self._extra().get("post_type"), "reel")

    def test_story_on_one_file_is_stored_and_drops_the_cover(self):
        self._attach("video")
        cover = MediaAsset.objects.create(
            organization=self.org, workspace=self.workspace, filename="cover.png", media_type="image"
        )

        response = self._save("story", **{f"fb_cover_asset_id_{self.acc_id}": str(cover.id)})

        self.assertIn(response.status_code, (200, 204, 302))
        self.assertEqual(self._extra().get("post_type"), "story")
        self.assertNotIn("cover_asset_id", self._extra())

    def test_a_story_with_two_files_is_refused(self):
        self._attach("image", 0)
        self._attach("image", 1)

        response = self._save("story")

        self.assertEqual(response.status_code, 400)
        self.assertIn("exactly one photo or one video", response.json()["errors"]["facebook_placement"])

    def test_a_reel_without_a_video_is_refused(self):
        self._attach("image")

        response = self._save("reel")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Facebook Page: A Reel needs exactly one video", response.json()["errors"]["facebook_placement"])
        self.assertNotIn("post_type", self._extra())

    def test_a_story_video_over_60_seconds_is_refused(self):
        asset = self._attach("video")
        asset.width, asset.height, asset.duration = 1080, 1920, 75
        asset.save(update_fields=["width", "height", "duration"])

        response = self._save("story")

        self.assertEqual(response.status_code, 400)
        self.assertIn("1 min at most", response.json()["errors"]["facebook_placement"])

    def test_a_post_with_photos_and_a_video_is_refused(self):
        self._attach("image", 0)
        self._attach("video", 1)

        response = self._save("post")

        self.assertEqual(response.status_code, 400)
        self.assertIn("not both", response.json()["errors"]["facebook_placement"])

    def test_automatic_keeps_upstreams_behaviour_for_mixed_media(self):
        self._attach("image", 0)
        self._attach("video", 1)

        response = self._save("auto")

        self.assertIn(response.status_code, (200, 204, 302))

    def test_the_cover_survives_next_to_a_reel(self):
        self._attach("video")
        cover = MediaAsset.objects.create(
            organization=self.org, workspace=self.workspace, filename="cover.png", media_type="image"
        )

        self._save("reel", **{f"fb_cover_asset_id_{self.acc_id}": str(cover.id)})

        self.assertEqual(self._extra().get("post_type"), "reel")
        self.assertEqual(self._extra().get("cover_asset_id"), str(cover.id))

    def test_a_save_without_the_row_leaves_upstreams_handling_alone(self):
        self._attach("video")

        self._save(None, **{f"facebook_post_type_{self.acc_id}": "reel"})

        self.assertEqual(self._extra().get("post_type"), "reel")
