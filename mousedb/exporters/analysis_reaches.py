"""One row per reach, in a shape a statistician can load and use immediately.

WHY A THIRD DOCUMENT
--------------------
Two already exist. The COMPLETE RECORD carries everything known about a reach,
including how it was produced, and fills every blank with a word saying why it is
blank. The SHAREABLE TABLE drops the process columns and uses plain names. Both
are right for what they are, and neither can be handed to someone who wants to
run statistics, for one reason:

    a word in a numeric column makes the whole column text.

"Not recorded" in a speed column is an honest answer to "why is this empty". It
is also what stops ``mean()`` working, and a reader either knows to clean it or
finds out from their results. This document takes the other side of that trade.

THE QUALITIES THIS FORMAT HAS
-----------------------------
Copied from a dataset an analysis collaborator assembled and sent back, because
their analysis already expects this shape:

* One row per reach.
* The row is SELF-CONTAINED. The animal's details, the session's details and the
  session's totals are repeated onto every row, so no file ever has to be joined
  to another to answer a question.
* Missing is ``NA`` -- one spelling, everywhere, which every statistics package
  already reads as missing.
* Numeric columns hold numbers and nothing else. Categorical columns hold words.
* Column names are lowercase with underscores.
* The test phase comes with a coarser grouping beside it, not only the raw label.
* An inclusion flag says whether the animal was used.

WHAT IS DELIBERATELY DIFFERENT
------------------------------
* EVERY kinematic measurement, not the handful one analysis happened to use.
  Which measures matter is the reader's question to ask; one left out is one they
  cannot ask about without coming back to us.
* No principal-component columns and no agreement columns. Those are the OUTPUT
  of an analysis, not measurements, and this is a measurements file.
* The header lines up with the data. In the file this shape was taken from, the
  first eight column names sit three places away from the values beneath them,
  so anything read there by column name gets the wrong field -- including the
  subject id.

Every one of those qualities is asserted by tests. A format that is only written
down drifts the first time somebody adds a column.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

import pandas as pd

from . import data_dictionary as dd
from . import extended_features as extended
from . import reach_summary

MISSING = "NA"
ROW_NUMBER = "entry_index"
NAME_RULE = re.compile(r"^[a-z][a-z0-9_]*$")

# Identity and context, repeated onto every row so the file stands alone.
IDENTITY: List[Tuple[str, str]] = [
    ("SubjectID", "subject_id"),
    ("Test_Date", "test_date"),
    ("Session_ID", "session_id"),
    ("Tray_ID", "tray_id"),
    ("tray_type", "tray_type"),
    ("Test_Type", "test_type"),
    ("Test_Type_Grouped", "test_type_grouped"),
    ("Days_Post_Injury", "days_post_injury"),
    ("Injury_Type", "injury_type"),
    ("Injury_level", "injury_level"),
    ("InjGroupAssignTyp", "study_group"),
    ("SexTyp", "sex"),
    ("SpeciesStrainTyp", "strain"),
    ("Exclusion_in_origin_study", "animal_inclusion"),
    ("video_name", "video_name"),
    ("segment_num", "pellet_number"),
    ("reach_num", "swipe_id"),
]

# Session totals, repeated on every row of that session. Both scoring streams are
# carried: the algorithm's and the hand scores'. They disagree sometimes, and
# which is right is the reader's question, not ours to settle by dropping one.
SESSION_TOTALS: List[Tuple[str, str]] = [
    ("Total_Swipes_AI", "total_swipes_ai"),
    ("Attention_AI", "attention_ai"),
    ("Video_Displaced", "video_displaced"),
    ("Video_Retrieved", "video_retrieved"),
    ("Video_Contacted", "video_contacted"),
    ("Manual_Displaced", "manual_displaced"),
    ("Manual_Retrieved", "manual_retrieved"),
    ("Manual_Contacted", "manual_contacted"),
]

# The measures that analysis used, under its names, so a script written against
# that file keeps working. Everything else follows under our own names.
NAMED_KINEMATICS: List[Tuple[str, str]] = [
    ("ext_righthand_extension_past_nose_mm", "swipe_length"),
    ("ext_righthand_lateral_deviation_mm", "swipe_breadth"),
    ("ext_righthand_swept_area_mm2", "swipe_area"),
    ("ext_righthand_peak_speed_mm_per_frame", "swipe_speed"),
    ("ext_righthand_total_path_mm", "path_length"),
    ("duration_frames", "swipe_duration_frames"),
]

# Columns whose value is a word. Everything else must load as a number.
TEXT_COLUMNS = {
    "subject_id", "test_date", "session_id", "tray_id", "tray_type", "test_type",
    "test_type_grouped", "injury_type", "injury_level", "study_group", "sex",
    "strain", "animal_inclusion", "video_name", "reach_outcome", "pellet_outcome",
    "pellet_state_at_this_reach", "experiment_stage",
}



# What the pellet's state was when this reach was made.
#
# WHY THIS IS NOT OPTIONAL
# A reach made after its pellet was already retrieved or knocked out of reach
# could not have succeeded, whatever the paw did. Counting it alongside reaches
# that could have succeeded does not fail -- it quietly makes every success rate,
# every per-reach average and every comparison between animals wrong, because the
# denominator contains attempts that were never attempts at anything. Which
# pellet a reach was aimed at is already carried for the same reason; this says
# whether that pellet was still there to be had.
STATE_AVAILABLE = "pellet still available"
STATE_DECIDED_BY_THIS = "this reach decided the pellet"
STATE_ALREADY_GONE = "pellet already retrieved or displaced"

PELLET_STATE_VALUES = "; ".join(
    (STATE_AVAILABLE, STATE_DECIDED_BY_THIS, STATE_ALREADY_GONE))
PELLET_STATE_DESCRIBED = (
    "pellet still available=the pellet had not yet been moved when this reach was "
    "made, so the reach could have succeeded; "
    "this reach decided the pellet=this is the one reach credited with what "
    "happened to the pellet, and there is at most one per pellet; "
    "pellet already retrieved or displaced=an earlier reach at the same pellet "
    "had already settled it, so this reach could not have succeeded")


def pellet_state(frame) -> "pd.Series":
    """One of three states per reach, from the deciding reach and reach order.

    A pellet is settled by at most one reach. Everything before that reach
    happened while the pellet was still there; everything after it at the same
    pellet happened when it was gone. A pellet nothing ever moved stayed
    available for every reach aimed at it.
    """
    causal = (frame["causal_reach"] if "causal_reach" in frame.columns
              else pd.Series(0, index=frame.index))
    is_causal = causal.map(
        lambda v: str(v).strip().lower() in ("1", "1.0", "true", "t", "yes"))

    order = pd.to_numeric(frame.get("reach_num"), errors="coerce")
    # Group by the pellet within its own video, never by pellet number alone:
    # every video has a pellet 1.
    keys = [frame[c] for c in ("video_name", "segment_num") if c in frame.columns]
    if not keys or order.isna().all():
        return pd.Series(
            [STATE_DECIDED_BY_THIS if c else STATE_AVAILABLE for c in is_causal],
            index=frame.index)

    group = pd.Series(list(zip(*[k.astype(str) for k in keys])), index=frame.index)
    deciding_order = (order.where(is_causal).groupby(group).transform("min"))

    out = []
    for causal_flag, own, decided in zip(is_causal, order, deciding_order):
        if causal_flag:
            out.append(STATE_DECIDED_BY_THIS)
        elif pd.isna(decided) or pd.isna(own) or own < decided:
            out.append(STATE_AVAILABLE)
        else:
            out.append(STATE_ALREADY_GONE)
    return pd.Series(out, index=frame.index)



# The session's phase at three coarsenesses, because a reader groups at whichever
# one their question needs: the raw label, the timepoint, and the stage of the
# experiment the animal was in. The dataset this format came from carried the same
# three, which is what they are for -- tags saying what was happening in that
# session and where the animal was in the experiment.
STAGE_BEFORE = "Before injury"
STAGE_AFTER = "After injury"
STAGE_REHAB = "Rehabilitation"
STAGE_AFTER_REHAB = "After rehabilitation"
STAGE_UNKNOWN = "Unscheduled or unknown"

EXPERIMENT_STAGE_VALUES = "; ".join(
    (STAGE_BEFORE, STAGE_AFTER, STAGE_REHAB, STAGE_AFTER_REHAB, STAGE_UNKNOWN))

# Matched on the phase label by substring, in this order, rather than against a
# list of one laboratory's phase names. A lab that names its phases differently
# gets the same answer as long as the words mean what they say, and a phase that
# matches nothing is reported as unknown instead of being forced into a stage.
_STAGE_RULES = (
    ("post_rehab", STAGE_AFTER_REHAB),
    ("post-rehab", STAGE_AFTER_REHAB),
    ("rehab", STAGE_REHAB),
    ("post_injury", STAGE_AFTER),
    ("post-injury", STAGE_AFTER),
    ("baseline", STAGE_BEFORE),
    ("train", STAGE_BEFORE),
    ("pre_injury", STAGE_BEFORE),
    ("pre-injury", STAGE_BEFORE),
)


def experiment_stage(labels) -> "pd.Series":
    """The coarse stage for each session label."""
    def one(value):
        text = str(value or "").strip().lower()
        for needle, stage in _STAGE_RULES:
            if needle in text:
                return stage
        return STAGE_UNKNOWN

    return pd.Series([one(v) for v in labels], index=getattr(labels, "index", None))


def _blank(value) -> bool:
    """Truly absent. NOT the other documents' no-value words.

    Those words need no special handling here: in a numeric column a word is not
    a number, so converting the column turns it into NA by itself. In a TEXT
    column the word must survive, because "Undetermined" is a real answer about a
    pellet -- it means nobody has decided yet -- and blanking it would turn a
    statement into a silence.
    """
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def other_kinematics(columns) -> List[Tuple[str, str]]:
    """Every extended measurement not already carried under a named column."""
    taken = {src for src, _ in NAMED_KINEMATICS}
    return [(c, c[len(extended.PREFIX):]) for c in sorted(columns)
            if c.startswith(extended.PREFIX) and c not in taken]


def build(full: pd.DataFrame) -> Tuple[pd.DataFrame, List[dict]]:
    """(frame, dictionary rows) from the complete per-reach frame."""
    extra = other_kinematics(full.columns)
    pairs = IDENTITY + NAMED_KINEMATICS + extra + SESSION_TOTALS
    present = [(src, name) for src, name in pairs if src in full.columns]

    data: Dict[str, pd.Series] = {name: full[src] for src, name in present}

    # The committed answer for this reach, in the same words the shareable table
    # uses, so two documents can never disagree about an outcome.
    if "segment_outcome" in full.columns:
        pellet = reach_summary._pellet_result(full["segment_outcome"])
        causal = (full["causal_reach"] if "causal_reach" in full.columns
                  else pd.Series(0, index=full.index))
        data["reach_outcome"] = reach_summary._reach_result(causal, pellet)
        data["pellet_outcome"] = pellet
    data["pellet_state_at_this_reach"] = pellet_state(full)
    for source in ("Test_Type_Grouped", "Test_Type"):
        if source in full.columns:
            data["experiment_stage"] = experiment_stage(full[source])
            break

    # A plain counter, first column, so the file can be sorted in a spreadsheet
    # and put back exactly as it was. Nothing is derived from it and nothing
    # should be: it means "this was row N as written", not anything about the
    # reach. It exists because sorting a sheet is a one-way door otherwise --
    # the order here is chronological within each animal and session, which no
    # combination of the other columns reproduces once it has been lost.
    data[ROW_NUMBER] = range(1, len(full) + 1)

    order = ([ROW_NUMBER]
             + [n for _s, n in IDENTITY if n in data]
             + [n for n in ("experiment_stage", "reach_outcome", "pellet_outcome",
                            "pellet_state_at_this_reach") if n in data]
             + [n for _s, n in NAMED_KINEMATICS + extra + SESSION_TOTALS if n in data])
    out = pd.DataFrame({k: data[k] for k in order}, columns=order)

    for name in out.columns:
        if name in TEXT_COLUMNS:
            out[name] = out[name].map(
                lambda v: MISSING if _blank(v) else str(v).strip())
        else:
            numbers = pd.to_numeric(
                out[name].map(lambda v: None if _blank(v) else v), errors="coerce")
            out[name] = numbers.map(lambda v: MISSING if pd.isna(v) else v)

    return out, _dictionary_rows(present, order)


def _dictionary_rows(present: List[Tuple[str, str]], order: List[str]) -> List[dict]:
    extended_keys = [s[len(extended.PREFIX):] for s, _ in present
                     if s.startswith(extended.PREFIX)]
    source_rows = {r["VariableName"]: r for r in
                   (dd.REACH_DATA + dd.ODC_REACH_ANIMAL + dd.ODC_REACH_SESSION
                    + dd.ODC_REACH_TOTALS + extended.dictionary_rows(extended_keys))}
    by_name = {name: src for src, name in present}

    answers = {r["VariableName"]: r for r in reach_summary._dictionary_rows([], {})}
    rows = []
    for name in order:
        if name == ROW_NUMBER:
            base = dd._row(
                name, "Entry index",
                "A plain counter over the rows of this file, starting at 1. It "
                "carries no meaning about the reach: it is here so the file can "
                "be sorted in a spreadsheet and then put back in the order it "
                "was written, which is chronological within each animal and "
                "session and is not reproducible from the other columns once "
                "lost.",
                "", "integer", "", "", "1")
        elif name == "experiment_stage":
            base = dd._row(
                name, "Stage of the experiment",
                "Where the animal was in the experiment when this session was run, "
                "as the coarsest of the three phase columns. test_type is the "
                "session's own label and test_type_grouped is its timepoint; this "
                "is the stage, for grouping that does not care which week it was. "
                "Derived from the phase label by matching the words in it, so a "
                "label that names no stage is reported as unknown rather than "
                "being forced into one.",
                "", "text", EXPERIMENT_STAGE_VALUES, "")
        elif name == "pellet_state_at_this_reach":
            base = dd._row(
                name, "Pellet state when this reach was made",
                "Whether the pellet this reach was aimed at was still there to be "
                "had. A reach made after the pellet was already retrieved or "
                "knocked out of reach could not have succeeded whatever the paw "
                "did, so counting it with reaches that could have succeeded makes "
                "every rate and average wrong. Derived from which reach is "
                "credited with the pellet and the order of reaches at that pellet.",
                "", "text", PELLET_STATE_VALUES, PELLET_STATE_DESCRIBED)
        elif name in ("reach_outcome", "pellet_outcome"):
            base = dict(answers.get(
                "reach_result" if name == "reach_outcome" else "pellet_result", {}))
        else:
            base = dict(source_rows.get(by_name.get(name, name)) or dd._row(
                name, name.replace("_", " ").capitalize(), "Per-reach measurement."))
        base["VariableName"] = name
        note = ("Absent values are written as NA, so the column loads as numbers."
                if name not in TEXT_COLUMNS
                else "Absent values are written as NA.")
        base["Comments"] = (str(base.get("Comments", "")) + " " + note).strip()
        rows.append(base)
    return rows
