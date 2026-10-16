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
    # The FACT, not a verdict: whether the animal came through its surgeries.
    # Whether that means its reaches belong in an analysis is the reader's
    # decision, and it depends on the question -- an animal lost in its injury
    # surgery still has a full set of ordinary baseline reaches.
    ("Survived_All_Surgeries", "survived_all_surgeries"),
    ("Exclusion_in_origin_study", "excluded_in_origin_study"),
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
    ("Video_Pellets", "pellets_in_video"),
    ("Video_Displaced", "video_displaced"),
    ("Video_Retrieved", "video_retrieved"),
    ("Video_Contacted", "video_contacted"),
    ("Manual_Pellets", "pellets_hand_scored"),
    ("Manual_Displaced", "manual_displaced"),
    ("Manual_Retrieved", "manual_retrieved"),
    ("Manual_Contacted", "manual_contacted"),
]

# The measures that analysis used, under its names, so a script written against
# that file keeps working. Everything else follows under our own names.
# Measurements computed once per reach rather than once per landmark: the paw's
# rotation about the reach axis (its angle in the camera plane, from the left
# edge of the hand to the right edge, which is the pronation measure), how far
# that angle travelled over the reach, the head's angle and width, the nose's
# distance from the slit, the straightness and smoothness of the path, and the
# speed at apex in real time units rather than per frame.
#
# WHICH of the flat columns are real measurements was already settled for the
# shareable summary document, which excludes the ones that are declared but never
# computed. That list is reused here instead of a second one being written, so
# the two documents cannot drift apart about what is carried or what it is
# called. Only the per-reach measurements are taken: the frame numbers and
# durations this document already has under its own names are skipped.
FLAT_ALREADY_HERE = {"duration_frames"}


def flat_kinematics() -> List[Tuple[str, str]]:
    """(source column, name here) for the per-reach measurements, from the summary."""
    from . import reach_summary
    return [(src, name) for src, name in reach_summary.KINEMATICS
            if not src.startswith(extended.PREFIX) and src not in FLAT_ALREADY_HERE
            and not src.endswith("_frame")]


# Where the reach sits in the sequence at its pellet. Not kinematics, but a
# reader cannot ask "was this the first attempt" without them, and deriving them
# from the row order is exactly the kind of re-derivation this format exists to
# make unnecessary.
# The animal's body weight on the day of the session: in grams, and as a
# percentage of its free-feeding weight. The percentage is the food restriction
# state, and in a task where the animal works for food it belongs on every reach
# it made that day.
SESSION_WEIGHT: List[Tuple[str, str]] = [
    ("Session_Weight_g", "body_weight_g"),
    ("Session_Weight_Pct", "body_weight_percent_of_free_feeding"),
]

# Measurements the extractor computes on the hand point and on the whole reach,
# which are not in the per-landmark extended block and were not carried. Each is
# filled on essentially every reach (see the audit in the commit message).
WHOLE_REACH: List[Tuple[str, str]] = [
    ("apex_frame", "apex_frame"),
    ("velocity_at_apex_px_per_frame", "speed_at_apex_px_per_frame"),
    ("peak_velocity_px_per_frame", "peak_speed_px_per_frame"),
    ("mean_velocity_px_per_frame", "mean_speed_px_per_frame"),
    ("mean_likelihood", "tracking_confidence_mean"),
    ("frames_low_confidence", "frames_low_confidence"),
    ("attention_score", "attention_at_this_reach"),
    ("interaction_frame", "interaction_frame"),
]

_WHOLE_REACH_ROWS = {
    "apex_frame": (
        "Frame of the reach's apex", "integer", "",
        "The video frame at which the reach reached its furthest extension. The "
        "same quantity is given per tracked paw point as the four *_apex_frame "
        "columns; this is the reach's own, from the hand point."),
    "speed_at_apex_px_per_frame": (
        "Speed at the apex (pixels per frame)", "number", "pixels per frame",
        "How fast the hand was moving in the frame of furthest extension. The "
        "same measurement in real units is speed_at_apex_mm_per_sec."),
    "peak_speed_px_per_frame": (
        "Peak speed of the hand (pixels per frame)", "number", "pixels per frame",
        "The fastest the hand point moved between two frames of this reach. "
        "Computed on the hand point, which is why it differs from the four "
        "per-landmark peak speeds: those follow the edges of the paw."),
    "mean_speed_px_per_frame": (
        "Mean speed of the hand (pixels per frame)", "number", "pixels per frame",
        "Average frame-to-frame movement of the hand point over the reach. As "
        "with the peak, it is the hand point rather than the paw edges."),
    "tracking_confidence_mean": (
        "Mean tracking confidence for the hand", "number", "",
        "The tracker's reported likelihood for the hand point, averaged over the "
        "frames of the reach. A measure of the data, not of the animal: it says "
        "how sure DeepLabCut was about where it put the point."),
    "frames_low_confidence": (
        "Frames where the hand was tracked below 0.5", "integer", "frames",
        "How many frames of this reach have a hand-point likelihood below 0.5, "
        "which is the threshold this pipeline treats as unreliable everywhere "
        "else. A reach with several is one whose measurements are softer."),
    "attention_at_this_reach": (
        "Attention to the tray during this reach", "number", "percent",
        "The percentage of this reach's frames in which the animal was attending "
        "to the tray -- its nose confidently tracked and positioned at the tray "
        "rather than elsewhere. The file also carries attention_ai, which is the "
        "mean of this across the session; a reach made while the animal was not "
        "attending is a different event from one made while it was, and only "
        "this per-reach value can tell them apart."),
    "interaction_frame": (
        "Frame the paw met the pellet", "integer", "",
        "The video frame at which the paw was judged to have reached the pellet, "
        "where one was identified. Empty on most reaches, because the pipeline "
        "identifies it only for the reach credited with deciding a pellet -- so "
        "its absence means no interaction was identified, not that none "
        "happened."),
}

REACH_CONTEXT: List[Tuple[str, str]] = [
    ("n_reaches_in_segment", "reaches_at_this_pellet"),
    ("is_first_reach", "is_first_reach_at_pellet"),
    ("is_last_reach", "is_last_reach_at_pellet"),
    ("start_frame", "start_frame"),
    ("end_frame", "end_frame"),
    ("pellet_position_idealness", "pellet_position_idealness"),
]

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
    "strain", "survived_all_surgeries", "excluded_in_origin_study",
    "video_name", "reach_outcome", "pellet_outcome",
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

    # frame.get returns None for a missing column, and pd.to_numeric(None) is a
    # scalar, not a series -- so ask for the column explicitly and fall back to a
    # series of the right length when it is not there.
    order = (pd.to_numeric(frame["reach_num"], errors="coerce")
             if "reach_num" in frame.columns
             else pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64"))
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


# WHAT A COLUMN IS CALLED HERE WHEN THE EXTRACTOR'S NAME MISLEADS.
#
# "visibility" is the kinematics extractor's word for the tracker's own reported
# likelihood for a point -- how sure DeepLabCut is that it put that point in the
# right place in that frame. It is a quality measure for the row, not something
# the animal did, and the name says the opposite of what it means: it reads as
# "the paw was visible in the video", which is not it. The tracker can be
# confident about a point that is partly occluded and unsure about one in plain
# view, so a reader who takes the column for an occlusion measure is reading a
# different quantity than the one in the cell.
#
# This document is the one shaped for a reader, so it uses the honest word. The
# complete record keeps the extractor's name, as it does for every other column,
# and the dictionary entry here names the original so the two can be lined up.
RENAMED_WORDS = {"visibility": "confidence"}


def analysis_name(extended_key: str) -> str:
    """What a per-reach measurement is called in this document."""
    parts = extended_key.split("_")
    return "_".join(RENAMED_WORDS.get(part, part) for part in parts)


def other_kinematics(columns) -> List[Tuple[str, str]]:
    """Every extended measurement not already carried under a named column."""
    taken = {src for src, _ in NAMED_KINEMATICS}
    return [(c, analysis_name(c[len(extended.PREFIX):])) for c in sorted(columns)
            if c.startswith(extended.PREFIX) and c not in taken]


def build(full: pd.DataFrame) -> Tuple[pd.DataFrame, List[dict]]:
    """(frame, dictionary rows) from the complete per-reach frame."""
    extra = other_kinematics(full.columns)
    flat = flat_kinematics()
    pairs = (IDENTITY + SESSION_WEIGHT + REACH_CONTEXT + NAMED_KINEMATICS + flat
             + WHOLE_REACH + extra + SESSION_TOTALS)
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
             + [n for _s, n in SESSION_WEIGHT if n in data]
             + [n for _s, n in REACH_CONTEXT if n in data]
             + [n for _s, n in NAMED_KINEMATICS + flat + WHOLE_REACH + extra
                + SESSION_TOTALS if n in data])
    out = pd.DataFrame({k: data[k] for k in order}, columns=order)
    out = _carry_session_totals(out)

    for name in out.columns:
        if name in TEXT_COLUMNS:
            out[name] = out[name].map(
                lambda v: MISSING if _blank(v) else str(v).strip())
        else:
            numbers = pd.to_numeric(
                out[name].map(lambda v: None if _blank(v) else v), errors="coerce")
            out[name] = numbers.map(write_number)

    # The final column list, not `order`: the session numbers are added after
    # it is built, and a column without a dictionary row is an undocumented
    # column -- which the export refuses to call complete, correctly.
    return out, _dictionary_rows(present, list(out.columns))


# How many digits a number is written with.
#
# WHY NOT ALL OF THEM. A paw position comes from a video frame, and one pixel is
# about a quarter of a millimetre. Writing a 4 mm measurement as
# 3.638133375394476 claims to know it to a ten-thousandth of the width of an
# atom of resolution the camera does not have, and claiming precision that is not
# there is not a harmless habit: it makes two values look different when the
# difference is float arithmetic, and it makes a column unreadable at a glance.
#
# Six significant digits is far more than the measurement supports and still
# short enough to read. Whole numbers are written without a decimal point at all,
# which is what fixes a hand-scored count of 13 pellets printing as "13.0" beside
# the algorithm's "13" for the same session.
SIGNIFICANT_DIGITS = 6


def write_number(value):
    """One number as it should appear in the file: trimmed, and not falsely precise."""
    if value is None or pd.isna(value):
        return MISSING
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return int(number)
    rounded = float("%.*g" % (SIGNIFICANT_DIGITS, number))
    # A number small enough to round to zero at six digits is written as 0
    # rather than as an exponent, because in these columns -- distances in
    # millimetres, speeds per frame -- it IS zero to anything the camera could
    # see, and "4.7e-08" in a spreadsheet column of millimetres reads as an
    # error rather than as a very small number.
    # 1e-04 is exactly the point below which Python prints an exponent, so this
    # is the same rule as "never write an exponent in these columns".
    if rounded != 0 and abs(rounded) < 1e-04:
        return 0
    return int(rounded) if rounded == int(rounded) else rounded


def _summary_rows_by_name() -> Dict[str, dict]:
    """The summary document's dictionary entries, keyed by the name it gives them.

    The flat per-reach measurements are carried here under the summary's names
    (paw_angle_at_apex_deg and the rest), and their definitions are already
    written there. Looking them up by that name reuses the definition instead of
    writing a second one that can drift from it.
    """
    from . import reach_summary
    rows = {}
    for row in reach_summary._dictionary_rows(reach_summary.KINEMATICS, {}):
        rows[row["VariableName"]] = row
    return rows


def _carry_session_totals(out: pd.DataFrame) -> pd.DataFrame:
    """Put each session's summary numbers onto every reach row of that session.

    WHY ON EVERY ROW. A session-level value is as true of each reach in the
    session as it is of the session, and a per-reach analysis needs these as
    denominators and covariates: a success rate is wrong without knowing how
    many pellets were presented, and a reach made after the pellet was gone has
    to be countable. Repeating the value down the rows is the intended shape of
    this format -- it is what lets a question be answered without joining a
    second file, which is the whole reason there is one file per unit rather
    than five.

    Computed by the sessions exporter, from these very rows, so the number here
    and the number there cannot disagree.
    """
    from . import analysis_sessions
    if out.empty or not all(k in out.columns for k in analysis_sessions.KEYS):
        return out
    sessions, _rows = analysis_sessions.build(out, [])
    carried = [c for c in analysis_sessions._DERIVED if c in sessions.columns]
    if not carried:
        return out
    merged = out.merge(sessions[analysis_sessions.KEYS + carried],
                       on=analysis_sessions.KEYS, how="left")
    merged.index = out.index
    for column in carried:
        merged[column] = merged[column].map(
            lambda v: MISSING if _blank(v) else v)
    return merged


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
        elif name in _WHOLE_REACH_ROWS:
            title, dtype, unit, description = _WHOLE_REACH_ROWS[name]
            base = dd._row(name, title, description, unit, dtype)
        elif name in _WEIGHT_ROWS:
            title, description, unit = _WEIGHT_ROWS[name]
            base = dd._row(name, title, description, unit, "number")
            base["Comments"] = (
                "Recorded by hand on the day of the session, so it is the same "
                "for every reach that animal made that day.")
        elif name in _session_totals():
            # A number for the whole session, repeated on every reach row of it.
            # Defined in the sessions document and taken from there, so the same
            # column is described in the same words in both files.
            title, dtype, description = _session_totals()[name]
            base = dd._row(name, title, description, "", dtype)
            base["Comments"] = (
                "A value for the whole session, repeated on every reach row of "
                "that session so a per-reach question can be answered without "
                "joining the sessions file.")
        else:
            base = dict(source_rows.get(by_name.get(name, name))
                        or _summary_rows_by_name().get(name)
                        or dd._row(name, name.replace("_", " ").capitalize(),
                                   "Per-reach measurement."))
        base["VariableName"] = name
        note = ("Absent values are written as NA, so the column loads as numbers."
                if name not in TEXT_COLUMNS
                else "Absent values are written as NA.")
        pair = _unit_partner(name, order)
        # A reader comparing this file with another of ours needs to know the
        # numbers were rounded, and to what -- otherwise a difference in the
        # seventh digit looks like a difference in the data.
        digits = ("" if name in TEXT_COLUMNS else
                  "Numbers are written to %d significant digits, which is more "
                  "than the measurement supports: a paw position comes from a "
                  "video frame and one pixel is about a quarter of a millimetre. "
                  "A whole number is written without a decimal point."
                  % SIGNIFICANT_DIGITS)
        base["Comments"] = " ".join(
            x for x in (str(base.get("Comments", "")), pair, note, digits) if x).strip()
        rows.append(base)
    return rows


_WEIGHT_ROWS = {
    "body_weight_g": (
        "Body weight on the day",
        "What the animal weighed when it was weighed before this session, in "
        "grams. Recorded by hand in the cohort's tracking sheet.",
        "g"),
    "body_weight_percent_of_free_feeding": (
        "Body weight as a percentage of free-feeding weight",
        "The animal's weight on the day as a percentage of the weight it held "
        "when fed freely, which is how food restriction is managed and judged. "
        "A value near 100 means the animal was close to its unrestricted "
        "weight; the lower it is, the hungrier the animal was when it reached. "
        "Worth treating as a covariate rather than a housekeeping number: an "
        "animal works harder for a pellet when it is further below its "
        "free-feeding weight.",
        "percent"),
}


def _session_totals():
    """{column: (title, type, description)} for the sessions document's own numbers."""
    from . import analysis_sessions
    return analysis_sessions._DERIVED


# The five measurements this document renames to the names the analysis
# collaborator's own file used. A reader of the file sees, for instance,
# righthand_extension_past_nose_px with no millimetre partner beside it, and
# cannot tell that the millimetre version is the column called swipe_length --
# the two descriptions are identical and neither mentions the other. So each
# half is told about the other half, by name.
_RENAMED_FROM = {new_name: src[len(extended.PREFIX):]
                 for src, new_name in NAMED_KINEMATICS
                 if src.startswith(extended.PREFIX)}
_UNIT_SUFFIXES = [("_mm2", "_px2"), ("_mm_per_frame", "_px_per_frame"),
                  ("_mm", "_px")]


def _unit_partner(name: str, columns) -> str:
    """A sentence naming what this column is called elsewhere, when that differs.

    Two reasons a name differs, and a reader needs both: the five measurements
    renamed to the analysis collaborator's own words (swipe_length and the rest),
    and the word changed because the extractor's was misleading (visibility ->
    confidence).
    """
    present = set(columns)
    for ours, theirs in RENAMED_WORDS.items():
        if ("_%s_" % theirs) in name or name.endswith("_%s" % theirs):
            extractor = name.replace("_%s_" % theirs, "_%s_" % ours)
            if extractor.endswith("_%s" % theirs):
                extractor = extractor[: -len(theirs)] + ours
            return ("The kinematics extractor calls this %s. It is renamed here "
                    "because it is the tracker's own confidence in where it put "
                    "the point, which is a measure of the data rather than of "
                    "the animal, and 'visibility' reads as a claim about whether "
                    "the paw could be seen." % extractor)
    original = _RENAMED_FROM.get(name)
    if original:
        for mm, px in _UNIT_SUFFIXES:
            if original.endswith(mm):
                other = original[: -len(mm)] + px
                if other in present:
                    # The name carries its own unit (_mm / _mm2 /
                    # _mm_per_frame), so the sentence does not restate it and
                    # cannot restate it wrongly.
                    return ("This is the measurement the kinematics extractor "
                            "calls %s; the same measurement in pixels is the "
                            "column %s." % (original, other))
        return "Named %s by the kinematics extractor." % original
    for mm, px in _UNIT_SUFFIXES:
        if name.endswith(px):
            stem = name[: -len(px)] + mm
            for renamed, source in _RENAMED_FROM.items():
                if source == stem:
                    return ("The same measurement in millimetres is carried in "
                            "this file under the name %s, which is what the "
                            "analysis collaborator's own file called it."
                            % renamed)
    return ""
