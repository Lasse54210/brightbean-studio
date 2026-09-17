"""Template context for the entrance pages (Blue Monkey Media fork)."""

from .signup_policy import signup_is_open


def signup_open(request):
    """``signup_open`` for templates, so the login page can hide "Sign up"
    when nobody could use it anyway."""
    return {"signup_open": signup_is_open(request)}
