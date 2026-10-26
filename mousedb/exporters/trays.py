"""Which trays have pellet outcomes -- one definition for every export.

WHY THIS EXISTS
---------------
On the easy and flat training trays the pellets are presented but not tracked, and
no pellet outcome is computed (lab decision, 2026-10-23). Such a video is not cut
into one segment per pellet either: all of its reaches sit in one stretch, the
whole video. So on every reach from one of those trays, every question about a
pellet -- what happened to it, what the reach did to it, which reach decided it,
how many reaches were aimed at it -- has no answer. The word for that is
"Not applicable" (or an empty cell, in a file whose convention for an absent
number is NA).

Before 2026-10-26 the exports left these rows to the general rules, which gave
four different wrong answers on the same rows: "Undetermined" (reads as a pellet
still waiting for a person to decide it), "pellet still available" (a claim about
a pellet nobody tracked), "missed" (a claim that every reach missed), and numbers
such as 337 reaches "at this pellet" (the whole video counted as one pellet). A
reader could not tell a training tray's non-answer from a pillar pellet's real one.

Each exporter names the columns that ask about a pellet and calls ``blank_where``
on them; this module only says which rows those are.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

# The tray letters with no pellet outcomes: E = easy, F = flat. P (pillar) is the
# scored task.
OUTCOME_FREE_TRAYS = ("E", "F")


def outcome_free(frame: pd.DataFrame, column: str = "tray_type") -> pd.Series:
    """True on each row that comes from an easy or flat tray.

    A frame without the tray column gives all False: the rule cannot be applied
    to rows whose tray is unknown, and guessing would hide a pillar pellet's
    answer.
    """
    if column not in frame.columns:
        return pd.Series(False, index=frame.index)
    return (frame[column].fillna("").astype(str).str.strip().str.upper()
            .isin(OUTCOME_FREE_TRAYS))


def blank_where(frame: pd.DataFrame, rows: pd.Series, columns: Iterable[str],
                value) -> pd.DataFrame:
    """``frame`` with ``value`` in each of ``columns`` on ``rows`` (columns the
    frame does not have are skipped). The frame is changed in place and returned."""
    if not rows.any():
        return frame
    for column in columns:
        if column in frame.columns:
            frame[column] = frame[column].astype("object")
            frame.loc[rows, column] = value
    return frame
