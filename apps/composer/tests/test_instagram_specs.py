"""Tests for the Instagram media specs (Blue Monkey Media fork).

Pure arithmetic, no database: what apps/composer/instagram_specs.py says about
a file at a placement. The numbers under test are Meta's, read on 2026-09-11,
and pinning them here is half the point -- when someone bumps a limit because a
blog post said so, a test should be what stops them.

The other half is the zero case. MediaAsset.width/height/duration default to 0
and are filled in by a background ffprobe task, so "not measured yet" is a
normal state that has to produce nothing at all rather than a blocked publish.
"""

from django.test import SimpleTestCase

from apps.composer.instagram_specs import (
    BLOCK,
    WARN,
    blocking_message,
    check_media,
    format_duration,
    format_ratio,
)

# Shapes, as (width, height).
PORTRAIT = (1080, 1920)  # 9:16
LANDSCAPE = (1920, 1080)  # 16:9
SQUARE = (1080, 1080)
PORTRAIT_FEED = (1080, 1350)  # 4:5
SLIVER = (100, 4000)  # 0.025:1, inside a Reel's range and outside a Story's


def _levels(findings):
    return [f.level for f in findings]


class ReelTests(SimpleTestCase):
    def test_a_nine_sixteen_video_is_fine(self):
        self.assertEqual(check_media(*PORTRAIT, 30, "reel"), [])

    def test_a_sixteen_nine_video_warns_about_cropping(self):
        findings = check_media(*LANDSCAPE, 30, "reel")

        self.assertEqual(_levels(findings), [WARN])
        self.assertIn("16:9", findings[0].message)
        self.assertIn("9:16", findings[0].message)

    def test_cropping_is_a_warning_and_never_a_block(self):
        # The composer must still let you schedule it. Cropping is a choice.
        self.assertIsNone(blocking_message(check_media(*LANDSCAPE, 30, "reel")))

    def test_outside_the_required_range_blocks(self):
        findings = check_media(10, 4000, 30, "reel")  # 0.0025:1

        self.assertEqual(_levels(findings), [BLOCK])

    def test_the_required_range_is_wide_enough_for_odd_shapes(self):
        # 0.025:1 is well inside 0.01:1, so this only warns about cropping.
        self.assertEqual(_levels(check_media(*SLIVER, 30, "reel")), [WARN])

    def test_a_video_under_three_seconds_blocks(self):
        findings = check_media(*PORTRAIT, 2, "reel")

        self.assertEqual(_levels(findings), [BLOCK])
        self.assertIn("3 s", findings[0].message)

    def test_fifteen_minutes_is_the_ceiling(self):
        self.assertEqual(check_media(*PORTRAIT, 15 * 60, "reel"), [])
        self.assertEqual(_levels(check_media(*PORTRAIT, 15 * 60 + 1, "reel")), [BLOCK])

    def test_shape_and_duration_are_both_reported(self):
        findings = check_media(*LANDSCAPE, 1, "reel")

        self.assertEqual(_levels(findings), [WARN, BLOCK])


class StoryTests(SimpleTestCase):
    def test_a_story_video_is_sixty_seconds_at_most(self):
        self.assertEqual(check_media(*PORTRAIT, 60, "story", "video"), [])
        self.assertEqual(_levels(check_media(*PORTRAIT, 61, "story", "video")), [BLOCK])

    def test_a_story_is_stricter_on_shape_than_a_reel(self):
        # 0.025:1 passes as a Reel (0.01:1) and not as a Story (0.1:1). The two
        # bounds really do differ in the reference; this is the test that says
        # so out loud.
        self.assertEqual(_levels(check_media(*SLIVER, 30, "reel")), [WARN])
        self.assertEqual(_levels(check_media(*SLIVER, 30, "story", "video")), [BLOCK])

    def test_a_story_image_has_no_duration_and_nothing_to_block_on(self):
        findings = check_media(*SLIVER, 0, "story", "image")

        # Only a recommendation applies to a story image, so an extreme shape
        # is a warning rather than a refusal.
        self.assertEqual(_levels(findings), [WARN])

    def test_a_story_image_at_nine_sixteen_is_silent(self):
        self.assertEqual(check_media(*PORTRAIT, 0, "story", "image"), [])

    def test_an_unknown_kind_is_judged_as_a_video(self):
        # A Story is usually a video, and judging an unknown as the stricter of
        # the two is the side that fails loudly rather than silently.
        self.assertEqual(_levels(check_media(*PORTRAIT, 90, "story")), [BLOCK])


class FeedImageTests(SimpleTestCase):
    def test_four_five_through_one_ninety_one_publishes_uncropped(self):
        for shape in (PORTRAIT_FEED, SQUARE, (1910, 1000)):
            with self.subTest(shape=shape):
                self.assertEqual(check_media(*shape, 0, "image", "image"), [])

    def test_a_nine_sixteen_image_warns_that_the_feed_crops_it(self):
        findings = check_media(*PORTRAIT, 0, "image", "image")

        self.assertEqual(_levels(findings), [WARN])
        self.assertIn("4:5", findings[0].message)
        self.assertIn("1.91:1", findings[0].message)

    def test_a_feed_image_never_blocks(self):
        # Deliberate: the reference words 4:5-1.91:1 as hard, but Instagram
        # crops rather than refusing, and a false block costs a release.
        for shape in (PORTRAIT, LANDSCAPE, SLIVER):
            with self.subTest(shape=shape):
                self.assertIsNone(blocking_message(check_media(*shape, 0, "image", "image")))

    def test_a_carousel_item_is_judged_as_a_feed_image(self):
        self.assertEqual(check_media(*SQUARE, 0, "carousel", "image"), [])
        self.assertEqual(_levels(check_media(*PORTRAIT, 0, "carousel", "image")), [WARN])


class UnknownMeasurementTests(SimpleTestCase):
    def test_an_unprocessed_asset_produces_nothing(self):
        # Exactly what a freshly uploaded MediaAsset looks like before the
        # ffprobe task has run. Anything but [] here stops every new upload.
        self.assertEqual(check_media(0, 0, 0, "reel"), [])

    def test_a_missing_duration_does_not_block_on_the_minimum(self):
        self.assertEqual(check_media(*PORTRAIT, 0, "reel"), [])

    def test_none_is_treated_the_same_as_zero(self):
        self.assertEqual(check_media(None, None, None, "reel"), [])

    def test_a_measured_shape_still_reports_with_an_unknown_duration(self):
        self.assertEqual(_levels(check_media(*LANDSCAPE, 0, "reel")), [WARN])

    def test_an_unknown_placement_is_not_this_module_s_problem(self):
        self.assertEqual(check_media(*LANDSCAPE, 30, "boomerang"), [])
        self.assertEqual(check_media(*LANDSCAPE, 30, ""), [])


class FormattingTests(SimpleTestCase):
    def test_common_ratios_get_their_name(self):
        self.assertEqual(format_ratio(1920 / 1080), "16:9")
        self.assertEqual(format_ratio(1080 / 1920), "9:16")
        self.assertEqual(format_ratio(1.0), "1:1")
        self.assertEqual(format_ratio(1080 / 1350), "4:5")

    def test_a_near_miss_still_gets_the_name(self):
        # 1080x1912 is a trimmed 9:16, not a shape anyone chose. Warning about
        # it would be noise.
        self.assertEqual(format_ratio(1080 / 1912), "9:16")

    def test_an_unnamed_ratio_falls_back_to_decimals(self):
        self.assertEqual(format_ratio(2.35), "2.35:1")
        self.assertEqual(format_ratio(1 / 2.35), "1:2.35")

    def test_durations_read_like_a_person_wrote_them(self):
        self.assertEqual(format_duration(12), "12 s")
        self.assertEqual(format_duration(60), "1 min")
        self.assertEqual(format_duration(95), "1 min 35 s")
        self.assertEqual(format_duration(15 * 60), "15 min")


class BlockingMessageTests(SimpleTestCase):
    def test_it_returns_the_first_blocking_message(self):
        findings = check_media(*LANDSCAPE, 1, "reel")

        self.assertIn("3 s", blocking_message(findings))

    def test_warnings_alone_are_not_a_message(self):
        self.assertIsNone(blocking_message(check_media(*LANDSCAPE, 30, "reel")))
