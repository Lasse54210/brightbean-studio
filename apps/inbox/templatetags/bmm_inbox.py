"""Template access to the post a message hangs off (Blue Monkey Media fork).

A filter rather than view context on purpose: the message list, the detail
panel and the active-filter notice all need the same answer, and three
templates asking one function keeps the upstream views at a one-line change.
"""

from django import template

from apps.inbox.post_reference import reference_for

register = template.Library()


@register.filter
def post_reference(message):
    """The post an inbox message hangs off, or None when it has none."""
    return reference_for(message)
