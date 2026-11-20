"""The four levels on every per-reach row, and the names that say which level a column is.

WHY THIS EXISTS
---------------
Every per-reach export row is ONE REACH, and it repeats the three levels above
it, so a row can be read without joining anything:

  reach     what THIS reach did (retrieved, displaced, missed)
  segment   one pellet: what happened to it, the same on every reach aimed at it
  session   one tray = one video: its totals (pellets, retrieved, displaced,
            untouched), from the algorithm and from the hand scores
  day       one animal on one date, every pillar tray of it: the same totals

Before 2026-11-19 the files mixed these up in three ways a reader could not see:

* The complete record's ``outcome`` held a value only on the reach credited
  with the pellet; every other reach read "Not recorded" -- an absence where
  the answer is known. When the reach that moved a pellet is known, every other
  reach at that pellet MISSED (a person naming the causal reach answers every
  other reach of the segment too), and every reach at an untouched pellet missed.
* ``contact_group`` meant "a contact frame was found", not what the reach did,
  so a causal reach a person picked (no contact frame is recorded for those, by
  design: the contact frame only serves the algorithm's own inference) read
  "missed" beside a pellet it retrieved.
* Names did not say their level: ``Video_Retrieved`` vs ``pellet_outcome`` vs
  ``day_total_reaches``; "session" meant a tray in one file and an animal-day in
  another.

THE NAMING RULE (published names)
---------------------------------
A column that belongs to a level above the reach starts with that level:
``reach_``, ``segment_``, ``session_`` (= tray = video) or ``day_``. Every
measurement of the reach itself (its frames, kinematics, tracking confidence,
the earlier program's swipe_* measures) starts with ``reach_`` too (owner's
decision, 2026-11-19: reach_swipe_length, reach_start_frame ...). A column
with no level word is an identity key (subject_id, video_name, test_date,
tray_type, session_id, tray_id -- kept as they were, because the subset
exports, the exclusion pass and readers' scripts find rows by them), an
animal-level column, or a provenance column of the complete record. The animal-level ODC common data elements
(SubjectID, SpeciesTyp, AgeVal, BodyWgtMeasrVal, SexTyp, InjGroupAssignTyp)
keep ODC's exact spelling: ODC requires its own variables to match it.

The builders keep their internal names; the published names are applied where
each file is written (``publish``), together with the dictionary rows, so a
column and its definition are always renamed in the same breath.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

MOVED_CODES = ("retrieved", "displaced_sa", "displaced_outside")
# Segment outcomes at which no reach moved the pellet: every reach there missed.
# 'abnormal'/'abnormal_exception' = a person saw something other than a reach
# move it, so no reach is credited and none of them moved it.
NOT_MOVED_BY_A_REACH = ("untouched", "missed", "abnormal", "abnormal_exception")
MISSED = "missed"
CONTACTED = "contacted"


def _truthy(series: pd.Series) -> pd.Series:
    return series.map(lambda v: str(v).strip().lower() in ("1", "1.0", "true", "t", "yes"))


def segment_keys(frame: pd.DataFrame) -> pd.Series:
    """One key per pellet: (video, segment). Never segment alone -- every video
    has a segment 1."""
    cols = [c for c in ("video_name", "segment_num") if c in frame.columns]
    if not cols:
        return pd.Series(range(len(frame)), index=frame.index)
    return pd.Series(list(zip(*[frame[c].astype(str) for c in cols])), index=frame.index)


def credited(frame: pd.DataFrame, causal_column: str = "causal_reach") -> pd.Series:
    """True on every reach whose segment has a reach credited with the pellet."""
    if causal_column not in frame.columns:
        return pd.Series(False, index=frame.index)
    causal = _truthy(frame[causal_column])
    return causal.groupby(segment_keys(frame)).transform("any").astype(bool)


def reach_outcome_codes(frame: pd.DataFrame) -> pd.Series:
    """What THIS reach did, in the pipeline's words, on every reach row.

      the reach credited with a moved pellet  -> the pellet's outcome
                                                 (retrieved / displaced_sa /
                                                 displaced_outside)
      any other reach at a pellet whose
      credited reach is known                 -> missed
      any reach at an untouched pellet, or at
      one a person saw moved by something
      other than a reach                      -> missed
      a moved pellet with NO credited reach   -> empty (undetermined: one of
                                                 these reaches moved it and
                                                 which one is not recorded;
                                                 calling them all missed
                                                 would be a false claim)
      a pellet nobody decided                 -> empty (undetermined)

    Easy and flat trays are left to the caller's tray rule (Not applicable).
    """
    out = pd.Series([None] * len(frame), index=frame.index, dtype="object")
    if "segment_outcome" not in frame.columns:
        return out
    seg = frame["segment_outcome"].astype("object").map(
        lambda v: str(v).strip().lower() if v is not None and not pd.isna(v) else "")
    causal = (_truthy(frame["causal_reach"]) if "causal_reach" in frame.columns
              else pd.Series(False, index=frame.index))
    known = credited(frame)
    moved = seg.isin(MOVED_CODES)
    out[seg.isin(NOT_MOVED_BY_A_REACH)] = MISSED
    out[moved & known & ~causal] = MISSED
    here = moved & causal
    out[here] = seg[here]
    return out


def reach_contact(codes: pd.Series) -> pd.Series:
    """contacted / missed from the reach outcome, so the two can never disagree:
    a reach that retrieved or displaced its pellet met it; a reach that missed
    did not move it. Empty where the reach outcome is undetermined."""
    out = pd.Series([None] * len(codes), index=codes.index, dtype="object")
    out[codes.isin(MOVED_CODES)] = CONTACTED
    out[codes == MISSED] = MISSED
    return out


# --- published names ----------------------------------------------------------
# {internal name: published name}, per document. A name not listed is published
# as it is. New columns added on 2026-11-19 are built under their published
# names and need no entry.

# The complete record (ODC_reaches_<cohort>.csv) and the all-cohort reach_data.csv.
COMPLETE: Dict[str, str] = {
    "outcome": "reach_outcome",
    "causal_reach": "reach_causal",
    "contact_group": "reach_contact_group",
    "Session_Weight_g": "Day_Weight_g",
    "Session_Weight_Pct": "Day_Weight_Pct",
    "Video_Reaches_AI": "Session_Reaches_AI",
    "Attention_AI": "Session_Attention_AI",
    "Video_Pellets": "Session_Pellets_AI",
    "Video_Displaced": "Session_Displaced_AI",
    "Video_Retrieved": "Session_Retrieved_AI",
    "Video_Contacted": "Session_Contacted_AI",
    "Manual_Pellets": "Session_Pellets_Hand",
    "Manual_Displaced": "Session_Displaced_Hand",
    "Manual_Retrieved": "Session_Retrieved_Hand",
    "Manual_Contacted": "Session_Contacted_Hand",
    "Contacted_Match": "Session_Contacted_Difference",
    "Day_Total_Reaches_AI": "Day_Reaches_AI",
    "Day_Total_Reaches_Complete": "Day_Complete",
    "n_reaches_in_segment": "segment_reaches",
    "is_first_reach": "reach_is_first_in_segment",
    "is_last_reach": "reach_is_last_in_segment",
    "flagged_for_review": "reach_flagged_for_review",
    "flag_reason": "reach_flag_reason",
}

LEVEL_PREFIXES = ("reach_", "segment_", "session_", "day_")
REACH = "reach_"

# The complete record's and reach_data.csv's per-reach measurements (the
# database's reach columns that measure the reach), published as reach_<name>.
# Every extended measurement (ext_*) is one too; see complete_names.
REACH_MEASUREMENTS = (
    "start_frame", "apex_frame", "end_frame", "duration_frames",
    "max_extent_pixels", "max_extent_ruler", "max_extent_mm",
    "velocity_at_apex_px_per_frame", "velocity_at_apex_mm_per_sec",
    "peak_velocity_px_per_frame", "mean_velocity_px_per_frame",
    "trajectory_straightness", "trajectory_smoothness",
    "hand_angle_at_apex_deg", "hand_rotation_total_deg",
    "grasp_aperture_max_mm", "grasp_aperture_at_contact_mm",
    "head_width_at_apex_mm", "nose_to_slit_at_apex_mm",
    "head_angle_at_apex_deg", "head_angle_change_deg",
    "apex_distance_to_pellet_mm", "lateral_deviation_mm",
    "mean_likelihood", "frames_low_confidence", "tracking_quality_score",
    "interaction_frame", "distance_to_interaction",
)
EXTENDED_PREFIX = "ext_"


def with_reach_prefix(names: Dict[str, str], measurements) -> Dict[str, str]:
    """``names`` plus reach_<name> for every measurement it does not already
    rename and that does not already start with a level word."""
    out = dict(names)
    for m in measurements:
        if m in out or str(m).lower().startswith(LEVEL_PREFIXES):
            continue
        out[m] = REACH + str(m)
    return out


def complete_names(columns) -> Dict[str, str]:
    """The complete record's (and reach_data.csv's) published names for these
    columns: COMPLETE, plus reach_ on every per-reach measurement."""
    return with_reach_prefix(COMPLETE, [c for c in columns if c in REACH_MEASUREMENTS
                                        or str(c).startswith(EXTENDED_PREFIX)])

# The shareable table (reaches_<cohort>_summary.csv).
SUMMARY: Dict[str, str] = {
    "pellet_number": "segment_number",
    "reach_number_in_pellet": "reach_number",
    "reaches_at_this_pellet": "segment_reaches",
    "is_first_reach_at_pellet": "reach_is_first_in_segment",
    "is_last_reach_at_pellet": "reach_is_last_in_segment",
    "reach_result": "reach_outcome",
    "pellet_result": "segment_outcome",
    "reach_decided_the_pellet": "reach_causal",
    "mean_attention_score_in_session": "session_attention_ai",
    "reaches_in_video": "session_reaches_ai",
    "day_total_reaches": "day_reaches_ai",
    "day_total_reaches_complete": "day_complete",
    "pellets_in_video": "session_pellets_ai",
    "pellets_retrieved_in_session": "session_retrieved_ai",
    "pellets_displaced_in_session": "session_displaced_ai",
    "pellets_moved_in_session": "session_contacted_ai",
    "pellets_hand_scored": "session_pellets_hand",
    "pellets_retrieved_hand_scored": "session_retrieved_hand",
    "pellets_displaced_hand_scored": "session_displaced_hand",
    "pellets_moved_hand_scored": "session_contacted_hand",
}

# The analysis documents (reaches_<cohort>_analysis.csv and sessions_<cohort>_analysis.csv).
ANALYSIS: Dict[str, str] = {
    "pellet_number": "segment_number",
    "swipe_id": "reach_number",
    "pellet_outcome": "segment_outcome",
    "pellet_state_at_this_reach": "reach_pellet_state",
    "body_weight_g": "day_body_weight_g",
    "body_weight_percent_of_free_feeding": "day_body_weight_percent_of_free_feeding",
    "reaches_at_this_pellet": "segment_reaches",
    "is_first_reach_at_pellet": "reach_is_first_in_segment",
    "is_last_reach_at_pellet": "reach_is_last_in_segment",
    "pellet_position_idealness": "segment_pellet_position_idealness",
    "attention_at_this_reach": "reach_attention",
    "day_total_reaches": "day_reaches_ai",
    "day_total_reaches_complete": "day_complete",
    "attention_ai": "session_attention_ai",
    "pellets_in_video": "session_pellets_ai",
    "video_displaced": "session_displaced_ai",
    "video_retrieved": "session_retrieved_ai",
    "video_contacted": "session_contacted_ai",
    "pellets_hand_scored": "session_pellets_hand",
    "manual_displaced": "session_displaced_hand",
    "manual_retrieved": "session_retrieved_hand",
    "manual_contacted": "session_contacted_hand",
    "reaches_in_video": "session_reaches_ai",
    "pellets_presented": "session_pellets_presented",
    "pellets_reached_for": "session_pellets_reached_for",
    "reaches_that_decided_a_pellet": "session_reaches_that_decided_a_pellet",
    "reaches_after_the_pellet_was_gone": "session_reaches_after_the_pellet_was_gone",
}
for _o, _p in (("retrieved", "retrieved"), ("displaced", "displaced"),
               ("contacted", "contacted"), ("missed", "untouched")):
    ANALYSIS["percent_%s_video" % _o] = "session_percent_%s_ai" % _p
    ANALYSIS["percent_%s_hand_scored" % _o] = "session_percent_%s_hand" % _p

RENAMED_ON = "2026-11-19"


def publish(frame: pd.DataFrame, rows: List[dict],
            names: Dict[str, str]) -> Tuple[pd.DataFrame, List[dict]]:
    """(frame, dictionary rows) under their published names.

    The column and its dictionary row are renamed together; each renamed row's
    Comments says what the column was called before, so a reader holding an
    older file can line the two up.
    """
    present = {k: v for k, v in names.items() if k in frame.columns and k != v}
    if present:
        clash = set(present.values()) & (set(frame.columns) - set(present))
        if clash:
            raise ValueError("published names already taken by other columns: %s"
                             % ", ".join(sorted(clash)))
        frame = frame.rename(columns=present)
    out = []
    for row in rows:
        name = str(row.get("VariableName", ""))
        if name in present:
            row = dict(row)
            row["VariableName"] = present[name]
            note = ("Named %s in files written before %s (same values unless this "
                    "entry says otherwise)." % (name, RENAMED_ON))
            row["Comments"] = (str(row.get("Comments", "")) + " " + note).strip()
        out.append(row)
    return frame, out


def published(name: str, names: Dict[str, str]) -> str:
    return names.get(name, name)
