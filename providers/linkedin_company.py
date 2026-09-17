"""LinkedIn provider variant for Company Page posting.

Lists organizations the authenticated member administers, lets the user
pick one, and publishes to that Company Page via the organization URN.
"""

from __future__ import annotations

import logging

from .linkedin import API_BASE, LINKEDIN_HEADERS, LinkedInProvider
from .types import InboxMessage

logger = logging.getLogger(__name__)


class LinkedInCompanyProvider(LinkedInProvider):
    """LinkedIn provider scoped to Company Page posting."""

    @property
    def platform_name(self) -> str:
        return "LinkedIn (Company Page)"

    @property
    def required_scopes(self) -> list[str]:
        return [
            "r_basicprofile",
            "w_member_social",
            "w_organization_social",
            "r_organization_social",
            "rw_organization_admin",
        ]

    def get_user_pages(self, access_token: str) -> list[dict]:
        resp = self._request(
            "GET",
            f"{API_BASE}/v2/organizationalEntityAcls"
            "?q=roleAssignee&role=ADMINISTRATOR"
            "&projection=(elements*(organizationalTarget~(id,localizedName,vanityName,logoV2(original~:playableStreams))))",
            access_token=access_token,
            headers=LINKEDIN_HEADERS,
        )
        data = resp.json()
        pages: list[dict] = []
        for element in data.get("elements", []):
            org = element.get("organizationalTarget~", {})
            org_urn = element.get("organizationalTarget", "")
            org_id = org_urn.split(":")[-1] if org_urn else org.get("id", "")
            logo_url = None
            logo = org.get("logoV2", {}).get("original~", {})
            elements = logo.get("elements", [])
            if elements:
                identifiers = elements[0].get("identifiers", [])
                if identifiers:
                    logo_url = identifiers[0].get("identifier")
            pages.append(
                {
                    "id": str(org_id),
                    "name": org.get("localizedName", ""),
                    "handle": org.get("vanityName", ""),
                    "access_token": access_token,
                    "picture": logo_url,
                }
            )
        return pages

    def get_messages(self, access_token: str, since=None, account=None) -> list[InboxMessage]:
        """Comments on this Page's posts, not on the member's own.

        Blue Monkey Media fork. The base provider derives the author from the
        profile behind the token, which is a person, so a Company Page account
        was polling the posts of whoever connected it: the member's comments
        landed in the Page's inbox and the Page's own comments never arrived at
        all.

        ``account`` is optional because upstream's signature has no room for it
        and we do not want to change every provider (see
        ``apps/inbox/tasks.py``, ``_fetch_messages``). Without one there is no
        way to know *which* Page is meant, and answering with the member's
        comments would be worse than answering with nothing.
        """
        organization_id = getattr(account, "account_platform_id", "")
        if not organization_id:
            logger.warning("LinkedIn Company inbox skipped: no account given, so no organization to poll")
            return []
        return self._messages_for_author(access_token, f"urn:li:organization:{organization_id}", since)
