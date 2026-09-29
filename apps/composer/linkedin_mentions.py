"""Tag LinkedIn company pages from the composer (Blue Monkey Media fork).

New file, like the rest of the fork: ``views.py`` and ``urls.py`` only call in
here. How the tag ends up in the post is ``providers/linkedin_mentions.py``.

The flow: paste the link of a company page, the server asks LinkedIn which page
that is, and the composer keeps LinkedIn's name for it plus its URN in
``platform_extra["mentions"]``. On publish, that name in the caption becomes
the tag. Because LinkedIn only links a tag whose text matches the page's name
exactly, a save is refused while a tagged name is missing from the caption:
otherwise the tag would quietly publish as plain text.

Only the Company Page account type: the vanity-name lookup needs
``rw_organization_admin``, which only that provider asks for.
"""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from providers.linkedin_mentions import MAX_MENTIONS, clean_mentions, find_organization, name_in_text, page_key

LINKEDIN_MENTION_PLATFORMS = ("linkedin_company",)

# JSON list of {"name", "urn", "vanity"}. Absent means "the panel was not in
# this form, keep what is stored"; "[]" clears the tags.
MENTIONS_FIELD = "li_mentions_{acc_id}"


def _posted_mentions(request, acc_id):
    """The mentions in the form, or None when the panel was not in it."""
    field = MENTIONS_FIELD.format(acc_id=acc_id)
    if field not in request.POST:
        return None
    try:
        value = json.loads(request.POST.get(field) or "[]")
    except ValueError:
        value = []
    return clean_mentions(value if isinstance(value, list) else [])


def build_linkedin_extra(request, acc_id, existing=None):
    """``platform_extra`` for one LinkedIn Company Page account.

    Merges into what is stored, so a key some other path wrote survives.
    """
    extra = {**(existing or {})}
    mentions = _posted_mentions(request, acc_id)
    if mentions is None:
        return extra
    if mentions:
        extra["mentions"] = mentions
    else:
        extra.pop("mentions", None)
    return extra


def linkedin_mention_error(request, workspace, selected_ids):
    """First tag that has no place in its caption, or None.

    The caption is the account's override when it has one, else the post's.
    """
    if not selected_ids:
        return None

    from apps.social_accounts.models import SocialAccount

    accounts = SocialAccount.objects.filter(
        id__in=selected_ids,
        workspace=workspace,
        platform__in=LINKEDIN_MENTION_PLATFORMS,
    )
    for account in accounts:
        acc_id = str(account.id)
        mentions = _posted_mentions(request, acc_id)
        if not mentions:
            continue
        caption = request.POST.get(f"override_caption_{acc_id}", "").strip() or request.POST.get("caption", "")
        for mention in mentions:
            if not name_in_text(mention["name"], caption):
                return (
                    f'{account.account_name}: write "{mention["name"]}" in the text, exactly like this. '
                    "LinkedIn turns that name into the tag."
                )
    return None


@login_required
@require_GET
def linkedin_organization(request, workspace_id, account_id):
    """Which company page a pasted link points to, for the composer's tag panel."""
    from apps.credentials.models import resolve_platform_credentials
    from apps.social_accounts.models import SocialAccount
    from providers import get_provider

    from .views import _get_workspace

    workspace = _get_workspace(request, workspace_id)
    account = get_object_or_404(
        SocialAccount, id=account_id, workspace=workspace, platform__in=LINKEDIN_MENTION_PLATFORMS
    )

    key = page_key(request.GET.get("page", ""))
    if not key:
        return JsonResponse(
            {"error": "Paste the link of a LinkedIn company page, like linkedin.com/company/name."},
            status=400,
        )

    credentials = resolve_platform_credentials(account.platform, workspace.organization_id)
    provider = get_provider(account.platform, credentials)

    access_token = account.oauth_access_token
    if account.token_expires_at and account.is_token_expiring_soon:
        try:
            access_token = account.refresh_oauth_token(provider)
        except Exception:
            return JsonResponse({"error": "LinkedIn needs to be reconnected for this account."}, status=502)

    try:
        organization = find_organization(provider, access_token, key)
    except Exception:
        return JsonResponse({"error": "LinkedIn did not answer. Try again in a moment."}, status=502)
    if not organization:
        return JsonResponse({"error": "LinkedIn has no company page at that link."}, status=404)
    return JsonResponse({"organization": organization, "max": MAX_MENTIONS})
