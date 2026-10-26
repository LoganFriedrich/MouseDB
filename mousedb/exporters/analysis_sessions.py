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
from . import trays
from ..stats import PELLETS_PER_TRAY

# The session IS the unit, so these must not vary within one. Taken as the first
# value in the group, which is the same as any other when the data is sound; a
# column that does vary is reported rather than silently reduced.
PER_SESSION: List[str] = [
    "subject_id", "test_date", "session_id", "tray_id", "tray_type",
    "test_type", "test_type_grouped", "experiment_stage", "days_post_injury",
    "injury_type", "injury_level", "study_group", "sex", "strain",
    "survived_all_surgeries", "excluded_in_origin_study", "video_name",
    "total_swipes_ai", "attention_ai",
    "pellets_in_video", "video_displaced", "video_retrieved", "video_contacted",
    "pellets_hand_scored", "manual_displaced", "manual_retrieved", "manual_contacted",
]

# What one session is identified by. The video is the session's recording, so it
# identifies it exactly; subject is carried too so a group is readable on its own.
KEYS = ["subject_id", "video_name"]
# The per-session counts of pellets. NA on an easy or flat tray, which has no
# pellets to count (trays.py says why).
PELLET_COUNTS = ("pellets_reached_for", "reaches_that_decided_a_pellet",
                 "reaches_after_the_pellet_was_gone")
_EASY_FLAT = (" NA on an easy or flat training tray: its pellets are not tracked and "
              "the video is not cut into one stretch per pellet, so there is nothing "
              "to count.")

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


def _value(group: pd.DataFrame, column: str):
    """The session's count in ``column`` as a number, or None when absent."""
    if column not in group.columns:
        return None
    v = pd.to_numeric(pd.Series([group[column].iloc[0]]).replace(ar.MISSING, None),
                      errors="coerce").iloc[0]
    return None if pd.isna(v) else float(v)


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
        # Two different numbers, both kept, each under a name that says what it
        # is. Until 2026-10-16 the second was published as "pellets_presented"
        # and used as the denominator of every percentage, so a session in which
        # the animal ignored some pellets showed an inflated success rate (up to
        # 100% where the truth was 5%). A session is one video of one tray.
        row["pellets_presented"] = PELLETS_PER_TRAY
        row["pellets_reached_for"] = (int(group["pellet_number"].nunique(dropna=True))
                                      if "pellet_number" in group.columns else None)

        if "pellet_state_at_this_reach" in group.columns:
            decided = int((group["pellet_state_at_this_reach"]
                           == ar.STATE_DECIDED_BY_THIS).sum())
            row["reaches_that_decided_a_pellet"] = decided
            row["reaches_after_the_pellet_was_gone"] = int(
                (group["pellet_state_at_this_reach"] == ar.STATE_ALREADY_GONE).sum())
        # An easy or flat tray has no pellets to count (trays.py says why): these
        # are absent, not zero -- "0 pellets reached for" would be a claim.
        if trays.outcome_free(group).iloc[0]:
            for column in PELLET_COUNTS:
                row[column] = None

        # Every outcome as a percentage of the pellets presented, from each
        # scoring stream separately. They disagree sometimes; reporting one would
        # be choosing for the reader. Contacted = displaced + retrieved, and a
        # presented pellet that was not contacted was missed.
        for stream, prefix in (("video", "video"), ("hand_scored", "manual")):
            displaced = _value(group, "%s_displaced" % prefix)
            retrieved = _value(group, "%s_retrieved" % prefix)
            contacted = (None if displaced is None or retrieved is None
                         else displaced + retrieved)
            missed = None if contacted is None else PELLETS_PER_TRAY - contacted
            for outcome, count in (("retrieved", retrieved), ("displaced", displaced),
                                   ("contacted", contacted), ("missed", missed)):
                row["percent_%s_%s" % (outcome, stream)] = _rate(count, PELLETS_PER_TRAY)

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
        "How many pellets the tray held in this session: 20, the tray's capacity. "
        "Every percentage in this file is out of this number."),
    "pellets_reached_for": (
        "Pellets reached for", "integer",
        "How many of the tray's 20 pellets the animal reached for at least once in "
        "this session: the number of different pellet numbers among this session's "
        "reaches. A pellet never reached for is not counted, so this is at most "
        "pellets_presented. It is not a denominator for any percentage here."
        + _EASY_FLAT),
    "reaches_that_decided_a_pellet": (
        "Reaches that decided a pellet", "integer",
        "How many reaches in this session were the one credited with what happened "
        "to their pellet. At most one per pellet, so this cannot exceed "
        "pellets_reached_for." + _EASY_FLAT),
    "reaches_after_the_pellet_was_gone": (
        "Reaches made after the pellet was gone", "integer",
        "How many reaches were made at a pellet that had already been retrieved or "
        "knocked out of reach, so they could not have succeeded. Counting these as "
        "attempts is what makes a success rate wrong." + _EASY_FLAT),
}

# The eight percentages: four outcomes, each from both scoring streams. Contacted
# is displaced + retrieved; missed is every presented pellet not contacted. All
# are out of pellets_presented (20), never out of the pellets reached for.
_OUTCOME_WORDS = {
    "retrieved": "scored as retrieved",
    "displaced": "scored as displaced (moved but not eaten)",
    "contacted": "contacted (displaced plus retrieved)",
    "missed": "missed (presented but not contacted: 20 minus contacted)",
}
for _outcome, _words in _OUTCOME_WORDS.items():
    for _stream, _title, _who in (("video", "algorithm", "by the algorithm from the video"),
                                  ("hand_scored", "hand scored", "by hand from the tray")):
        _DERIVED["percent_%s_%s" % (_outcome, _stream)] = (
            "Percent %s (%s)" % (_outcome, _title), "number",
            "Pellets %s %s, as a percentage of the 20 pellets presented. NA when "
            "that stream has no score for this session. The two streams measure the "
            "same thing independently; where they differ, that difference is itself "
            "the finding." % (_words, _who))


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
