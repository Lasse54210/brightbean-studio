"""Tests for the Facebook Reel and Story specs (Blue Monkey Media fork)."""

import pytest

from apps.composer.facebook_specs import check_media
from apps.composer.instagram_specs import BLOCK, WARN, blocking_message


def _levels(findings):
    return [finding.level for finding in findings]


def test_a_vertical_hd_reel_has_nothing_to_say():
    assert check_media(1080, 1920, 30, "reel", "video") == []


@pytest.mark.parametrize(("width", "height"), [(1920, 1080), (1080, 1350)])
def test_a_reel_between_16_9_and_9_16_publishes_with_a_warning(width, height):
    assert _levels(check_media(width, height, 30, "reel", "video")) == [WARN]


def test_a_reel_wider_than_16_9_is_refused():
    message = blocking_message(check_media(2560, 1080, 30, "reel", "video"))

    assert "between 9:16 and 16:9" in message


@pytest.mark.parametrize(("duration", "words"), [(2, "3 s at the very least"), (91, "1 min 30 s at most")])
def test_a_reel_outside_3_to_90_seconds_is_refused(duration, words):
    assert words in blocking_message(check_media(1080, 1920, duration, "reel", "video"))


def test_a_story_video_stops_at_60_seconds():
    assert check_media(1080, 1920, 60, "story", "video") == []
    assert "1 min at most" in blocking_message(check_media(1080, 1920, 61, "story", "video"))


def test_a_landscape_story_is_a_warning_not_a_stop():
    assert _levels(check_media(1920, 1080, 20, "story", "video")) == [WARN]


def test_a_small_file_warns():
    findings = check_media(480, 854, 20, "story", "video")

    assert _levels(findings) == [WARN]
    assert "at least 540x960" in findings[0].message


def test_a_story_image_has_no_duration():
    assert check_media(1080, 1920, 0, "story", "image") == []


def test_unknown_measurements_say_nothing():
    assert check_media(0, 0, 0, "reel", "video") == []
    assert check_media(0, 0, None, "story", "video") == []


def test_a_post_is_not_judged():
    assert check_media(2560, 1080, 600, "post", "video") == []


def test_blocking_comes_first():
    findings = check_media(480, 854, 120, "story", "video")

    assert findings[0].level == BLOCK
