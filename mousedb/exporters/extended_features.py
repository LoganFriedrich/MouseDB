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


def _describe(key: str) -> str:
    """A sentence for a key whose name is its own description.

    These are machine-generated on purpose. Writing 161 descriptions by hand would
    produce 161 chances to describe a measurement wrongly; naming the parts the
    pipeline itself used is accurate by construction, and anything needing real prose
    gets an entry in ``_NOTES`` below.
    """
    body = key
    landmark = ""
    for name in ("righthand", "rhleft", "rhright", "paw", "tray", "frames"):
        if body.startswith(name + "_"):
            landmark = name
            body = body[len(name) + 1:]
            break
    words = body.replace("_", " ")
    where = ""
    for marker, phrase in (("at apex", "at the apex of the reach"),
                           ("at contact", "at the frame the paw met the pellet"),
                           ("at start", "at the first frame of the reach"),
                           ("at end", "at the last frame of the reach")):
        if words.endswith(marker):
            words, where = words[: -len(marker)].strip(), phrase
            break
    text = "%s%s" % (words[:1].upper() + words[1:], (", %s" % where) if where else "")
    if landmark:
        text += " (%s)" % {"righthand": "right hand landmark",
                           "rhleft": "left edge of the right hand",
                           "rhright": "right edge of the right hand",
                           "paw": "paw outline",
                           "tray": "tray",
                           "frames": "frame count"}.get(landmark, landmark)
    return text + "."


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
