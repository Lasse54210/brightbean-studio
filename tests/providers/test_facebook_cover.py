"""Tests for the Facebook video cover (Blue Monkey Media fork).

The cover is set after the video is already live, so the cases that matter
most are the failing ones: none of them may raise, because a raise makes the
engine retry and a retry posts the video twice.
"""

from unittest.mock import MagicMock, call, patch

from providers.exceptions import APIError
from providers.facebook import FacebookProvider
from providers.facebook_cover import set_video_cover
from providers.types import PostType, PublishContent

COVER = "https://s3.example/brightbean-media/video-covers/a.jpg"
THUMBS = "https://graph.facebook.com/v25.0/reel-video-1/thumbnails"


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def _reel_content(extra=None):
    return PublishContent(
        text="Reel caption",
        media_urls=["https://cdn.example.com/reel.mp4"],
        post_type=PostType.REEL,
        video_duration_sec=30,
        extra={"page_id": "page-1", **(extra or {})},
    )


def _reel_responses(*after_finish):
    return [
        _resp({"video_id": "reel-video-1", "upload_url": "https://rupload.facebook.com/reel-upload"}),
        _resp({"success": True}),
        _resp({"success": True}),
        *after_finish,
        _resp({"post_id": "page-1_post-9", "permalink_url": "https://www.facebook.com/reel/reel-video-1"}),
    ]


@patch("providers.facebook_cover._download", return_value=b"jpeg-bytes")
def test_a_reel_gets_its_cover_after_finish(download):
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(side_effect=_reel_responses(_resp({"success": True})))

    result = provider.publish_post("page-token", _reel_content({"cover_url": COVER}))

    assert result.platform_post_id == "post-9"
    download.assert_called_once_with(COVER)
    assert provider._request.call_args_list[3] == call(
        "POST",
        THUMBS,
        access_token="page-token",
        data={"is_preferred": "true"},
        files={"source": ("cover.jpg", b"jpeg-bytes", "image/jpeg")},
    )
    # The cover URL is publish input, not something to persist on the post.
    assert "cover_url" not in result.extra


def test_no_cover_means_no_extra_call():
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(side_effect=_reel_responses())

    provider.publish_post("page-token", _reel_content())

    assert all("thumbnails" not in c.args[1] for c in provider._request.call_args_list)


@patch("providers.facebook_cover._download", return_value=b"jpeg-bytes")
def test_a_refused_cover_does_not_fail_the_published_reel(_download):
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=_reel_responses(APIError("(#100) Invalid parameter", platform="facebook", status_code=400))
    )

    result = provider.publish_post("page-token", _reel_content({"cover_url": COVER}))

    assert result.platform_post_id == "post-9"


@patch("providers.facebook_cover._download", side_effect=OSError("storage down"))
def test_a_cover_that_cannot_be_downloaded_does_not_fail_the_reel(_download):
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(side_effect=_reel_responses())

    result = provider.publish_post("page-token", _reel_content({"cover_url": COVER}))

    assert result.platform_post_id == "post-9"


@patch("providers.facebook_cover._download", return_value=b"jpeg-bytes")
def test_a_regular_page_video_gets_the_cover_too(_download):
    provider = FacebookProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "video-7"}),
            _resp({"success": True}),
            _resp({"post_id": "page-1_post-3", "permalink_url": "/page-1/videos/video-7"}),
        ]
    )

    provider.publish_post(
        "page-token",
        PublishContent(
            text="Video",
            media_urls=["https://cdn.example.com/v.mp4"],
            post_type=PostType.VIDEO,
            extra={"page_id": "page-1", "cover_url": COVER},
        ),
    )

    assert provider._request.call_args_list[1].args[1] == "https://graph.facebook.com/v25.0/video-7/thumbnails"


def test_success_false_in_a_2xx_body_counts_as_refused():
    provider = MagicMock()
    provider._request.return_value = _resp({"success": False})
    provider._safe_json = FacebookProvider._safe_json

    with patch("providers.facebook_cover._download", return_value=b"x"):
        ok = set_video_cover(
            provider, "t", "https://graph.facebook.com/v25.0", "v1", _reel_content({"cover_url": COVER})
        )

    assert ok is False
