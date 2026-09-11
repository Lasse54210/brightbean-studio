"""Tests for the Instagram placement extras (Blue Monkey Media fork).

Two things are being pinned down here. First that a submit which does not carry
the panel cannot wipe a placement that was chosen earlier, because that is the
failure mode that silently turns a Story back into a Reel. Second that the
placement has to match the attached media before the save goes through, since
Instagram accepts a mismatched container and only fails it minutes later.
"""

from django.test import RequestFactory, TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.instagram_extras import (
    build_instagram_extra,
    instagram_placement_error,
    placement_media_error,
    placement_spec_error,
)
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ACC = "11111111-1111-1111-1111-111111111111"


def _post(fields=None):
    return RequestFactory().post("/", fields or {})


class BuildInstagramExtraTests(TestCase):
    def test_placement_is_stored_as_the_post_type_hint(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": "story"}), ACC)

        # post_type is what apps/publisher/engine.py::_resolve_post_type reads.
        self.assertEqual(extra["post_type"], "story")

    def test_an_unknown_placement_keeps_the_stored_one(self):
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": "boomerang"}),
            ACC,
            {"post_type": "story"},
        )

        self.assertEqual(extra["post_type"], "story")

    def test_an_empty_placement_keeps_the_stored_one(self):
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": ""}),
            ACC,
            {"post_type": "reel"},
        )

        self.assertEqual(extra["post_type"], "reel")

    def test_nothing_stored_and_nothing_chosen_leaves_the_derivation_alone(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": ""}), ACC)

        # No post_type at all, so the publisher derives it the way it always
        # did. An empty string here would be an invalid hint, not a blank one.
        self.assertNotIn("post_type", extra)

    def test_automatic_clears_a_stored_placement(self):
        # 'auto' is the way back to the publisher's own derivation. It is a
        # distinct value because an empty one already means "keep what is
        # stored", and both meanings are needed.
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": "auto"}),
            ACC,
            {"post_type": "story", "share_to_feed": True, "thumb_offset": 900},
        )

        self.assertEqual(extra, {})

    def test_automatic_on_a_fresh_post_stores_nothing(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": "auto"}), ACC)

        self.assertEqual(extra, {})

    def test_share_to_feed_is_only_stored_for_a_reel(self):
        reel = build_instagram_extra(_post({f"ig_placement_{ACC}": "reel", f"ig_share_to_feed_{ACC}": "true"}), ACC)
        story = build_instagram_extra(_post({f"ig_placement_{ACC}": "story", f"ig_share_to_feed_{ACC}": "true"}), ACC)

        self.assertIs(reel["share_to_feed"], True)
        self.assertNotIn("share_to_feed", story)

    def test_an_unchecked_share_to_feed_is_false_and_not_missing(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": "reel"}), ACC)

        self.assertIs(extra["share_to_feed"], False)

    def test_cover_timestamp_becomes_thumb_offset(self):
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": "reel", f"ig_cover_timestamp_ms_{ACC}": "2500"}), ACC
        )

        self.assertEqual(extra["thumb_offset"], 2500)

    def test_a_cover_that_is_not_a_number_is_dropped_rather_than_raising(self):
        for raw in ("abc", "2,5", "2.5", "\u00b2"):
            with self.subTest(raw=raw):
                extra = build_instagram_extra(
                    _post({f"ig_placement_{ACC}": "reel", f"ig_cover_timestamp_ms_{ACC}": raw}), ACC
                )

                self.assertNotIn("thumb_offset", extra)

    def test_a_negative_cover_is_dropped(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": "reel", f"ig_cover_timestamp_ms_{ACC}": "-1"}), ACC)

        self.assertNotIn("thumb_offset", extra)

    def test_a_cover_on_a_story_is_dropped(self):
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": "story", f"ig_cover_timestamp_ms_{ACC}": "2500"}), ACC
        )

        self.assertNotIn("thumb_offset", extra)


class PlacementMediaErrorTests(TestCase):
    def test_a_reel_needs_a_video(self):
        self.assertTrue(placement_media_error("reel", ["image"]))
        self.assertIsNone(placement_media_error("reel", ["video"]))

    def test_a_feed_image_needs_an_image(self):
        self.assertTrue(placement_media_error("image", ["video"]))
        self.assertIsNone(placement_media_error("image", ["image"]))

    def test_a_carousel_needs_two_files(self):
        self.assertTrue(placement_media_error("carousel", ["image"]))
        self.assertIsNone(placement_media_error("carousel", ["image", "video"]))

    def test_a_carousel_holds_ten_files(self):
        # Meta's own number, from the `children` parameter. An eleventh file is
        # refused outright, so it has to be caught before the publish.
        self.assertIsNone(placement_media_error("carousel", ["image"] * 10))
        self.assertTrue(placement_media_error("carousel", ["image"] * 11))

    def test_a_story_takes_either(self):
        self.assertIsNone(placement_media_error("story", ["video"]))
        self.assertIsNone(placement_media_error("story", ["image"]))

    def test_every_placement_needs_media_at_all(self):
        for placement in ("reel", "story", "carousel", "image"):
            self.assertTrue(placement_media_error(placement, []))

    def test_an_unknown_placement_is_not_this_function_s_problem(self):
        self.assertIsNone(placement_media_error("", ["image"]))


class InstagramPlacementErrorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="bluemonkeymedia",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.acc_id = str(self.account.id)
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hi")

    def _attach(self, media_type):
        asset = MediaAsset.objects.create(
            workspace=self.workspace,
            organization=self.org,
            filename=f"file.{media_type}",
            media_type=media_type,
        )
        PostMedia.objects.create(post=self.post, media_asset=asset, position=self.post.media_attachments.count())
        return asset

    def test_a_reel_on_an_image_is_rejected_and_names_the_account(self):
        self._attach("image")

        error = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": "reel"}),
            self.post,
            self.workspace,
            [self.acc_id],
        )

        self.assertIn("bluemonkeymedia", error)

    def test_a_reel_on_a_video_passes(self):
        self._attach("video")

        error = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": "reel"}),
            self.post,
            self.workspace,
            [self.acc_id],
        )

        self.assertIsNone(error)

    def test_a_stored_placement_is_rechecked_when_the_media_changed(self):
        # The panel is in the form but submits nothing usable, so the save keeps
        # the stored placement. The media is now an image, so that stored Reel
        # is no longer publishable and has to be caught here.
        self._attach("image")
        PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            platform_extra={"post_type": "reel"},
        )

        error = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": ""}),
            self.post,
            self.workspace,
            [self.acc_id],
        )

        self.assertIsNotNone(error)

    def test_automatic_is_never_rejected(self):
        # An image with a stored Reel would be rejected; picking Automatic
        # instead must be the way out of that, not another error.
        self._attach("image")
        PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            platform_extra={"post_type": "reel"},
        )

        error = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": "auto"}),
            self.post,
            self.workspace,
            [self.acc_id],
        )

        self.assertIsNone(error)

    def test_a_form_without_the_panel_is_left_alone(self):
        self._attach("image")
        PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            platform_extra={"post_type": "reel"},
        )

        error = instagram_placement_error(_post({}), self.post, self.workspace, [self.acc_id])

        self.assertIsNone(error)

    def test_a_new_post_is_judged_on_its_session_media(self):
        asset = MediaAsset.objects.create(
            workspace=self.workspace,
            organization=self.org,
            filename="clip.mp4",
            media_type="video",
        )
        # Not saved, but it already has a UUID primary key -- which is exactly
        # why _media_kinds cannot use post.pk to decide where to look.
        unsaved = Post(workspace=self.workspace, author=self.user)
        self.assertTrue(unsaved.pk)

        ok = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": "reel"}),
            unsaved,
            self.workspace,
            [self.acc_id],
            [str(asset.id)],
        )
        missing = instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": "reel"}),
            unsaved,
            self.workspace,
            [self.acc_id],
            [],
        )

        self.assertIsNone(ok)
        self.assertIsNotNone(missing)

    def test_a_non_instagram_account_is_ignored(self):
        tiktok = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="tiktok",
            account_platform_id="tt-1",
            account_name="janschmitz51",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self._attach("image")

        error = instagram_placement_error(
            _post({f"ig_placement_{tiktok.id}": "reel"}),
            self.post,
            self.workspace,
            [str(tiktok.id)],
        )

        self.assertIsNone(error)


class PlacementSpecErrorTests(TestCase):
    """The bridge between the specs module and the save gate.

    What the specs say is tested in test_instagram_specs.py; what is tested
    here is which of those findings get to stop a save, and that a carousel is
    judged item by item rather than on its first file.
    """

    def _record(self, kind="video", width=1080, height=1920, duration=30):
        return {"kind": kind, "width": width, "height": height, "duration": duration}

    def test_a_shape_instagram_refuses_stops_the_save(self):
        error = placement_spec_error("reel", [self._record(width=10, height=4000)])

        self.assertIsNotNone(error)

    def test_a_crop_warning_does_not_stop_the_save(self):
        # 16:9 as a Reel is cropped, not refused. The panel says so; the save
        # goes through.
        self.assertIsNone(placement_spec_error("reel", [self._record(width=1920, height=1080)]))

    def test_a_video_that_is_too_long_stops_the_save(self):
        self.assertIsNone(placement_spec_error("story", [self._record(duration=60)]))
        self.assertIsNotNone(placement_spec_error("story", [self._record(duration=61)]))

    def test_an_unmeasured_asset_never_stops_the_save(self):
        # The ffprobe task has not run. Blocking here would stop every upload
        # that is published straight away.
        self.assertIsNone(placement_spec_error("reel", [self._record(width=0, height=0, duration=0)]))

    def test_only_the_first_file_counts_outside_a_carousel(self):
        good = self._record()
        bad = self._record(width=10, height=4000)

        self.assertIsNone(placement_spec_error("reel", [good, bad]))

    def test_a_carousel_never_blocks_however_odd_its_files_are(self):
        square = self._record(kind="image", width=1080, height=1080, duration=0)
        # 4000x10 is 400:1, far outside what any placement recommends.
        extreme = self._record(kind="image", width=4000, height=10, duration=0)

        # A carousel item is judged as a feed image, and no feed image rule
        # blocks -- the crop is a warning, the same as anywhere else. This is
        # the test that will fail if someone makes 4:5-1.91:1 a hard stop, and
        # that is the moment to check the per-item loop again.
        self.assertIsNone(placement_spec_error("carousel", [square, square, extreme]))

    def test_no_media_is_not_this_function_s_problem(self):
        # placement_media_error already says "attach media first".
        self.assertIsNone(placement_spec_error("reel", []))


class InstagramPlacementErrorSpecTests(TestCase):
    """The specs reaching the save gate, end to end."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="specs@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.org = Organization.objects.create(name="Specs Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Specs Workspace")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-2",
            account_name="bluemonkeymedia",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.acc_id = str(self.account.id)
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hi")

    def _attach(self, **fields):
        asset = MediaAsset.objects.create(
            workspace=self.workspace,
            organization=self.org,
            filename="clip.mp4",
            media_type="video",
            **fields,
        )
        PostMedia.objects.create(post=self.post, media_asset=asset, position=self.post.media_attachments.count())
        return asset

    def _error(self, placement="reel"):
        return instagram_placement_error(
            _post({f"ig_placement_{self.acc_id}": placement}),
            self.post,
            self.workspace,
            [self.acc_id],
        )

    def test_a_two_second_reel_is_rejected_and_names_the_account(self):
        self._attach(width=1080, height=1920, duration=2)

        error = self._error()

        self.assertIn("bluemonkeymedia", error)
        self.assertIn("3 s", error)

    def test_a_sixteen_nine_reel_is_allowed_through(self):
        self._attach(width=1920, height=1080, duration=30)

        self.assertIsNone(self._error())

    def test_an_unmeasured_video_is_allowed_through(self):
        # Defaults straight off the model: this is what an asset looks like
        # between the upload finishing and the ffprobe task running.
        self._attach()

        self.assertIsNone(self._error())
