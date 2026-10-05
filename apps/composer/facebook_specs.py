"""What Facebook does to a file as a Reel or a Story.

New file (Blue Monkey Media fork), the Facebook counterpart of
``instagram_specs.py``; it borrows that module's ``Finding`` and its way of
saying ratios and durations. Mirrored in ``_facebook_settings.html``. Zero or
missing measurements are unknown and produce nothing, for the same reason as
there: a fresh upload has not been measured yet.

Numbers read on 2026-10-05:

- **Reel** (Reels Publishing API): 9:16, at least 540x960, 3 to 90 seconds,
  24-60 fps. Meta enforces the duration (error 1363128) and an aspect ratio
  between 16:9 and 9:16 (error 1363040); those block. 9:16 itself and the
  minimum resolution are given as specs without an error code, so they warn.
- **Story** (Page Stories API): 9:16, at least 540x960; a video 3 to 60
  seconds. The reference also says "3 to 90 seconds" in its table, but lists
  "a video story can not exceed 60 seconds" as a limitation, so 60 blocks
  (providers/facebook_stories.py refuses the same). Ratio and resolution warn.

Frame rate and file size are not measured by the media library, so they are
not checked here.
"""

from .instagram_specs import BLOCK, NINE_BY_SIXTEEN, RATIO_TOLERANCE, WARN, Finding, format_duration, format_ratio

SIXTEEN_BY_NINE = 16 / 9
MIN_SHORT_SIDE = 540
MIN_LONG_SIDE = 960

MEDIA_SPECS = {
    "reel": {"label": "A Facebook Reel", "required_ratio": (NINE_BY_SIXTEEN, SIXTEEN_BY_NINE), "duration": (3.0, 90.0)},
    "story_video": {"label": "A Facebook Story video", "required_ratio": None, "duration": (3.0, 60.0)},
    "story_image": {"label": "A Facebook Story image", "required_ratio": None, "duration": None},
}


def _spec_for(placement, kind):
    if placement == "reel":
        return MEDIA_SPECS["reel"]
    if placement == "story":
        return MEDIA_SPECS["story_image"] if kind == "image" else MEDIA_SPECS["story_video"]
    return None


def check_media(width, height, duration, placement, kind=None):
    """Findings for one file as a Facebook Reel or Story, blocking ones first."""
    spec = _spec_for(placement, kind)
    if spec is None:
        return []
    findings = _ratio_findings(width, height, spec) + _duration_findings(duration, spec)
    return sorted(findings, key=lambda finding: finding.level != BLOCK)


def _ratio_findings(width, height, spec):
    if not width or not height or width < 0 or height < 0:
        return []
    ratio = width / height
    said = format_ratio(ratio)
    label = spec["label"]

    required = spec["required_ratio"]
    # A hair of tolerance on the bounds: 1080x1920 is exactly 9:16, a scaled
    # export lands just outside it and Meta does not refuse that.
    if required is not None and not (
        required[0] * (1 - RATIO_TOLERANCE) <= ratio <= required[1] * (1 + RATIO_TOLERANCE)
    ):
        return [
            Finding(
                BLOCK,
                f"{label} has to be between {format_ratio(required[0])} and {format_ratio(required[1])}. "
                f"This file is {said}, which Facebook will not accept.",
            )
        ]

    findings = []
    if abs(ratio - NINE_BY_SIXTEEN) > NINE_BY_SIXTEEN * RATIO_TOLERANCE:
        findings.append(Finding(WARN, f"This file is {said}. {label} is shown full screen at 9:16."))
    if min(width, height) < MIN_SHORT_SIDE or max(width, height) < MIN_LONG_SIDE:
        findings.append(
            Finding(
                WARN,
                f"This file is {width}x{height}. Facebook asks for at least {MIN_SHORT_SIDE}x{MIN_LONG_SIDE} "
                f"and may refuse anything smaller.",
            )
        )
    return findings


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
