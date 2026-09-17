"""Who may create an account here.

New file on purpose (Blue Monkey Media fork): the rule lives in one place and
the two allauth adapters, the login page and the closed-signup page all read
it, so there is exactly one answer to "can this person sign up".

Upstream leaves registration open. That is the right default for a hosted
product and the wrong one for an agency's own instance: every stranger who
finds the login page can create an account, and ``apps/accounts/signals.py``
then hands them a fresh organisation with storage on our bucket. Worse, a
colleague who simply logs in lands in that same fresh organisation and never
shows up under the agency's team members, which is how two people ended up
invisible to their own admin.

So registration is closed unless one of two things holds:

* ``ACCOUNT_OPEN_SIGNUP`` is true in the environment (upstream's behaviour), or
* the visitor arrived through an invitation link. ``members.views.accept_invite``
  parks the token in the session before sending them to sign up, and the
  post-signup signal accepts it. A valid token is therefore proof that someone
  inside an organisation asked for this account.

Logging in is never affected: this gate only decides whether a *new* account
may be created, for the email form and for Google alike.
"""

from django.conf import settings

SESSION_KEY = "pending_invite_token"


def pending_invitation(request):
    """The invitation this visitor is here to accept, or None.

    Reads the same session key the invite flow writes. Expired and already
    accepted invitations do not count: the token still sits in the session
    then, but it no longer vouches for anyone.
    """
    session = getattr(request, "session", None)
    token = session.get(SESSION_KEY) if session is not None else None
    if not token:
        return None

    from apps.members.models import Invitation

    invitation = Invitation.objects.filter(token=token, accepted_at__isnull=True).first()
    if invitation is None or invitation.is_expired:
        return None
    return invitation


def signup_is_open(request):
    """Whether a new account may be created on this request."""
    if getattr(settings, "ACCOUNT_OPEN_SIGNUP", False):
        return True
    return pending_invitation(request) is not None
