"""Tests for the placement allowlist (Blue Monkey Media fork).

The point of apply_placement is not that it copies values, it is that it
refuses to copy a parameter onto a container that does not take it. Instagram
does not ignore those: it fails the container asynchronously, minutes later,
with an error that names nothing. So the negative cases below matter more than
the positive ones.
"""

from unittest.mock import MagicMock

from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.instagram_placement import apply_placement, is_video_url
from providers.types import PostType, PublishContent


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


def test_share_to_feed_and_thumb_offset_land_on_a_reel():
    payload = {"media_type": "REELS", "video_url": "https://example.com/v.mp4"}
    content = PublishContent(extra={"share_to_feed": True, "thumb_offset": 2500})

    apply_placement(payload, content)

    assert payload["share_to_feed"] is True
    assert payload["thumb_offset"] == 2500


def test_share_to_feed_false_is_kept():
    # False is the whole point of the toggle (keep the Reel out of the grid),
    # so it must not be dropped as "empty".
    payload = {"media_type": "REELS"}

    apply_placement(payload, PublishContent(extra={"share_to_feed": False}))

    assert payload["share_to_feed"] is False


def test_thumb_offset_zero_is_kept():
    payload = {"media_type": "REELS"}

    apply_placement(payload, PublishContent(extra={"thumb_offset": 0}))

    assert payload["thumb_offset"] == 0


def test_reels_only_parameters_are_dropped_on_a_story():
    payload = {"media_type": "STORIES", "video_url": "https://example.com/v.mp4"}

    apply_placement(payload, PublishContent(extra={"share_to_feed": True, "thumb_offset": 100}))

    assert "share_to_feed" not in payload
    assert "thumb_offset" not in payload


def test_alt_text_only_lands_on_a_feed_image():
    feed = {}
    reel = {"media_type": "REELS"}
    content = PublishContent(extra={"alt_text": "A mustard jar on a table"})

    apply_placement(feed, content)
    apply_placement(reel, content)

    assert feed["alt_text"] == "A mustard jar on a table"
    assert "alt_text" not in reel


def test_collaborators_are_not_allowed_on_a_story():
    story = {"media_type": "STORIES"}
    carousel = {"media_type": "CAROUSEL"}
    content = PublishContent(extra={"collaborators": ["bluemonkeymedia"]})

    apply_placement(story, content)
    apply_placement(carousel, content)

    assert "collaborators" not in story
    assert carousel["collaborators"] == ["bluemonkeymedia"]


def test_unknown_keys_from_platform_extra_are_never_forwarded():
    # platform_extra also carries the publish response and the composer's own
    # bookkeeping (post_type, ig_user_id, tags). None of that is an API
    # parameter and all of it would fail the container.
    payload = {"media_type": "REELS"}
    content = PublishContent(
        extra={
            "post_type": "reel",
            "ig_user_id": "17841400000000000",
            "tags": ["mosterd"],
            "id": "18000000000000000",
        }
    )

    apply_placement(payload, content)

    assert payload == {"media_type": "REELS"}


def test_empty_extra_leaves_the_payload_alone():
    payload = {"image_url": "https://example.com/a.jpg"}

    apply_placement(payload, PublishContent())

    assert payload == {"image_url": "https://example.com/a.jpg"}


def test_graph_provider_sends_share_to_feed_on_the_container():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post(
        "token",
        PublishContent(
            text="caption",
            media_urls=["https://example.com/v.mp4"],
            post_type=PostType.REEL,
            extra={"ig_user_id": "ig-1", "share_to_feed": False, "thumb_offset": 1200},
        ),
    )

    create_call = provider._request.call_args_list[0]
    payload = create_call.kwargs["json"]
    assert payload["media_type"] == "REELS"
    assert payload["share_to_feed"] is False
    assert payload["thumb_offset"] == 1200


def test_instagram_login_provider_publishes_a_story_without_reels_parameters():
    provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post(
        "token",
        PublishContent(
            media_urls=["https://example.com/v.mp4"],
            post_type=PostType.STORY,
            extra={"share_to_feed": True, "thumb_offset": 900},
        ),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "STORIES"
    assert payload["video_url"] == "https://example.com/v.mp4"
    assert "share_to_feed" not in payload
    assert "thumb_offset" not in payload


# ---------------------------------------------------------------------------
# Video detection on the URL path, not the URL
# ---------------------------------------------------------------------------

PRESIGNED_VIDEO = "https://s3.example.com/bucket/media_library/2026/09/abc-clip.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef"


def test_is_video_url_reads_the_path_and_ignores_the_query_string():
    assert is_video_url("https://example.com/v.mp4")
    assert is_video_url("https://example.com/V.MOV")
    assert is_video_url(PRESIGNED_VIDEO)
    assert not is_video_url("https://example.com/a.jpg?X-Amz-Signature=deadbeef")
    assert not is_video_url("https://example.com/clip.mp4.jpg")
    assert not is_video_url("")
    assert not is_video_url(None)


def test_graph_provider_publishes_a_presigned_story_video_as_video():
    # Upstream judged the whole URL, so a presigned Story video read as an
    # image and went out as image_url. The container then fails minutes later.
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post(
        "token",
        PublishContent(media_urls=[PRESIGNED_VIDEO], post_type=PostType.STORY, extra={"ig_user_id": "ig-1"}),
    )

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "STORIES"
    assert payload["video_url"] == PRESIGNED_VIDEO
    assert "image_url" not in payload


def test_instagram_login_provider_publishes_a_presigned_story_video_as_video():
    provider = InstagramLoginProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "container-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post("token", PublishContent(media_urls=[PRESIGNED_VIDEO], post_type=PostType.STORY))

    payload = provider._request.call_args_list[0].kwargs["json"]
    assert payload["media_type"] == "STORIES"
    assert payload["video_url"] == PRESIGNED_VIDEO
    assert "image_url" not in payload


def test_a_presigned_video_in_a_carousel_becomes_a_video_child():
    provider = InstagramProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        side_effect=[
            _resp({"id": "child-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "child-2"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "carousel-1"}),
            _resp({"status_code": "FINISHED"}),
            _resp({"id": "media-1"}),
        ]
    )

    provider.publish_post(
        "token",
        PublishContent(
            media_urls=[PRESIGNED_VIDEO, "https://example.com/a.jpg?X-Amz-Signature=deadbeef"],
            post_type=PostType.CAROUSEL,
            extra={"ig_user_id": "ig-1"},
        ),
    )

    first_child = provider._request.call_args_list[0].kwargs["json"]
    second_child = provider._request.call_args_list[2].kwargs["json"]
    assert first_child["media_type"] == "VIDEO"
    assert first_child["video_url"] == PRESIGNED_VIDEO
    assert "media_type" not in second_child
    assert second_child["image_url"].endswith("deadbeef")
