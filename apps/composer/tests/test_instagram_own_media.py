"""Own media per Instagram account (Blue Monkey Media fork, increment 3).

The form field is guarded like the placement: absent keeps, empty clears. The
save gate judges an account on its own files when it has them, and refuses a
file that has left the library instead of letting the publisher fall back to
something else than what was chosen.
"""

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.instagram_extras import (
    build_instagram_media,
    instagram_placement_error,
    own_media_preview,
)
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ACC = "11111111-1111-1111-1111-111111111111"
A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _post(fields=None):
    return RequestFactory().post("/", fields or {})


class BuildInstagramMediaTests(TestCase):
    def test_absent_field_keeps_the_stored_list(self):
        self.assertEqual(build_instagram_media(_post({}), ACC, [A]), [A])

    def test_empty_field_clears_the_stored_list(self):
        self.assertIsNone(build_instagram_media(_post({f"ig_media_ids_{ACC}": ""}), ACC, [A]))

    def test_ids_come_back_in_form_order_without_duplicates(self):
        got = build_instagram_media(_post({f"ig_media_ids_{ACC}": f"{B}, {A},{B}"}), ACC)
        self.assertEqual(got, [B, A])

    def test_anything_that_is_not_a_uuid_is_dropped(self):
        got = build_instagram_media(_post({f"ig_media_ids_{ACC}": f"{A},../etc,<script>,42"}), ACC)
        self.assertEqual(got, [A])

    def test_only_garbage_stores_nothing(self):
        self.assertIsNone(build_instagram_media(_post({f"ig_media_ids_{ACC}": "nope"}), ACC))


class Fixture(TestCase):
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
        self.acc_id = str(self.account.id)
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="hi")

    def _asset(self, media_type, **fields):
        return MediaAsset.objects.create(
            workspace=self.workspace,
            organization=self.org,
            filename=f"file.{media_type}",
            media_type=media_type,
            **fields,
        )

    def _attach(self, media_type):
        asset = self._asset(media_type)
        PostMedia.objects.create(post=self.post, media_asset=asset, position=self.post.media_attachments.count())
        return asset

    def _gate(self, fields):
        return instagram_placement_error(_post(fields), self.post, self.workspace, [self.account.id])


class OwnMediaAtTheSaveGateTests(Fixture):
    def test_own_files_are_judged_instead_of_the_posts(self):
        self._attach("image")
        own = self._asset("video")
        # A Reel on the post's image would be refused; on the account's own video it is fine.
        self.assertIsNotNone(self._gate({f"ig_placement_{self.acc_id}": "reel"}))
        self.assertIsNone(
            self._gate({f"ig_placement_{self.acc_id}": "reel", f"ig_media_ids_{self.acc_id}": str(own.id)})
        )

    def test_own_files_can_also_be_the_wrong_kind(self):
        self._attach("video")
        own = self._asset("image")
        error = self._gate({f"ig_placement_{self.acc_id}": "reel", f"ig_media_ids_{self.acc_id}": str(own.id)})
        self.assertIn("A Reel needs a video", error)

    def test_own_files_are_measured_too(self):
        self._attach("video")
        own = self._asset("video", width=1920, height=1080, duration=1.0)
        error = self._gate({f"ig_placement_{self.acc_id}": "reel", f"ig_media_ids_{self.acc_id}": str(own.id)})
        self.assertIn("3 s at the very least", error)

    def test_a_file_that_left_the_library_is_a_stop(self):
        self._attach("video")
        error = self._gate({f"ig_placement_{self.acc_id}": "reel", f"ig_media_ids_{self.acc_id}": A})
        self.assertIn("no longer in the media library", error)
        self.assertTrue(error.startswith("acct:"))

    def test_a_stored_list_is_judged_when_the_field_is_absent(self):
        # Panel in the form (placement present) but this build of the panel
        # somehow lacks the media field: the stored list still applies.
        self._attach("image")
        own = self._asset("video")
        PlatformPost.objects.create(post=self.post, social_account=self.account, platform_specific_media=[str(own.id)])
        self.assertIsNone(self._gate({f"ig_placement_{self.acc_id}": "reel"}))

    def test_an_emptied_list_falls_back_to_the_posts_files(self):
        self._attach("image")
        own = self._asset("video")
        PlatformPost.objects.create(post=self.post, social_account=self.account, platform_specific_media=[str(own.id)])
        error = self._gate({f"ig_placement_{self.acc_id}": "reel", f"ig_media_ids_{self.acc_id}": ""})
        self.assertIn("A Reel needs a video", error)


class OwnMediaPreviewTests(Fixture):
    def test_preview_describes_each_account_s_files_in_order(self):
        tall = self._asset("video", width=1080, height=1920, duration=12.0)
        square = self._asset("image", width=1080, height=1080)
        pp = PlatformPost.objects.create(
            post=self.post, social_account=self.account, platform_specific_media=[str(square.id), str(tall.id)]
        )
        preview = own_media_preview([pp], self.workspace)
        items = preview[self.acc_id]
        self.assertEqual([i["id"] for i in items], [str(square.id), str(tall.id)])
        self.assertEqual(items[1]["kind"], "video")
        self.assertEqual((items[1]["width"], items[1]["height"], items[1]["duration"]), (1080, 1920, 12.0))

    def test_accounts_without_a_list_are_left_out(self):
        pp = PlatformPost.objects.create(post=self.post, social_account=self.account)
        self.assertEqual(own_media_preview([pp], self.workspace), {})


class AccountMediaPickerViewTests(Fixture):
    def setUp(self):
        super().setUp()
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        WorkspaceMembership.objects.create(
            user=self.user, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.url = reverse("composer:account_media_picker", kwargs={"workspace_id": self.workspace.id})

    def test_requires_login(self):
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_lists_every_kind_of_file_with_its_measurements(self):
        self.client.force_login(self.user)
        self._asset("video", width=1080, height=1920, duration=9.0)
        self._asset("image", width=1080, height=1350)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("account-media-selected", body)
        self.assertIn("1080x1920", body)
        self.assertIn("1080x1350", body)
        # The JSON the panel receives rides along in data-item, HTML-escaped.
        self.assertIn("data-item=", body)
        self.assertIn("&quot;kind&quot;: &quot;video&quot;", body)

    def test_another_workspace_is_forbidden(self):
        self.client.force_login(self.user)
        other = Workspace.objects.create(organization=self.org, name="Other")
        url = reverse("composer:account_media_picker", kwargs={"workspace_id": other.id})
        self.assertEqual(self.client.get(url).status_code, 403)


class ComposeEditRenderTests(Fixture):
    """The panel and the modal actually render on the edit page, with the
    stored files in the JSON the panel reads. A template error here is the kind
    of thing only a browser would otherwise find."""

    def setUp(self):
        super().setUp()
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        WorkspaceMembership.objects.create(
            user=self.user, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.client.force_login(self.user)

    def test_edit_page_carries_the_accounts_own_files(self):
        tall = self._asset("video", width=1080, height=1920, duration=8.0)
        PlatformPost.objects.create(post=self.post, social_account=self.account, platform_specific_media=[str(tall.id)])
        url = reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Media for this account", body)
        self.assertIn("bmm-open-account-media", body)
        self.assertIn(reverse("composer:account_media_picker", kwargs={"workspace_id": self.workspace.id}), body)
        # own_media rides in the platform-extras JSON the composer boots from.
        self.assertIn("own_media", body)
        self.assertIn(str(tall.id), body)
