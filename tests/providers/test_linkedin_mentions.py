"""Tests for tagging LinkedIn company pages (Blue Monkey Media fork).

What matters: the tag lands where the page's name already stands, nothing is
added to the text, and everything around it is still escaped. A wrong tag
does not fail on LinkedIn; it publishes as plain text or truncates the post.
"""

from unittest.mock import MagicMock

from providers.linkedin import LinkedInProvider, escape_commentary
from providers.linkedin_mentions import (
    apply_mentions,
    clean_mentions,
    find_organization,
    name_in_text,
    page_key,
)
from providers.types import PublishContent

ORG = {"name": "Stichting Voorbeeld", "urn": "urn:li:organization:123", "vanity": "stichtingvoorbeeld"}


def _resp(data):
    return MagicMock(json=MagicMock(return_value=data))


class TestPageKey:
    def test_company_url(self):
        assert page_key("https://www.linkedin.com/company/stichtingvoorbeeld/") == "stichtingvoorbeeld"

    def test_url_without_scheme_and_with_a_subpage(self):
        assert page_key("linkedin.com/company/voorbeeld-bv/posts/?feedView=all") == "voorbeeld-bv"

    def test_showcase_and_school(self):
        assert page_key("https://nl.linkedin.com/showcase/some-brand") == "some-brand"
        assert page_key("https://www.linkedin.com/school/rijksuniversiteit-groningen/") == "rijksuniversiteit-groningen"

    def test_admin_url_gives_the_numeric_id(self):
        assert page_key("https://www.linkedin.com/company/12345/admin/dashboard/") == "12345"

    def test_bare_vanity_name(self):
        assert page_key("voorbeeld-bv") == "voorbeeld-bv"

    def test_a_person_or_another_site_is_refused(self):
        assert page_key("https://www.linkedin.com/in/someone/") is None
        assert page_key("https://example.com/company/x") is None
        assert page_key("") is None


class TestApplyMentions:
    def test_the_name_in_the_text_becomes_the_tag(self):
        text = escape_commentary("Vandaag is het Stichting Voorbeeld!")

        assert apply_mentions(text, [ORG]) == "Vandaag is het @[Stichting Voorbeeld](urn:li:organization:123)!"

    def test_a_typed_at_sign_is_part_of_the_tag(self):
        text = escape_commentary("Dank @Stichting Voorbeeld")

        assert apply_mentions(text, [ORG]) == "Dank @[Stichting Voorbeeld](urn:li:organization:123)"

    def test_only_the_first_occurrence_is_tagged(self):
        text = escape_commentary("Stichting Voorbeeld, echt Stichting Voorbeeld")

        result = apply_mentions(text, [ORG])

        assert result.count("urn:li:organization:123") == 1
        assert result.endswith("echt Stichting Voorbeeld")

    def test_a_missing_name_leaves_the_text_alone(self):
        text = escape_commentary("Nothing to see (here)")

        assert apply_mentions(text, [ORG]) == text

    def test_matching_is_case_sensitive_like_linkedin(self):
        text = escape_commentary("stichting voorbeeld")

        assert apply_mentions(text, [ORG]) == text

    def test_whole_words_only(self):
        short = {"name": "Voorbeeld", "urn": "urn:li:organization:1"}
        text = escape_commentary("Voorbeelden niet")

        assert apply_mentions(text, [short]) == text

    def test_reserved_characters_around_and_in_the_name_stay_escaped(self):
        acme = {"name": "Acme (NL)", "urn": "urn:li:organization:7"}
        text = escape_commentary("#ondernemen met Acme (NL) *vandaag*")

        result = apply_mentions(text, [acme])

        assert result == "\\#ondernemen met @[Acme \\(NL\\)](urn:li:organization:7) \\*vandaag\\*"

    def test_the_longer_name_wins_where_names_overlap(self):
        groningen = {"name": "Stichting Voorbeeld Groningen", "urn": "urn:li:organization:456"}
        text = escape_commentary("Stichting Voorbeeld Groningen en Stichting Voorbeeld")

        result = apply_mentions(text, [ORG, groningen])

        assert result == (
            "@[Stichting Voorbeeld Groningen](urn:li:organization:456) en "
            "@[Stichting Voorbeeld](urn:li:organization:123)"
        )

    def test_a_malformed_mention_is_ignored(self):
        text = escape_commentary("Stichting Voorbeeld")

        assert apply_mentions(text, [{"name": "Stichting Voorbeeld", "urn": "urn:li:person:abc"}]) == text
        assert apply_mentions(text, None) == text


def test_name_in_text_reads_plain_text():
    assert name_in_text("Acme (NL)", "met Acme (NL)!")
    assert not name_in_text("Acme", "Acmeco")


def test_clean_mentions_deduplicates_and_drops_junk():
    cleaned = clean_mentions([ORG, ORG, {"name": "", "urn": "urn:li:organization:9"}, "x"])

    assert cleaned == [ORG]


class TestFindOrganization:
    def test_vanity_name(self):
        provider = MagicMock()
        provider._request.return_value = _resp(
            {"elements": [{"id": 123, "localizedName": "Stichting Voorbeeld", "vanityName": "stichtingvoorbeeld"}]}
        )

        assert find_organization(provider, "token", "stichtingvoorbeeld") == ORG
        call = provider._request.call_args
        assert call.kwargs["params"] == {"q": "vanityName", "vanityName": "stichtingvoorbeeld"}

    def test_numeric_id(self):
        provider = MagicMock()
        provider._request.return_value = _resp(
            {
                "results": {
                    "123": {"id": 123, "localizedName": "Stichting Voorbeeld", "vanityName": "stichtingvoorbeeld"}
                }
            }
        )

        assert find_organization(provider, "token", "123") == ORG
        assert "organizationsLookup?ids=List(123)" in provider._request.call_args.args[1]

    def test_no_such_page(self):
        provider = MagicMock()
        provider._request.return_value = _resp({"elements": []})

        assert find_organization(provider, "token", "nope") is None


def test_published_commentary_carries_the_tag():
    provider = LinkedInProvider({"client_id": "id", "client_secret": "secret"})
    provider._request = MagicMock(
        return_value=MagicMock(headers={"x-restli-id": "urn:li:share:1"}, json=MagicMock(return_value={}))
    )

    provider.publish_post(
        "token",
        PublishContent(
            text="Morgen: Stichting Voorbeeld (21 november)",
            extra={"author": "urn:li:organization:999", "mentions": [ORG]},
        ),
    )

    body = provider._request.call_args.kwargs["json"]
    assert body["commentary"] == "Morgen: @[Stichting Voorbeeld](urn:li:organization:123) \\(21 november\\)"
