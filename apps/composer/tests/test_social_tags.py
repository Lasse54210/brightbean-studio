"""Tests for tagging from the composer (Blue Monkey Media fork, 2026-09-29).

Instagram: usernames typed in the panel are stored bare and a bad one stops
the save, because Instagram fails the whole container on an unknown username.
LinkedIn: a tagged company page has to stand in the caption by its exact name,
or LinkedIn would publish the tag as plain text; the save says so first.
"""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.instagram_extras import build_instagram_extra, instagram_tag_error, parse_usernames
from apps.composer.linkedin_mentions import build_linkedin_extra, linkedin_mention_error
from apps.composer.models import PlatformPost, Post
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.organizations.models import Organization
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

ACC = "11111111-1111-1111-1111-111111111111"
ORG = {"name": "Stichting Voorbeeld", "urn": "urn:li:organization:123", "vanity": "stichtingvoorbeeld"}


def _post(fields=None):
    return RequestFactory().post("/", fields or {})


class ParseUsernamesTests(TestCase):
    def test_takes_at_signs_commas_spaces_and_profile_links(self):
        names, bad = parse_usernames("@Anna.B, piet_1  https://www.instagram.com/klaas/?hl=nl\n@anna.b")

        self.assertEqual(names, ["anna.b", "piet_1", "klaas"])
        self.assertEqual(bad, [])

    def test_rejects_what_cannot_be_a_username(self):
        _names, bad = parse_usernames("anna, piet!")

        self.assertEqual(bad, ["piet!"])


class InstagramTagExtraTests(TestCase):
    def test_tags_and_collaborators_are_stored_as_usernames(self):
        extra = build_instagram_extra(
            _post(
                {
                    f"ig_placement_{ACC}": "auto",
                    f"ig_user_tags_{ACC}": "@anna, @piet",
                    f"ig_collaborators_{ACC}": "@klaas",
                }
            ),
            ACC,
        )

        self.assertEqual(extra["user_tags"], ["anna", "piet"])
        self.assertEqual(extra["collaborators"], ["klaas"])

    def test_an_emptied_field_clears_the_stored_tags(self):
        extra = build_instagram_extra(
            _post({f"ig_placement_{ACC}": "auto", f"ig_user_tags_{ACC}": ""}),
            ACC,
            {"user_tags": ["anna"]},
        )

        self.assertNotIn("user_tags", extra)

    def test_a_form_without_the_field_keeps_the_stored_tags(self):
        extra = build_instagram_extra(_post({f"ig_placement_{ACC}": "auto"}), ACC, {"user_tags": ["anna"]})

        self.assertEqual(extra["user_tags"], ["anna"])

    def test_errors(self):
        self.assertIsNone(instagram_tag_error(_post({f"ig_user_tags_{ACC}": "@anna"}), ACC))
        self.assertIn("piet!", instagram_tag_error(_post({f"ig_user_tags_{ACC}": "@anna, piet!"}), ACC))
        self.assertIn("3 collaborators", instagram_tag_error(_post({f"ig_collaborators_{ACC}": "a, b, c, d"}), ACC))
        many = ", ".join(f"user{i}" for i in range(21))
        self.assertIn("20 people", instagram_tag_error(_post({f"ig_user_tags_{ACC}": many}), ACC))


class LinkedInExtraTests(TestCase):
    def test_mentions_are_stored_and_other_keys_survive(self):
        extra = build_linkedin_extra(
            _post({f"li_mentions_{ACC}": json.dumps([ORG])}),
            ACC,
            {"poll_question": "Q?"},
        )

        self.assertEqual(extra, {"poll_question": "Q?", "mentions": [ORG]})

    def test_an_empty_list_clears_and_a_missing_field_keeps(self):
        cleared = build_linkedin_extra(_post({f"li_mentions_{ACC}": "[]"}), ACC, {"mentions": [ORG]})
        kept = build_linkedin_extra(_post(), ACC, {"mentions": [ORG]})

        self.assertNotIn("mentions", cleared)
        self.assertEqual(kept["mentions"], [ORG])

    def test_garbage_in_the_field_stores_nothing(self):
        extra = build_linkedin_extra(_post({f"li_mentions_{ACC}": "not json"}), ACC)

        self.assertNotIn("mentions", extra)


class LinkedInMentionErrorTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=org, name="Test Workspace")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="linkedin_company",
            account_platform_id="999",
            account_name="Stichting Voorbeeld",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.acc_id = str(self.account.id)

    def _error(self, caption, override=""):
        return linkedin_mention_error(
            _post(
                {
                    "caption": caption,
                    f"override_caption_{self.acc_id}": override,
                    f"li_mentions_{self.acc_id}": json.dumps([ORG]),
                }
            ),
            self.workspace,
            [self.acc_id],
        )

    def test_a_name_in_the_caption_passes(self):
        self.assertIsNone(self._error("Het is Stichting Voorbeeld!"))

    def test_a_missing_name_is_refused_and_says_which(self):
        error = self._error("Het is stichting voorbeeld!")

        self.assertIn('"Stichting Voorbeeld"', error)

    def test_the_account_override_is_what_counts(self):
        self.assertIsNone(self._error("Iets anders", override="Met Stichting Voorbeeld"))
        self.assertIsNotNone(self._error("Stichting Voorbeeld", override="Iets anders"))


class ComposerHttpTests(TestCase):
    """The panels render, the lookup answers and the save refuses a lost tag.
    A template or wiring error here is otherwise something only a browser finds."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.org = Organization.objects.create(name="Test Org")
        self.workspace = Workspace.objects.create(organization=self.org, name="Test Workspace")
        OrgMembership.objects.create(user=self.user, organization=self.org, org_role=OrgMembership.OrgRole.OWNER)
        WorkspaceMembership.objects.create(
            user=self.user, workspace=self.workspace, workspace_role=WorkspaceMembership.WorkspaceRole.OWNER
        )
        self.linkedin = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="linkedin_company",
            account_platform_id="999",
            account_name="Stichting Voorbeeld",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.instagram = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-1",
            account_name="stichtingvoorbeeld",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def test_edit_page_renders_both_panels_with_the_stored_tags(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, caption="Stichting Voorbeeld")
        PlatformPost.objects.create(post=post, social_account=self.linkedin, platform_extra={"mentions": [ORG]})
        PlatformPost.objects.create(post=post, social_account=self.instagram, platform_extra={"user_tags": ["anna"]})

        url = reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": post.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Tag companies", body)
        self.assertIn("Tag people", body)
        self.assertIn(f"/workspace/{self.workspace.id}/compose/linkedin-organization/", body)
        self.assertIn("urn:li:organization:123", body)
        self.assertIn('"anna"', body)

    def _lookup(self, page):
        url = reverse(
            "composer:linkedin_organization",
            kwargs={"workspace_id": self.workspace.id, "account_id": self.linkedin.id},
        )
        return self.client.get(url, {"page": page})

    def test_lookup_answers_with_linkedin_s_name_and_urn(self):
        with (
            patch("providers.get_provider", return_value=MagicMock()),
            patch("apps.composer.linkedin_mentions.find_organization", return_value=ORG) as find,
        ):
            response = self._lookup("https://www.linkedin.com/company/stichtingvoorbeeld/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["organization"], ORG)
        self.assertEqual(find.call_args.args[2], "stichtingvoorbeeld")

    def test_lookup_refuses_a_person_link(self):
        response = self._lookup("https://www.linkedin.com/in/someone/")

        self.assertEqual(response.status_code, 400)

    def test_lookup_is_only_for_company_page_accounts(self):
        url = reverse(
            "composer:linkedin_organization",
            kwargs={"workspace_id": self.workspace.id, "account_id": self.instagram.id},
        )
        self.assertEqual(self.client.get(url, {"page": "voorbeeld-bv"}).status_code, 404)

    def _save(self, caption):
        return self.client.post(
            reverse("composer:save_post", kwargs={"workspace_id": self.workspace.id}),
            data={
                "action": "save_draft",
                "title": "Test post",
                "caption": caption,
                "selected_accounts": str(self.linkedin.id),
                f"li_mentions_{self.linkedin.id}": json.dumps([ORG]),
            },
        )

    def test_save_refuses_a_tag_whose_name_is_not_in_the_text(self):
        response = self._save("Morgen is het zover")

        self.assertEqual(response.status_code, 400)
        self.assertIn("linkedin_mentions", response.json()["errors"])

    def test_save_stores_the_tag(self):
        response = self._save("Morgen is het Stichting Voorbeeld")

        self.assertIn(response.status_code, (200, 204, 302))
        pp = PlatformPost.objects.get(social_account=self.linkedin)
        self.assertEqual(pp.platform_extra["mentions"], [ORG])
