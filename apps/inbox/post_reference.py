"""Which post an inbox message hangs off (Blue Monkey Media fork).

New file on purpose: the fork keeps its own code out of upstream modules so a
rebase stays cheap. This one holds no state and touches no request, so it can
be tested as a plain function over a message row.

The link itself is not new. ``InboxMessage.related_post`` has always been
there, and both paths that store a message fill it: the poll resolves it in
one query per batch (``apps/inbox/tasks.py``, ``resolve_related_posts``) and
the webhook does the same lookup per event (``apps/inbox/webhooks.py``). What
was missing is that no template ever read it, so every comment landed in one
undifferentiated feed and you could not see, let alone filter on, the post it
was written under. That is the whole gap this module closes.

Two things stay deliberately honest here:

* **An unlinked message is not "posted outside Brightbean".** A comment can
  lack a ``PlatformPost`` because the post really was published elsewhere, but
  also because it arrived before the lookup understood that provider's name for
  the post id, or because the publishing side never recorded one. So an
  unlinked reference says what it knows -- an id -- and claims nothing about
  where the post came from.
* **No permalink is invented.** Facebook and Instagram hand us
  ``post_permalink_url`` and we pass it on. For a provider that does not, the
  reference simply has no link rather than a guessed URL that 404s.
"""

from dataclasses import dataclass

from django.db.models import Q

# What providers call "the post this message hangs off", in the order we trust
# them: Facebook's stripped and raw form first, then YouTube's and LinkedIn's
# name for the same thing. This tuple is the single order, read both by the
# grouping here and by ``resolve_related_posts`` when it fills ``related_post``,
# so a provider added to one is never silently missing from the other.
POST_KEYS = ("stored_post_id", "post_id", "video_id", "post_urn")

# Caption text kept in a chip before it gets an ellipsis. Long enough to
# recognise a post by, short enough to sit on one line next to a date.
LABEL_CHARS = 48


def post_key(extra: dict | None) -> str:
    """The id of the post a message hangs off, or "" when there is none.

    A DM has no post, and neither does a mention that arrived without one, so
    an empty string is a normal answer and not a failure.
    """
    for name in POST_KEYS:
        value = (extra or {}).get(name)
        if value:
            return str(value)
    return ""


def filter_by_post(queryset, key: str):
    """Narrow a message queryset to one post.

    Matches on every key in ``POST_KEYS`` rather than only the one the
    reference was built from: Facebook stores both a page-prefixed ``post_id``
    and the stripped ``stored_post_id``, and a feed filtered on one of the two
    must not drop messages that carry the other.
    """
    if not key:
        return queryset
    match = Q()
    for name in POST_KEYS:
        match |= Q(**{f"extra__{name}": key})
    return queryset.filter(match)


def _first_line(text: str) -> str:
    """The first non-empty line of a caption, trimmed to chip length."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= LABEL_CHARS else line[: LABEL_CHARS - 1].rstrip() + "…"
    return ""


@dataclass(frozen=True)
class PostReference:
    """What the inbox can say about the post a message hangs off."""

    key: str
    label: str
    permalink: str
    platform_post: object | None
    published_at: object | None

    @property
    def is_linked(self) -> bool:
        """True when the post exists in this Brightbean as a PlatformPost."""
        return self.platform_post is not None

    @property
    def post_id(self):
        """The composer ``Post`` id, for a link into the composer."""
        return self.platform_post.post_id if self.platform_post is not None else None


def reference_for(message) -> PostReference | None:
    """Build the post reference for one message, or None when it has no post.

    Reads only ``message.extra`` and ``message.related_post``; a caller that
    renders a list should ``select_related("related_post", "related_post__post")``
    so this stays free of per-row queries.
    """
    key = post_key(message.extra)
    platform_post = message.related_post
    if not key and platform_post is None:
        return None

    label = ""
    published_at = None
    if platform_post is not None:
        post = platform_post.post
        label = _first_line(platform_post.platform_specific_caption or post.caption or post.title)
        published_at = platform_post.published_at or post.published_at
        if not key:
            key = platform_post.platform_post_id

    if not label:
        # Nothing readable to show, so show the id. Truncated from the front:
        # platform ids share long prefixes and differ at the end.
        label = f"Post {key[-12:]}" if key else "Post"

    return PostReference(
        key=key,
        label=label,
        permalink=str((message.extra or {}).get("post_permalink_url") or ""),
        platform_post=platform_post,
        published_at=published_at,
    )


def reference_for_key(key: str) -> PostReference:
    """A bare reference for a post id with no message to describe it.

    The active-filter notice needs a label even when the filter matches
    nothing left in view (everything archived, say), and then the id is all
    there is to show.
    """
    return PostReference(
        key=key,
        label=f"Post {key[-12:]}" if key else "Post",
        permalink="",
        platform_post=None,
        published_at=None,
    )
