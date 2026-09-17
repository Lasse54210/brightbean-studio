"""Registration is by invitation (Blue Monkey Media fork).

What is pinned down: nobody can create an account by walking up to the signup
page, an invitation link still can, ``ACCOUNT_OPEN_SIGNUP`` restores upstream's
behaviour, and the Google adapter answers the same question the same way.
Logging in is out of scope here because the gate never touches it.
"""

from datetime import timedelta

from allauth.socialaccount.models import SocialLogin
from django.test import Client, RequestFactory, TestCase, override_settings
from django.utils import timezone

from apps.accounts.adapters import AccountAdapter, SocialAccountAdapter
from apps.accounts.models import User
from apps.accounts.signup_policy import SESSION_KEY, signup_is_open
from apps.members.models import Invitation, OrgMembership
from apps.organizations.models import Organization

SIGNUP = "/accounts/signup/"


def _invite(org, email="new@example.com", **fields):
    inviter = User.objects.create_user(
        email=f"inviter-{org.pk}@example.com", password="x", tos_accepted_at=timezone.now()
    )
    fields.setdefault("expires_at", timezone.now() + timedelta(days=7))
    return Invitation.objects.create(organization=org, email=email, invited_by=inviter, **fields)


class SignupPolicyTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Agency")

    def _request_with_session(self, token=None):
        client = Client()
        if token:
            session = client.session
            session[SESSION_KEY] = token
            session.save()
        request = RequestFactory().get(SIGNUP)
        request.session = client.session
        return request

    def test_closed_by_default(self):
        self.assertFalse(signup_is_open(self._request_with_session()))

    def test_a_valid_invitation_in_the_session_opens_it(self):
        invitation = _invite(self.org)
        self.assertTrue(signup_is_open(self._request_with_session(invitation.token)))

    def test_an_expired_invitation_does_not_count(self):
        invitation = _invite(self.org)
        Invitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertFalse(signup_is_open(self._request_with_session(invitation.token)))

    def test_an_accepted_invitation_does_not_count(self):
        invitation = _invite(self.org, accepted_at=timezone.now())
        self.assertFalse(signup_is_open(self._request_with_session(invitation.token)))

    def test_an_unknown_token_does_not_count(self):
        self.assertFalse(signup_is_open(self._request_with_session("not-a-real-token")))

    @override_settings(ACCOUNT_OPEN_SIGNUP=True)
    def test_the_setting_restores_open_registration(self):
        self.assertTrue(signup_is_open(self._request_with_session()))

    def test_both_adapters_give_the_same_answer(self):
        request = self._request_with_session()
        self.assertFalse(AccountAdapter().is_open_for_signup(request))
        self.assertFalse(SocialAccountAdapter().is_open_for_signup(request, SocialLogin()))
        invitation = _invite(self.org)
        request = self._request_with_session(invitation.token)
        self.assertTrue(AccountAdapter().is_open_for_signup(request))
        self.assertTrue(SocialAccountAdapter().is_open_for_signup(request, SocialLogin()))


class SignupViewTests(TestCase):
    """The gate as a visitor meets it."""

    def setUp(self):
        self.org = Organization.objects.create(name="Agency")
        self.client = Client()

    def test_the_signup_page_says_no(self):
        response = self.client.get(SIGNUP)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertContains(response, "Registration is by invitation")

    def test_a_walk_up_post_creates_nobody(self):
        response = self.client.post(
            SIGNUP, {"email": "stranger@example.com", "password1": "a-long-enough-passphrase-42"}
        )
        self.assertTemplateUsed(response, "account/signup_closed.html")
        self.assertFalse(User.objects.filter(email="stranger@example.com").exists())
        # And so no stray "My Organization" either.
        self.assertEqual(Organization.objects.count(), 1)

    def test_the_login_page_hides_the_signup_link(self):
        response = self.client.get("/accounts/login/")
        self.assertNotContains(response, "Don't have an account?")

    def test_an_invited_visitor_can_still_sign_up_and_lands_in_the_inviting_org(self):
        invitation = _invite(self.org, email="colleague@example.com")
        # The invite page parks the token in the session, exactly as accept_invite does.
        self.client.get(f"/members/invite/{invitation.token}/accept/")

        response = self.client.get(SIGNUP)
        self.assertTemplateUsed(response, "account/signup.html")

        response = self.client.post(
            SIGNUP, {"email": "colleague@example.com", "password1": "a-long-enough-passphrase-42"}
        )
        self.assertNotEqual(response.status_code, 200, response.content[:200])
        user = User.objects.get(email="colleague@example.com")
        memberships = list(OrgMembership.objects.filter(user=user).values_list("organization__name", flat=True))
        # In the agency's organisation, and not also in a fresh default one.
        self.assertEqual(memberships, ["Agency"])

    @override_settings(ACCOUNT_OPEN_SIGNUP=True)
    def test_open_registration_shows_the_form_and_the_link_again(self):
        response = self.client.get(SIGNUP)
        self.assertTemplateUsed(response, "account/signup.html")
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Don't have an account?")
