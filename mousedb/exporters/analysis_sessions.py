"""One row per animal per session, in the same analysis-ready format as the reaches.

WHY BOTH SHAPES EXIST
---------------------
Some questions are about reaches -- how fast, how far, how straight -- and some
are about sessions: how many pellets did this animal get today, did it improve
after rehabilitation. The reach file can answer the second kind, but only by
grouping it first, and everyone who does that makes their own decisions about
what counts. Those decisions then differ between people, and the numbers stop
being comparable.

So the grouping is done once, here, and written down. The dataset this format
came from carried both shapes for the same reason.

BUILT FROM THE SAME ROWS AS THE REACH FILE
------------------------------------------
Not from a separate query. Anything summarised here is summarised from exactly
the rows that file contains, so the two can never disagree about a session --
which they would, eventually, if each went back to the database on its own.

THE FORMAT IS THE REACH FILE'S
------------------------------
``NA`` for absent, lowercase names, numeric columns holding only numbers, an
``entry_index`` first so a sort in a spreadsheet can be undone, every column
carrying a dictionary row, and nothing requiring a join.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from . import analysis_reaches as ar
from . import data_dictionary as dd

# The session IS the unit, so these must not vary within one. Taken as the first
# value in the group, which is the same as any other when the data is sound; a
# column that does vary is reported rather than silently reduced.
PER_SESSION: List[str] = [
    "subject_id", "test_date", "session_id", "tray_id", "tray_type",
    "test_type", "test_type_grouped", "experiment_stage", "days_post_injury",
    "injury_type", "injury_level", "study_group", "sex", "strain",
    "survived_all_surgeries", "excluded_in_origin_study", "video_name",
    "total_swipes_ai", "attention_ai",
    "video_displaced", "video_retrieved", "video_contacted",
    "manual_displaced", "manual_retrieved", "manual_contacted",
]

# What one session is identified by. The video is the session's recording, so it
# identifies it exactly; subject is carried too so a group is readable on its own.
KEYS = ["subject_id", "video_name"]

TEXT_COLUMNS = ar.TEXT_COLUMNS


def _rate(part, whole):
    """Percent, or None when either side is missing.

    The numerator can be absent as well as the denominator: a session nobody
    hand-scored has no retrieved count, and asking for a percentage of it is a
    question with no answer rather than a zero.
    """
    if whole in (None, 0) or pd.isna(whole) or part is None or pd.isna(part):
        return None
    return round(100.0 * float(part) / float(whole), 4)


def build(reaches: pd.DataFrame,
          reach_dictionary: List[dict] = None) -> Tuple[pd.DataFrame, List[dict]]:
    """(frame, dictionary rows) from the analysis-ready per-reach frame.

    ``reach_dictionary`` is that file's own rows. Passed in rather than
    rebuilt, so a column carried through from the reaches is described
    identically in both files and cannot drift between them.
    """
    if reaches.empty or not all(k in reaches.columns for k in KEYS):
        return pd.DataFrame(), []

    work = reaches.copy()
    for column in ("pellet_number", "video_retrieved", "manual_retrieved"):
        if column in work.columns:
            work[column] = pd.to_numeric(
                work[column].replace(ar.MISSING, None), errors="coerce")

    rows: List[Dict] = []
    for _keys, group in work.groupby(KEYS, dropna=False, sort=True):
        row: Dict = {}
        for column in PER_SESSION:
            if column in group.columns:
                row[column] = group[column].iloc[0]

        row["reaches_in_session"] = int(len(group))
        pellets = (int(group["pellet_number"].nunique(dropna=True))
                   if "pellet_number" in group.columns else None)
        row["pellets_presented"] = pellets

        if "pellet_state_at_this_reach" in group.columns:
            decided = int((group["pellet_state_at_this_reach"]
                           == ar.STATE_DECIDED_BY_THIS).sum())
            row["reaches_that_decided_a_pellet"] = decided
            row["reaches_after_the_pellet_was_gone"] = int(
                (group["pellet_state_at_this_reach"] == ar.STATE_ALREADY_GONE).sum())

        # The headline measure, from each scoring stream separately. They
        # disagree sometimes; reporting one would be choosing for the reader.
        if pellets:
            if "video_retrieved" in group.columns:
                row["percent_retrieved_video"] = _rate(
                    group["video_retrieved"].iloc[0], pellets)
            if "manual_retrieved" in group.columns:
                row["percent_retrieved_hand_scored"] = _rate(
                    group["manual_retrieved"].iloc[0], pellets)

        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out, []

    out.insert(0, ar.ROW_NUMBER, range(1, len(out) + 1))

    for name in out.columns:
        if name in TEXT_COLUMNS:
            out[name] = out[name].map(
                lambda v: ar.MISSING if ar._blank(v) else str(v).strip())
        else:
            numbers = pd.to_numeric(
                out[name].map(lambda v: None if ar._blank(v) else v), errors="coerce")
            # Same number formatting as the reach file, from the same function:
            # a count of 13 must not read "13" there and "13.0" here.
            out[name] = numbers.map(ar.write_number)

    return out, _dictionary_rows(list(out.columns), reach_dictionary or [])


_DERIVED = {
    "reaches_in_session": (
        "Reaches in this session", "integer",
        "How many reaches the session contains -- the number of rows this session "
        "has in the per-reach file."),
    "pellets_presented": (
        "Pellets presented", "integer",
        "How many distinct pellets the animal was offered in this session, counted "
        "as the number of different pellet numbers appearing in it."),
    "reaches_that_decided_a_pellet": (
        "Reaches that decided a pellet", "integer",
        "How many reaches in this session were the one credited with what happened "
        "to their pellet. At most one per pellet, so this cannot exceed "
        "pellets_presented."),
    "reaches_after_the_pellet_was_gone": (
        "Reaches made after the pellet was gone", "integer",
        "How many reaches were made at a pellet that had already been retrieved or "
        "knocked out of reach, so they could not have succeeded. Counting these as "
        "attempts is what makes a success rate wrong."),
    "percent_retrieved_video": (
        "Percent retrieved (algorithm)", "number",
        "Pellets the algorithm scored as retrieved, as a percentage of the pellets "
        "presented in this session."),
    "percent_retrieved_hand_scored": (
        "Percent retrieved (hand scored)", "number",
        "Pellets scored as retrieved by hand from the tray, as a percentage of the "
        "pellets presented in this session. The same quantity as "
        "percent_retrieved_video measured independently; where the two differ, that "
        "difference is itself the finding."),
}


def _dictionary_rows(columns: List[str], reach_dictionary: List[dict]) -> List[dict]:
    """Reuse the reach file's entry for any column that came from it."""
    inherited = {r["VariableName"]: r for r in (reach_dictionary or [])}

    rows = []
    for name in columns:
        if name in _DERIVED:
            title, dtype, description = _DERIVED[name]
            row = dd._row(name, title, description, "", dtype)
        elif name in inherited:
            row = dict(inherited[name])
        else:
            row = dd._row(name, name.replace("_", " ").capitalize(),
                          "Carried from the per-reach file.")
        row["VariableName"] = name
        note = ("Absent values are written as NA, so the column loads as numbers."
                if name not in TEXT_COLUMNS else "Absent values are written as NA.")
        row["Comments"] = (str(row.get("Comments", "")) + " " + note).strip()
        rows.append(row)
    return rows
