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
Not from a separate query. Anything summarized here is summarized from exactly
the rows that file contains, so the two can never disagree about a session --
which they would, eventually, if each went back to the database on its own.

EVERY FILED TRAY HAS A ROW -- ALSO ONE WITH NO REACH
----------------------------------------------------
No reaches means no REACH rows, but session rows still exist in the session
files. A tray at which the animal never reached is a result (after an
injury it is often THE result), so it gets its session row like any other tray:
0 reaches, and every session-level number that does not depend on reaches
filled exactly as for any other tray -- the hand scores, the pellets the
pipeline cut and their outcomes (all untouched, usually: 0 percent retrieved),
the attention score, the session flag, the day totals. The exporter gives such
a tray one session-only row (odc_reaches.session_only_rows) that never reaches
the per-reach file; ``session_only`` names those videos here.

Which columns are filled on such a row and which are NA: every column is
FILLED except those computed FROM reaches (session_metrics: the
latency to the first reach, the gap between reaches, first-attempt success and
every per-session kinematics summary), which are NA -- there is no reach to
measure. The counts of reaches are 0, not blank: "0 pellets reached for" is
the answer. As on any tray, an Easy or Flat tray's pellet and outcome counts
are NA (trays.py says why). Each column's dictionary row says what it holds on
a tray with no reach (NO_REACH_RULES, session_metrics.NO_REACH_RULES).

THE SESSION MEASUREMENTS (session_metrics)
-------------------------------------------
The sessions document written by the export (``metrics=True``) also carries
the day's body weight, the day's pillar reaches, the session's length and
timing, success given contact, first-attempt success, the median, mean,
standard error and n of every per-reach kinematic measure, the displaced
split, the pellets moved by something other than a reach, pellet placement and
the day's recording status. session_metrics says what each is and why. The
per-reach file's own call (analysis_reaches carries the session totals onto
every reach) leaves ``metrics`` off, so none of them reach the per-reach file.

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
    "session_flag", "session_flag_reason", "session_flag_by", "session_flag_at",
    "day_total_reaches", "day_total_reaches_complete", "attention_ai",
    "pellets_in_video", "video_displaced", "video_retrieved", "video_contacted",
    "pellets_hand_scored", "manual_displaced", "manual_retrieved", "manual_contacted",
    "session_untouched_ai", "session_untouched_hand",
] + [c.lower() for c in ar.DAY_OUTCOME_SOURCES] + [
    # The day's body weight: the same value every reach row of the
    # day carries, so a session-level analysis need not join the reach file for
    # the animal's food-restriction state on the day.
    "body_weight_g", "body_weight_percent_of_free_feeding"]

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
          reach_dictionary: List[dict] = None,
          session_only=(), metrics: bool = False,
          sources: dict = None) -> Tuple[pd.DataFrame, List[dict]]:
    """(frame, dictionary rows) from the analysis-ready per-reach frame.

    ``metrics`` adds the session measurements (session_metrics); ``sources`` is
    session_metrics.prepare()'s result for the cohort (video lengths, pellet
    timing and outcomes, recording status). Without ``sources`` the columns
    they feed are NA; the ones computed from the reach rows are still filled.

    ``reach_dictionary`` is that file's own rows. Passed in rather than
    rebuilt, so a column carried through from the reaches is described
    identically in both files and cannot drift between them.

    ``session_only`` names the videos whose one row is a session-only row: a
    filed tray with no reach (module docstring). Their reach counts are 0.
    """
    session_only = {str(v) for v in (session_only or ())}
    if reaches.empty or not all(k in reaches.columns for k in KEYS):
        return pd.DataFrame(), []

    work = reaches.copy()
    for column in ("pellet_number", "video_retrieved", "manual_retrieved"):
        if column in work.columns:
            work[column] = pd.to_numeric(
                work[column].replace(ar.MISSING, None), errors="coerce")

    if metrics:
        from . import session_metrics as sm
        sources = sources or {}
        day_pillar = sm.day_pillar_reach_counts(work, session_only)
        measures = sm.kinematic_measures(work.columns)

    rows: List[Dict] = []
    for _keys, group in work.groupby(KEYS, dropna=False, sort=True):
        row: Dict = {}
        for column in PER_SESSION:
            if column in group.columns:
                row[column] = group[column].iloc[0]

        no_reach = str(group["video_name"].iloc[0]) in session_only
        row["reaches_in_video"] = 0 if no_reach else int(len(group))
        # Two different numbers, both kept, each under a name that says what it
        # is. The second must never be the denominator of a percentage: a
        # session in which the animal ignored some pellets would show an
        # inflated success rate (up to 100% where the truth was 5%). A session
        # is one video of one tray.
        row["pellets_presented"] = PELLETS_PER_TRAY
        row["pellets_reached_for"] = (int(group["pellet_number"].nunique(dropna=True))
                                      if "pellet_number" in group.columns else None)

        if "pellet_state_at_this_reach" in group.columns:
            decided = int((group["pellet_state_at_this_reach"]
                           == ar.STATE_DECIDED_BY_THIS).sum())
            row["reaches_that_decided_a_pellet"] = decided
            row["reaches_after_the_pellet_was_gone"] = int(
                (group["pellet_state_at_this_reach"] == ar.STATE_ALREADY_GONE).sum())
        if no_reach:
            # A tray with no reach: nothing was reached for, nothing decided by
            # a reach, nothing reached for after it was gone. Real zeros.
            for column in PELLET_COUNTS:
                row[column] = 0
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

        if metrics:
            row.update(sm.for_session(group, row, no_reach,
                                      bool(trays.outcome_free(group).iloc[0]),
                                      sources, day_pillar))
        rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return out, []

    if metrics:
        # The kinematics summaries are computed for all sessions at once (there
        # are hundreds of measures) and joined by video; a tray with no reach
        # has no reach rows, so it gets no values: NA.
        kin = sm.kinematics(work, measures, session_only)
        names = [sm.kinematic_column(s, pub) for _m, pub in measures for s in sm.STATS]
        if len(kin):
            kin = kin.reindex(out["video_name"].astype(str))
            kin.index = out.index
            out = pd.concat([out, kin[names]], axis=1)
        else:
            out = pd.concat([out, pd.DataFrame(index=out.index, columns=names)], axis=1)

    out.insert(0, ar.ROW_NUMBER, range(1, len(out) + 1))

    text = TEXT_COLUMNS | (set(sm.TEXT) if metrics else set())
    for name in out.columns:
        if name in text:
            out[name] = out[name].map(
                lambda v: ar.MISSING if ar._blank(v) else str(v).strip())
        else:
            numbers = pd.to_numeric(
                out[name].map(lambda v: None if ar._blank(v) else v), errors="coerce")
            # Same number formatting as the reach file, from the same function:
            # a count of 13 must not read "13" there and "13.0" here.
            out[name] = numbers.map(ar.write_number)

    extra_rows, kin_columns = [], set()
    if metrics:
        by_name = {r.get("VariableName"): r for r in (reach_dictionary or [])}
        kin_rows = sm.kinematic_dictionary_rows(dd._row, measures, by_name)
        kin_columns = {r["VariableName"] for r in kin_rows}
        extra_rows = sm.dictionary_rows(dd._row) + kin_rows
    return out, _dictionary_rows(list(out.columns), reach_dictionary or [],
                                 extra_rows, kin_columns, text)


_DERIVED = {
    "reaches_in_video": (
        "Reaches in this video", "integer",
        "How many reaches this session's video (one tray) contains -- the number of "
        "rows it has in the per-reach file; 0 for a filed tray at which the animal "
        "never reached, which has a row here and none there. Not the animal's whole testing day: for "
        "that, day_total_reaches. Formerly named reaches_in_session (until "
        "2026-11-17; same values), and the same count was also published as "
        "total_swipes_ai, which is retired: an earlier reach program's Total Swipes "
        "counted the whole day, not one tray."),
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
# is displaced + retrieved; "missed" is every presented pellet not contacted --
# untouched, in the words for a pellet. The columns keep the name percent_missed_*
# (scripts read them by name); their dictionary text says untouched. All are out
# of pellets_presented (20), never out of the pellets reached for.
_OUTCOME_WORDS = {
    "retrieved": "scored as retrieved",
    "displaced": "scored as displaced (moved but not eaten)",
    "contacted": "contacted (displaced plus retrieved)",
    "missed": "untouched (presented but not contacted: 20 minus contacted; the column keeps the name missed)",
}
for _outcome, _words in _OUTCOME_WORDS.items():
    for _stream, _title, _who in (("video", "algorithm", "by the algorithm from the video"),
                                  ("hand_scored", "hand scored", "by hand from the tray")):
        _DERIVED["percent_%s_%s" % (_outcome, _stream)] = (
            "Percent %s (%s)" % ("untouched" if _outcome == "missed" else _outcome, _title), "number",
            "Pellets %s %s, as a percentage of the 20 pellets presented. NA when "
            "that stream has no score for this session. The two streams measure the "
            "same thing independently; where they differ, that difference is itself "
            "the finding." % (_words, _who))


# What each column holds on the row of a tray with NO reach (module docstring).
# Every other column: NO_REACH_DEFAULT.
NO_REACH_RULES = {
    "reaches_in_video": "0.",
    "pellets_reached_for": "0 on a pillar tray (NA on an easy or flat tray, as on any).",
    "reaches_that_decided_a_pellet": "0 on a pillar tray (NA on an easy or flat tray, as on any).",
    "reaches_after_the_pellet_was_gone": "0 on a pillar tray (NA on an easy or flat tray, as on any).",
    "attention_ai": ("the session's attention score, which is scored from the pose and "
                     "not from reaches: the mean over every segment of the video that has "
                     "a score (the whole session on an easy or flat tray), exactly as on "
                     "a tray with reaches."),
}
NO_REACH_DEFAULT = ("filled exactly as on any other tray: it does not depend on "
                    "reaches.")


def _dictionary_rows(columns: List[str], reach_dictionary: List[dict],
                     extra_rows: List[dict] = (), kin_columns=(),
                     text_columns=None) -> List[dict]:
    """Reuse the reach file's entry for any column that came from it.
    ``extra_rows``: session_metrics' rows (their own definitions)."""
    from . import session_metrics as sm
    inherited = {r["VariableName"]: r for r in (reach_dictionary or [])}
    own = {r["VariableName"]: r for r in (extra_rows or ())}
    text_columns = TEXT_COLUMNS if text_columns is None else text_columns
    no_reach_rules = dict(NO_REACH_RULES)
    no_reach_rules.update(sm.NO_REACH_RULES)
    no_reach_rules.update({c: sm.NO_REACH_KINEMATICS for c in kin_columns})

    rows = []
    for name in columns:
        if name in own:
            row = dict(own[name])
        elif name in _DERIVED:
            title, dtype, description = _DERIVED[name]
            row = dd._row(name, title, description, "", dtype)
        elif name in inherited:
            row = dict(inherited[name])
        else:
            row = dd._row(name, name.replace("_", " ").capitalize(),
                          "Carried from the per-reach file.")
        row["VariableName"] = name
        note = ("Absent values are written as NA, so the column loads as numbers."
                if name not in text_columns else "Absent values are written as NA.")
        if name != ar.ROW_NUMBER:
            note += (" On the row of a tray with no reach (every filed tray has a row): "
                     + no_reach_rules.get(name, NO_REACH_DEFAULT))
        row["Comments"] = (str(row.get("Comments", "")) + " " + note).strip()
        rows.append(row)
    return rows
