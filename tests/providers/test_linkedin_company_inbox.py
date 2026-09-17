"""A LinkedIn Company Page polls its own posts, not the member's.

The base provider derives the author from the profile behind the token, and
that profile is always a person. A Company Page account therefore polled the
posts of whoever connected it: the member's own comments landed in the Page's
inbox, and comments on the Page never arrived at all.
"""

from types import SimpleNamespace
from unittest.mock import patch

from apps.inbox.tasks import fetch_messages
from providers.linkedin import LinkedInProvider
from providers.linkedin_company import LinkedInCompanyProvider


def _account(platform_id="98765", token="token-abc"):
    return SimpleNamespace(account_platform_id=platform_id, oauth_access_token=token, platform="linkedin_company")


def test_company_page_polls_the_organization_urn():
    provider = LinkedInCompanyProvider({})
    with patch.object(LinkedInProvider, "_messages_for_author", return_value=[]) as author_call:
        provider.get_messages("token-abc", None, account=_account())

    assert author_call.call_args.args[1] == "urn:li:organization:98765"


def test_company_page_never_falls_back_to_the_member():
    """Without an account there is no Page to poll, so answer with nothing.

    Falling through to the base would quietly fill a Page inbox with the
    comments on the connecting member's personal posts.
    """
    provider = LinkedInCompanyProvider({})
    with patch.object(LinkedInProvider, "get_profile") as get_profile:
        assert provider.get_messages("token-abc", None) == []

    get_profile.assert_not_called()


def test_a_personal_account_still_polls_the_member():
    provider = LinkedInProvider({})
    with (
        patch.object(
            LinkedInProvider, "get_profile", return_value=SimpleNamespace(platform_id="member-1", name="", extra={})
        ),
        patch.object(LinkedInProvider, "_messages_for_author", return_value=[]) as author_call,
    ):
        provider.get_messages("token-abc")

    assert author_call.call_args.args[1] == "urn:li:person:member-1"


# ------------------------------------------------------- handing the account over


# Real classes and not mocks on purpose: ``fetch_messages`` decides by reading
# the signature, and a MagicMock reports ``(*args, **kwargs)`` for everything.


class _WantsAccount:
    def __init__(self):
        self.seen = {}

    def get_messages(self, access_token, since=None, account=None):
        self.seen = {"access_token": access_token, "since": since, "account": account}
        return []


class _Upstream:
    """A provider with upstream's signature, which would raise on account=."""

    def __init__(self):
        self.seen = {}

    def get_messages(self, access_token, since=None):
        self.seen = {"access_token": access_token, "since": since}
        return []


def test_fetch_messages_hands_the_account_to_a_provider_that_wants_it():
    account = _account()
    provider = _WantsAccount()

    fetch_messages(provider, account, None)

    assert provider.seen["account"] is account
    assert provider.seen["access_token"] == "token-abc"


def test_fetch_messages_calls_an_upstream_provider_exactly_as_upstream_does():
    account = _account()
    provider = _Upstream()

    fetch_messages(provider, account, None)

    assert "account" not in provider.seen
    assert provider.seen["access_token"] == "token-abc"


def test_the_real_company_provider_declares_the_account():
    """Guards the pairing: the seam only works if the signature really says so."""
    import inspect

    assert "account" in inspect.signature(LinkedInCompanyProvider({}).get_messages).parameters
    assert "account" not in inspect.signature(LinkedInProvider({}).get_messages).parameters
