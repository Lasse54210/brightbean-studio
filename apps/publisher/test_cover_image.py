"""Tests for the video cover image (Blue Monkey Media fork).

The point being pinned down: the composer stores a MediaAsset id, Instagram
needs a public JPEG URL, and a cover that cannot be turned into one must never
cost the post itself.
"""

import io
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from PIL import Image

from apps.media_library.models import MediaAsset
from apps.organizations.models import Organization
from apps.publisher.cover_image import MAX_SIDE, apply_cover_image, to_cover_jpeg
from apps.workspaces.models import Workspace


def _image_bytes(fmt, size=(1080, 1920), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


class ApplyInstagramCoverTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.override = override_settings(MEDIA_ROOT=self.media_root, APP_URL="https://bb.example")
        self.override.enable()
        self.org = Organization.objects.create(name="Cover Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Cover Workspace")

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def _asset(self, data, filename, mime, media_type="image"):
        asset = MediaAsset(
            workspace=self.workspace,
            organization=self.org,
            filename=filename,
            media_type=media_type,
            mime_type=mime,
            file_size=len(data),
        )
        asset.file.save(filename, ContentFile(data), save=False)
        asset.save()
        return asset

    def test_a_jpeg_is_used_as_it_is(self):
        asset = self._asset(_image_bytes("JPEG"), "cover.jpg", "image/jpeg")
        extra = {"cover_asset_id": str(asset.id), "post_type": "reel"}

        apply_cover_image(extra, "instagram")

        self.assertEqual(extra["cover_url"], f"https://bb.example{asset.file.url}")
        self.assertNotIn("cover_asset_id", extra)

    def test_a_png_is_converted_to_a_jpeg_once(self):
        asset = self._asset(_image_bytes("PNG", mode="RGBA"), "cover.png", "image/png")

        first = apply_cover_image({"cover_asset_id": str(asset.id)}, "instagram")["cover_url"]
        second = apply_cover_image({"cover_asset_id": str(asset.id)}, "instagram_login")["cover_url"]

        self.assertTrue(first.endswith(f"video-covers/{asset.id}.jpg"))
        self.assertEqual(first, second)
        with open(f"{self.media_root}/video-covers/{asset.id}.jpg", "rb") as fh:
            self.assertEqual(Image.open(fh).format, "JPEG")

    def test_a_missing_asset_publishes_without_a_cover(self):
        extra = {"cover_asset_id": "33333333-3333-3333-3333-333333333333", "thumb_offset": 1200}

        apply_cover_image(extra, "instagram")

        self.assertNotIn("cover_url", extra)
        self.assertNotIn("cover_asset_id", extra)
        # The frame still goes out, so the Reel keeps the second-best cover.
        self.assertEqual(extra["thumb_offset"], 1200)

    def test_a_video_is_never_sent_as_a_cover(self):
        asset = self._asset(b"not really a video", "clip.mp4", "video/mp4", media_type="video")
        extra = {"cover_asset_id": str(asset.id)}

        apply_cover_image(extra, "instagram")

        self.assertNotIn("cover_url", extra)

    def test_a_broken_image_does_not_raise(self):
        asset = self._asset(b"\x89PNG broken", "broken.png", "image/png")
        extra = {"cover_asset_id": str(asset.id)}

        apply_cover_image(extra, "instagram")

        self.assertNotIn("cover_url", extra)

    def test_facebook_gets_the_same_url(self):
        asset = self._asset(_image_bytes("JPEG"), "cover.jpg", "image/jpeg")
        extra = {"cover_asset_id": str(asset.id)}

        apply_cover_image(extra, "facebook")

        self.assertEqual(extra["cover_url"], f"https://bb.example{asset.file.url}")

    def test_other_platforms_lose_the_id_and_get_nothing(self):
        asset = self._asset(_image_bytes("JPEG"), "cover.jpg", "image/jpeg")
        extra = {"cover_asset_id": str(asset.id)}

        apply_cover_image(extra, "youtube")

        self.assertEqual(extra, {})


class ToCoverJpegTests(TestCase):
    def test_a_large_image_is_scaled_to_the_long_side(self):
        data = to_cover_jpeg(io.BytesIO(_image_bytes("PNG", size=(2160, 3840))))

        img = Image.open(io.BytesIO(data))
        self.assertEqual(img.format, "JPEG")
        self.assertEqual(max(img.size), MAX_SIDE)

    def test_transparency_becomes_white(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(buffer, format="PNG")

        img = Image.open(io.BytesIO(to_cover_jpeg(io.BytesIO(buffer.getvalue()))))

        self.assertEqual(img.getpixel((5, 5)), (255, 255, 255))
