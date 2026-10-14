"""The per-reach extended measurements, turned from one JSON column into real columns.

WHY THIS EXISTS
---------------
Every reach the pipeline produces carries an ``extended_features`` block: well over a
hundred measured values per reach (paw outline area, paw spread, per-landmark speed and
path shape, extension past the nose, visibility). They are populated, and none of them
reached any export, because the exporter dropped the column outright.

That mattered more than it looked. Several of the flat columns next to it are declared
but never computed, and their working replacements were sitting inside this block the
whole time -- ``max_extent_mm`` is empty while ``righthand_extension_past_nose_mm`` is
not; ``lateral_deviation_mm`` is empty while ``righthand_lateral_deviation_mm`` is not.
So the complete record was missing its best measurements AND showing blanks in their
place.

The key set is expected to be the same for every project -- verify that on your own
corpus before relying on it -- so the column set is stable and can be documented. The
columns are built from whatever keys are actually present, so a project that carries a
different set still exports correctly; it simply gets its own columns. A reach whose
block is missing a key gets the shared not-measured marker rather than a blank -- see
``mousedb.exporters.missing``.
"""
from __future__ import annotations

import json
from typing import Dict, Iterable, List, Optional

import pandas as pd

from . import data_dictionary as dd

SOURCE_COLUMN = "extended_features"
PREFIX = "ext_"

# Unit and meaning are carried in the key's own suffix, which is how the pipeline names
# them. Read longest-first so "_mm2" is not mistaken for "_mm".
_SUFFIX_UNITS = (
    ("_mm2", "mm^2"), ("_px2", "pixels^2"),
    ("_mm_per_frame", "mm per frame"), ("_px_per_frame", "pixels per frame"),
    ("_mm_per_sec", "mm per second"),
    ("_mm", "mm"), ("_px", "pixels"),
    ("_deg", "degrees"), ("_frames", "frames"), ("_sec", "seconds"),
)


def parse_block(value) -> Optional[dict]:
    """The block as a dict, or None when the row has none. Never raises: a row whose
    block cannot be read is treated as a row without one, because one unreadable
    reach must not stop a cohort's export."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def keys_in(series: Iterable) -> List[str]:
    """Every key present anywhere in these blocks, sorted.

    Sorted rather than first-seen order so the column order is the same on every run
    and in every cohort: a file whose columns move between exports cannot be diffed.
    """
    found = set()
    for value in series:
        block = parse_block(value)
        if block:
            found.update(block.keys())
    return sorted(found)


def expand(series: pd.Series, index=None, keys: Optional[List[str]] = None) -> pd.DataFrame:
    """One column per extended key, named ``ext_<key>``.

    The prefix is deliberate: several keys would otherwise collide with a flat column
    of the same meaning but different provenance, and a reader must be able to tell
    which one they are looking at.
    """
    keys = keys if keys is not None else keys_in(series)
    if not keys:
        return pd.DataFrame(index=index if index is not None else series.index)
    blocks = [parse_block(v) or {} for v in series]
    data = {PREFIX + k: [b.get(k) for b in blocks] for k in keys}
    return pd.DataFrame(data, index=index if index is not None else series.index)


def _unit_for(key: str) -> str:
    for suffix, unit in _SUFFIX_UNITS:
        if key.endswith(suffix):
            return unit
    return ""


# How each measurement is actually computed, taken from the kinematics extractor
# that produces it. A dictionary Description has to say what the value IS; for a
# calculated value that means saying how it was calculated, or a reader cannot
# tell what they are comparing.
#
# This replaces descriptions generated from the column NAME. Restating a name
# ("Paw outline area mean") defines nothing: it gives the reader the words they
# just read, and leaves them unable to tell whether an area is a convex hull, a
# bounding box or a polygon, or over what the mean was taken.
#
# Composed from three parts rather than written out 161 times -- the landmark,
# the measure, the variant. Each part is written once and is accurate for every
# column that uses it, which is both less text and fewer chances to be wrong.

_LANDMARKS = {
    "righthand": "the right hand",
    "lefthand": "the left hand",
    "rhleft": "the left edge of the right hand",
    "rhright": "the right edge of the right hand",
    "rhout": "the outer edge of the right hand",
    "nose": "the nose",
    "head": "the head",
    "tray": "the tray",
    "paw": "",      # inter-paw measures name their own subject
    "frames": "",
}

# Every trajectory measure is computed on the paw position with the NOSE
# SUBTRACTED, bracketed by two synthetic anchors: the midpoint of nose and left
# box edge at the first frame, and of nose and right box edge at the last. Said
# once here so each description below can be about its own formula.
_BASIS = ("Measured on the path of %s relative to the nose, bracketed by a "
          "synthetic start point at the midpoint of nose and left box edge and a "
          "synthetic end point at the midpoint of nose and right box edge, so "
          "reaches are comparable regardless of where the animal stood.")

_MEASURES = {
    "apex_frame":
        "The frame within the reach at which %s is furthest out through the slit "
        "(greatest distance past the nose).",
    "extension_past_nose":
        "How far %s got past the nose at the furthest point of the reach.",
    "total_path":
        "Total distance %s travelled, summed frame to frame along the whole path.",
    "lateral_spread":
        "The full side-to-side range of %s during the reach: its rightmost "
        "position minus its leftmost.",
    "swept_area":
        "The area enclosed by the path of %s, as the polygon through every "
        "tracked position in order (shoelace formula).",
    "path_directness":
        "How straight the reach was: straight-line distance from the start anchor "
        "to the end anchor divided by the total distance %s actually travelled. "
        "1 is a perfectly straight path; smaller values wander more.",
    "motion_smoothness":
        "How smooth the motion of %s was, as 1 / (1 + mean absolute jerk), where "
        "jerk is the third frame-to-frame difference of position. Approaches 1 "
        "for smooth motion and falls towards 0 as the path becomes jerky.",
    "lateral_deviation":
        "How far %s departed sideways from a straight reach: the greatest "
        "perpendicular distance from any tracked position to the straight line "
        "between the start and end anchors.",
    "mean_speed":
        "Average speed of %s: total distance travelled divided by the number of "
        "frame-to-frame steps.",
    "peak_speed":
        "Fastest single frame-to-frame step made by %s during the reach.",
    "apex_speed":
        "Speed of %s at the furthest point of the reach: the length of the "
        "frame-to-frame step straddling the apex frame.",
    "visibility":
        "How confidently the tracker placed %s -- its reported likelihood, where "
        "values below 0.5 are treated as unreliable everywhere else in this "
        "pipeline.",
    "apex_lead":
        "The number of frames between the earliest and the latest of the four paw "
        "points reaching their own furthest extension, as the difference between "
        "the largest and smallest of their four apex frames -- the paw's internal "
        "timing spread.",
    "any_paw_low_confidence":
        "Number of frames in the reach where at least one of the four tracked paw "
        "points fell below a likelihood of 0.5, so the paw was not fully tracked.",
    "contact_duration":
        "Number of frames in the reach showing pellet or tray movement while the "
        "wrist could not be confidently tracked: pellet x position varying by more "
        "than 2 px over a rolling window of 4 frames, with both wrist and pillar "
        "likelihood below 0.5. It is evidence the paw was at the tray while hidden "
        "from the camera, not a measured contact.",
    "width_proxy":
        "Distance between the left and right edges of the right hand -- the "
        "stand-in for how open the paw is, since grasp aperture itself is not "
        "measured.",
    "outline_area":
        "Area of the polygon through the four tracked paw points, taken in order "
        "of their angle around the centre of the four, which is the paw outline "
        "as tracked. Not a bounding box and not a convex hull.",
    "spread_max":
        "The largest distance between any two of the four tracked paw points -- "
        "how far apart the paw is at its widest, whichever pair that happens to "
        "be.",
    "velocity_correlation":
        "How alike the four tracked paw points move: the average Pearson "
        "correlation between their frame-to-frame speed profiles, over every "
        "pair. Near 1 means they speed up and slow down together.",
    "apex_lead_frames":
        "How many frames separate the earliest and the latest of the four paw "
        "points in reaching their own furthest extension -- the paw's internal "
        "timing spread.",
    "leading_point":
        "Which of the four tracked paw points led the reach, as the one whose "
        "own furthest extension falls at the earliest frame.",
}

_VARIANTS = {
    "at_apex": "Taken at the furthest point of the reach.",
    "at_contact": "Taken at the frame the paw met the pellet.",
    "at_start": "Taken at the first frame of the reach.",
    "at_end": "Taken at the last frame of the reach.",
    "max": "The largest value over the frames of the reach.",
    "min": "The smallest value over the frames of the reach.",
    "mean": "Averaged over the frames of the reach.",
    "range": "The largest value over the reach minus the smallest.",
}

_TRAJECTORY_MEASURES = {
    "apex_frame", "extension_past_nose", "total_path", "lateral_spread",
    "swept_area", "path_directness", "motion_smoothness", "lateral_deviation",
    "mean_speed", "peak_speed", "apex_speed",
}


def _split_key(key):
    """(landmark, measure, variant) for one extended-feature name."""
    landmark = ""
    body = key
    for name in sorted(_LANDMARKS, key=len, reverse=True):
        if body.startswith(name + "_"):
            landmark, body = name, body[len(name) + 1:]
            break

    for suffix, _unit in _SUFFIX_UNITS:          # drop the unit suffix
        if body.endswith(suffix):
            body = body[: -len(suffix)].rstrip("_")
            break

    variant = ""
    for name in sorted(_VARIANTS, key=len, reverse=True):
        if body.endswith("_" + name):
            variant, body = name, body[: -(len(name) + 1)]
            break

    return landmark, body, variant


def _describe(key: str) -> str:
    """What this measurement is and, being calculated, how it is calculated.

    Built from the landmark, the measure and the variant, so one accurate
    sentence per measure covers every column that uses it. A key this cannot take
    apart says it has no definition yet -- honest about saying little, which
    beats prose that sounds like a definition and is not.
    """
    landmark, measure, variant = _split_key(key)
    template = _MEASURES.get(measure)
    if not template:
        return ("%s. No written definition yet for this measurement; its name is "
                "all that is known here." % key.replace("_", " ").capitalize())

    subject = _LANDMARKS.get(landmark, "") or "the paw"
    text = template % subject if "%s" in template else template

    if variant and _VARIANTS.get(variant):
        text += " " + _VARIANTS[variant]
    if measure in _TRAJECTORY_MEASURES and landmark in (
            "righthand", "lefthand", "rhleft", "rhright", "rhout"):
        text += " " + (_BASIS % subject)
    return text


_NOTES = {
    "righthand_extension_past_nose_mm":
        "How far the paw reached past the nose. This is the working extent measurement: "
        "the flat max_extent_mm column is declared but never computed.",
    "righthand_lateral_deviation_mm":
        "Sideways departure of the paw from a straight reach. This is the working "
        "measurement: the flat lateral_deviation_mm column is never computed.",
    "paw_width_proxy_max_mm":
        "Widest paw spread during the reach; the working stand-in for grasp aperture, "
        "which is never computed.",
    "righthand_visibility_mean":
        "Mean tracking confidence for the paw across the reach; the working stand-in "
        "for tracking_quality_score, which is never computed.",
}


def dictionary_rows(keys: Iterable[str]) -> List[dict]:
    """A data dictionary row for every extended column (an upload fails without one)."""
    rows = []
    for key in keys:
        note = _NOTES.get(key, "")
        rows.append(dd._row(
            PREFIX + key,
            key.replace("_", " ")[:1].upper() + key.replace("_", " ")[1:],
            _describe(key),
            _unit_for(key),
            "number",
            comments=note or "Per-reach extended measurement produced by the kinematics extractor.",
        ))
    return rows
