"""What Instagram does to a file at a given placement.

New file on purpose: the fork keeps its own code out of upstream modules so a
rebase stays cheap. Nothing in here imports from Django, and nothing in here
touches a request or the database -- it is arithmetic on four numbers, so it
can be tested as arithmetic and mirrored one-to-one in the panel's JavaScript.

**Every number below was read off Meta's IG User Media reference on
2026-09-11.** That is the whole reason this module exists: when Meta moves a
limit there is exactly one file to correct, instead of a constant buried in a
template and a second copy of it in a validator.

What this module does *not* do is convert anything. A file that is the wrong
shape stays the wrong shape; you get told what Instagram will do with it.
Re-encoding is increment 4 of bmm/instagram-plaatsingen-plan.md and is a much
bigger thing than a warning.

One caveat that shapes the whole design: ``MediaAsset.width``, ``height`` and
``duration`` default to 0 and are filled in by a background ffprobe task
(``apps/media_library/tasks.py``). A file that was just uploaded therefore
measures 0 until that worker has run. Zero means *unknown* here, never
"zero pixels wide", and unknown produces no findings at all -- a blocking
check that fires on missing metadata would stop every fresh upload.
"""

from dataclasses import dataclass

BLOCK = "block"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    """One thing worth saying about a file at a placement.

    ``BLOCK`` means Instagram refuses it: the docs word these as "required" or
    as a hard minimum/maximum, and the refusal arrives asynchronously as
    "container failed" minutes after you scheduled, which is precisely the
    failure this module exists to pull forward. ``WARN`` means it publishes but
    not the way you probably meant -- cropping, mostly.
    """

    level: str
    message: str

    @property
    def blocks(self):
        return self.level == BLOCK


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

NINE_BY_SIXTEEN = 9 / 16

# Aspect ratio is always width / height, so a portrait file is below 1.
#
# `required`    -- outside this and the container is refused. Meta words these
#                  as "Required aspect ratio is between X and Y".
# `recommended` -- a single value to aim for, or a range that publishes without
#                  cropping. Outside it is a warning, never a stop.
# `duration`    -- (minimum, maximum) in seconds, or None where the reference
#                  gives no numbers for this media type.
MEDIA_SPECS = {
    # "Required aspect ratio is between 0.01:1 and 10:1 but we recommend 9:16"
    # "Duration: 15 mins maximum, 3 seconds minimum"
    "reel": {
        "label": "A Reel",
        "required_ratio": (0.01, 10.0),
        "recommended_ratio": NINE_BY_SIXTEEN,
        "duration": (3.0, 15 * 60.0),
    },
    # "Required aspect ratio is between 0.1:1 and 10:1 but we recommend 9:16"
    # "Duration: 60 seconds maximum, 3 seconds minimum"
    # Note the range is 0.1, not the Reel's 0.01. That is not a typo on our
    # side; the reference really does use different bounds for the two.
    "story_video": {
        "label": "A Story video",
        "required_ratio": (0.1, 10.0),
        "recommended_ratio": NINE_BY_SIXTEEN,
        "duration": (3.0, 60.0),
    },
    # Story images get a recommendation and no required range at all, so there
    # is nothing here that can block.
    "story_image": {
        "label": "A Story image",
        "required_ratio": None,
        "recommended_ratio": NINE_BY_SIXTEEN,
        "duration": None,
    },
    # "Aspect ratio: Must be within a 4:5 to 1.91:1 range"
    #
    # Worded as hard as the Reel's range, and still a warning rather than a
    # stop. In practice Instagram crops a feed image into that window instead
    # of refusing the container, and a false block costs a code change while a
    # false warning costs a sentence. If a feed image ever does come back as
    # "container failed", this is the first line to reconsider.
    "feed_image": {
        "label": "A feed image",
        "required_ratio": None,
        "recommended_ratio": (4 / 5, 1.91),
        "duration": None,
    },
}


# Which spec a placement is judged against. A placement is what you pick in the
# composer; a spec is what the reference has a table for, and they are not the
# same list: a Story is two different sets of numbers depending on what you
# hang under it, and a carousel has no table of its own -- the reference says
# to build carousel items as ordinary image or video containers.
def _spec_for(placement, kind):
    if placement == "reel":
        return MEDIA_SPECS["reel"]
    if placement == "story":
        return MEDIA_SPECS["story_image"] if kind == "image" else MEDIA_SPECS["story_video"]
    if placement in ("image", "carousel"):
        return MEDIA_SPECS["feed_image"]
    return None


# Meta's own maximum, quoted verbatim from the `children` parameter: "Carousels
# can have up to 10 total images, vidoes, or a mix of the two." Kept here
# rather than in instagram_extras.py so that all the platform numbers sit
# together.
CAROUSEL_MAX_ITEMS = 10

# How close a ratio has to be before it counts as the recommended one. 1080x1920
# is exactly 9:16; a file that was scaled or trimmed lands a hair off, and
# warning about that would be noise rather than signal.
RATIO_TOLERANCE = 0.015


# ---------------------------------------------------------------------------
# Saying it in words
# ---------------------------------------------------------------------------

_NAMED_RATIOS = (
    ("9:16", 9 / 16),
    ("2:3", 2 / 3),
    ("3:4", 3 / 4),
    ("4:5", 4 / 5),
    ("1:1", 1.0),
    ("5:4", 5 / 4),
    ("4:3", 4 / 3),
    ("3:2", 3 / 2),
    ("16:9", 16 / 9),
    ("1.91:1", 1.91),
)


def format_ratio(ratio):
    """A ratio as a person would say it: "16:9", or "2.35:1" when it has no name."""
    for label, value in _NAMED_RATIOS:
        if abs(ratio - value) <= value * RATIO_TOLERANCE:
            return label
    if ratio >= 1:
        return f"{ratio:.2f}:1"
    return f"1:{1 / ratio:.2f}"


def format_duration(seconds):
    """Seconds as "12 s", "1 min 35 s" or "15 min"."""
    seconds = round(float(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return f"{minutes} min"
    return f"{minutes} min {rest} s"


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------


def check_media(width, height, duration, placement, kind=None):
    """Findings for one file at one placement, worst first.

    ``kind`` is "video" or "image" and is optional because the two callers know
    it for different reasons -- the composer reads it off the thumbnail, the
    server off the asset. Leaving it out only loses the Story distinction; a
    Reel is always a video and a feed image is always an image, so those are
    judged the same either way.

    A zero, None or negative measurement is unknown and is skipped. See the
    module docstring: fresh uploads legitimately measure 0.
    """
    spec = _spec_for(placement, kind)
    if spec is None:
        return []
    return _ratio_findings(width, height, spec) + _duration_findings(duration, spec)


def _ratio_findings(width, height, spec):
    if not width or not height or width < 0 or height < 0:
        return []

    ratio = width / height
    label = spec["label"]
    said = format_ratio(ratio)

    required = spec["required_ratio"]
    if required is not None and not (required[0] <= ratio <= required[1]):
        # Refused outright, so there is nothing useful to add about cropping.
        return [
            Finding(
                BLOCK,
                f"{label} has to be between {format_ratio(required[0])} and {format_ratio(required[1])}. "
                f"This file is {said}, which Instagram will not accept.",
            )
        ]

    recommended = spec["recommended_ratio"]
    if isinstance(recommended, tuple):
        low, high = recommended
        if not (low <= ratio <= high):
            return [
                Finding(
                    WARN,
                    f"This file is {said}. {label} is cropped to fit between "
                    f"{format_ratio(low)} and {format_ratio(high)}.",
                )
            ]
        return []

    if abs(ratio - recommended) > recommended * RATIO_TOLERANCE:
        return [
            Finding(
                WARN,
                f"This file is {said}. {label} is {format_ratio(recommended)}, so Instagram crops the "
                f"middle {format_ratio(recommended)} out of it.",
            )
        ]
    return []


def _duration_findings(duration, spec):
    limits = spec["duration"]
    if limits is None or not duration or duration < 0:
        return []

    shortest, longest = limits
    label = spec["label"]
    if duration < shortest:
        return [
            Finding(
                BLOCK,
                f"{label} is {format_duration(shortest)} at the very least. This one is {format_duration(duration)}.",
            )
        ]
    if duration > longest:
        return [
            Finding(
                BLOCK,
                f"{label} is {format_duration(longest)} at most. This one is {format_duration(duration)}. "
                f"Trim it in the media library before publishing.",
            )
        ]
    return []


def blocking_message(findings):
    """The first blocking finding's message, or None."""
    for finding in findings:
        if finding.blocks:
            return finding.message
    return None
