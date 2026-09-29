"""Tag LinkedIn company pages in a post (Blue Monkey Media fork).

New file, like the rest of the fork. ``providers/linkedin.py`` only calls
``apply_mentions`` from ``_build_post_body``; the composer and its lookup view
live in ``apps/composer/linkedin_mentions.py``.

How a tag works on LinkedIn: the Posts API reads ``commentary`` as "little
text", where ``@[Name](urn:li:organization:123)`` renders as a link to that
page. Everything else in the commentary is escaped by ``escape_commentary``,
which is why a typed "@Name" came out as plain text.

LinkedIn only links the tag when the text between the brackets matches the
page's name exactly, case included; otherwise it shows the text unlinked. So
the composer stores the name LinkedIn itself returned, and the tag goes where
that name already stands in the caption. We never add words to the text.

Only organizations (companies, showcase pages, schools). A person needs a
member URN, and there is no API that finds one for someone else.

Verified on 2026-09-29 against the Posts API ("Mentions and Hashtags using
Posts commentary"), the little text format and the Organization Lookup API.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from .linkedin import API_BASE, LINKEDIN_HEADERS, escape_commentary

ORGANIZATION_URN = re.compile(r"^urn:li:organization:\d+$")

# linkedin.com/company/<vanity>, /showcase/<vanity> and /school/<vanity>. The
# admin URL of a page you manage carries the numeric id in the same place.
_PAGE_PATH = re.compile(r"^/(?:company|showcase|school)/([^/?#]+)")

# One post will not sensibly tag more than this, and it keeps a pasted list
# from turning into a thousand lookups.
MAX_MENTIONS = 20


def page_key(text):
    """The vanity name or numeric id in a pasted page link, or None.

    Accepts a full URL, one without the scheme, or the bare vanity name.
    """
    text = (text or "").strip()
    if not text:
        return None
    if "/" in text or "linkedin." in text:
        parsed = urlparse(text if "://" in text else "https://" + text)
        if not (parsed.hostname or "").endswith("linkedin.com"):
            return None
        match = _PAGE_PATH.match(parsed.path)
        if not match:
            return None
        text = unquote(match.group(1))
    text = text.strip()
    if not re.fullmatch(r"[\w.\-]{1,100}", text):
        return None
    return text


def find_organization(provider, access_token, key):
    """``{"name", "urn", "vanity"}`` for a page, or None when LinkedIn has none.

    A numeric key goes through ``organizationsLookup``, a vanity name through
    the vanityName finder. Both return only the public fields, so neither needs
    the member to administer the page; they do need ``rw_organization_admin``,
    which the Company Page provider asks for anyway. API errors propagate.
    """
    if key.isdigit():
        resp = provider._request(
            "GET",
            f"{API_BASE}/rest/organizationsLookup?ids=List({key})",
            access_token=access_token,
            headers=LINKEDIN_HEADERS,
        )
        org = (resp.json().get("results") or {}).get(key)
    else:
        resp = provider._request(
            "GET",
            f"{API_BASE}/rest/organizations",
            access_token=access_token,
            headers=LINKEDIN_HEADERS,
            params={"q": "vanityName", "vanityName": key},
        )
        elements = resp.json().get("elements") or []
        org = elements[0] if elements else None
    if not org or not org.get("id"):
        return None
    name = org.get("localizedName") or ""
    if not name:
        localized = (org.get("name") or {}).get("localized") or {}
        name = next(iter(localized.values()), "")
    if not name:
        return None
    return {
        "name": name,
        "urn": f"urn:li:organization:{org['id']}",
        "vanity": org.get("vanityName") or "",
    }


def clean_mentions(value):
    """The stored mentions that are safe to publish, in order, deduplicated."""
    mentions = []
    seen = set()
    for item in value or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        urn = str(item.get("urn") or "").strip()
        if not name or len(name) > 200 or not ORGANIZATION_URN.match(urn) or urn in seen:
            continue
        seen.add(urn)
        mentions.append({"name": name, "urn": urn, "vanity": str(item.get("vanity") or "")[:100]})
    return mentions[:MAX_MENTIONS]


def _name_pattern(name):
    """Where ``name`` stands in escaped commentary, with an optional typed "@".

    Whole words only, so tagging "Voorbeeld" leaves "Voorbeelden" alone.
    """
    escaped = re.escape(escape_commentary(name))
    return re.compile(r"(?<!\w)(?:\\@)?" + escaped + r"(?!\w)")


def name_in_text(name, text):
    """Whether the tag for ``name`` has a place to go in plain ``text``."""
    return bool(_name_pattern(name).search(escape_commentary(text or "")))


def apply_mentions(escaped, mentions):
    """Turn the first occurrence of each page name in ``escaped`` into a tag.

    ``escaped`` is commentary that has been through ``escape_commentary``.
    A name that is not in the text is skipped: the composer refuses to save
    that, so it only happens to a caption edited some other way afterwards.

    Longer names go first and a tag, once placed, is parked behind a
    placeholder, so "Stichting Voorbeeld Groningen" is not half-eaten by a
    tag for "Stichting Voorbeeld".
    """
    mentions = clean_mentions(mentions)
    if not escaped or not mentions:
        return escaped
    placed = []
    for mention in sorted(mentions, key=lambda m: len(m["name"]), reverse=True):
        token = f"@[{escape_commentary(mention['name'])}]({mention['urn']})"
        placeholder = f"\x00{len(placed)}\x00"
        escaped, count = _name_pattern(mention["name"]).subn(placeholder, escaped, count=1)
        if count:
            placed.append(token)
    for index, token in enumerate(placed):
        escaped = escaped.replace(f"\x00{index}\x00", token)
    return escaped
