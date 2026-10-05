"""Tests for Facebook Page Stories (Blue Monkey Media fork).

No network: ``_request`` is a mock that answers in the order the Page Stories
API does. The cases that matter most are the edges of "published": a failing
step must raise (and clean up), and nothing after a published Story may.
"""

from unittest.mock import MagicMock, call

import pytest

from providers.exceptions import APIError, PublishError
from providers.facebook import BASE_URL, FacebookProvider
from providers.types import PostType, PublishContent

PHOTO = "https://cdn.example.com/story.jpg"
VIDEO = "https://cdn.example.com/story.mp4"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _provider(*responses):
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(side_effect=list(responses))
    return provider


def _content(url, kind, duration=None, text="never sent"):
    return PublishContent(
        text=text,
        media_urls=[url],
        media_types=[kind],
        post_type=PostType.STORY,
        video_duration_sec=duration,
        extra={"page_id": "page-1"},
    )


def test_a_photo_story_stages_the_photo_unpublished_and_publishes_it():
    provider = _provider(_resp({"id": "photo-7"}), _resp({"success": True, "post_id": "page-1_story-3"}))

    result = provider.publish_post("page-token", _content(PHOTO, "image"))

    assert provider._request.call_args_list == [
        call("POST", f"{BASE_URL}/page-1/photos", access_token="page-token", json={"url": PHOTO, "published": False}),
        call("POST", f"{BASE_URL}/page-1/photo_stories", access_token="page-token", data={"photo_id": "photo-7"}),
    ]
    assert result.platform_post_id == "story-3"
    assert result.extra == {"photo_id": "photo-7", "post_id": "page-1_story-3"}


def test_a_failed_photo_story_removes_the_staged_photo():
    provider = _provider(_resp({"id": "photo-7"}), _resp({"success": False}), _resp({"success": True}))

    with pytest.raises(PublishError, match="publish the photo Story"):
        provider.publish_post("page-token", _content(PHOTO, "image"))

    assert provider._request.call_args_list[2] == call("DELETE", f"{BASE_URL}/photo-7", access_token="page-token")


def test_a_video_story_runs_start_upload_finish_without_a_caption():
    provider = _provider(
        _resp({"video_id": "vid-5", "upload_url": "https://rupload.facebook.com/video-upload/v25.0/vid-5"}),
        _resp({"success": True}),
        _resp({"success": True, "post_id": "story-9"}),
    )

    result = provider.publish_post("page-token", _content(VIDEO, "video", duration=20))

    calls = provider._request.call_args_list
    assert calls[0] == call(
        "POST", f"{BASE_URL}/page-1/video_stories", access_token="page-token", data={"upload_phase": "start"}
    )
    assert calls[1] == call(
        "POST",
        "https://rupload.facebook.com/video-upload/v25.0/vid-5",
        headers={"Authorization": "OAuth page-token", "file_url": VIDEO},
    )
    assert calls[2] == call(
        "POST",
        f"{BASE_URL}/page-1/video_stories",
        access_token="page-token",
        data={"upload_phase": "finish", "video_id": "vid-5"},
    )
    assert "never sent" not in str(calls)
    assert result.platform_post_id == "story-9"
    assert result.extra == {"video_id": "vid-5", "post_id": "story-9"}


def test_a_failed_upload_names_the_step_and_never_finishes():
    provider = _provider(
        _resp({"video_id": "vid-5", "upload_url": "https://rupload.facebook.com/x"}),
        _resp({"success": False, "debug_info": {"message": "fetch failed"}}),
    )

    with pytest.raises(PublishError, match="upload the Story video"):
        provider.publish_post("page-token", _content(VIDEO, "video", duration=20))

    assert provider._request.call_count == 2


@pytest.mark.parametrize("duration", [2, 61, 90])
def test_a_video_outside_3_to_60_seconds_is_refused_before_any_call(duration):
    provider = _provider()

    with pytest.raises(PublishError, match="3 to 60 seconds"):
        provider.publish_post("page-token", _content(VIDEO, "video", duration=duration))

    provider._request.assert_not_called()


def test_an_unknown_duration_goes_ahead():
    provider = _provider(
        _resp({"video_id": "vid-5", "upload_url": "https://rupload.facebook.com/x"}),
        _resp({"success": True}),
        _resp({"success": True, "post_id": "story-9"}),
    )

    assert provider.publish_post("page-token", _content(VIDEO, "video")).platform_post_id == "story-9"


def test_a_story_needs_exactly_one_file():
    provider = _provider()
    content = _content(PHOTO, "image")
    content.media_urls = [PHOTO, PHOTO]
    content.media_types = ["image", "image"]

    with pytest.raises(PublishError, match="exactly one"):
        provider.publish_post("page-token", content)


def test_a_published_story_without_a_post_id_still_succeeds():
    # Live is live: a body without post_id must not raise, or the engine
    # retries and the Story appears twice.
    provider = _provider(
        _resp({"video_id": "vid-5", "upload_url": "https://rupload.facebook.com/x"}),
        _resp({"success": True}),
        _resp({"success": True}),
    )

    result = provider.publish_post("page-token", _content(VIDEO, "video", duration=10))

    assert result.platform_post_id == "vid-5"
    assert result.extra == {"video_id": "vid-5"}


def test_an_http_error_on_publish_still_cleans_up_and_raises():
    provider = _provider(_resp({"id": "photo-7"}), APIError("boom", platform="Facebook"), _resp({}))

    with pytest.raises(APIError):
        provider.publish_post("page-token", _content(PHOTO, "image"))

    assert provider._request.call_args_list[2] == call("DELETE", f"{BASE_URL}/photo-7", access_token="page-token")
