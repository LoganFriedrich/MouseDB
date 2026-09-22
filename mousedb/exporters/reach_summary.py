"""The shareable per-reach table: what happened, without how we worked it out.

WHY THIS EXISTS
---------------
The per-reach export grew into one file doing two jobs. It is the complete internal
record (every measurement, every version, who reviewed what) AND the table handed to a
collaborator, and it is bad at the second job precisely because it is good at the first.
A reader outside the lab meets columns describing our pipeline's opinion of itself --
which algorithm version ran, what the algorithm said before a human corrected it,
whether the two disagreed -- and none of that is a fact about a mouse.

So there are two documents. The complete one keeps everything. This one answers the
question a collaborator actually has, for every reach, in words, with no empty cells:

  reach_result   what THIS reach did to the pellet, as a committed answer. Where that
                 answer came from -- algorithm, human review, exhaustive ground truth --
                 is deliberately absent. A verdict is a verdict; its provenance is our
                 internal business and lives in the complete record.

  pellet_result  what ultimately happened to the pellet in this reach's segment,
                 repeated on every reach of that segment. A reach that did not move the
                 pellet still belongs to a pellet that went somewhere, and carrying that
                 along is what lets a session be read without joining anything.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
Pipeline and model versions, source files, machine names, reviewer usernames, the
algorithm's superseded answer, algorithm-versus-human agreement, per-reach tracking
confidence, review flags, and the columns no code computes. Every one of them is in
the complete record.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from . import data_dictionary as dd
from . import missing

# --- the committed vocabulary -------------------------------------------------------
RETRIEVED = "Retrieved"
DISPLACED_IN = "Displaced into the scoring area"
DISPLACED_OUT = "Displaced out of reach"
NEVER_TOUCHED = "Never touched"
NO_EFFECT = "Did not move the pellet"

# The pipeline's internal words for a pellet's fate -> what they mean in English.
# 'triaged', 'uncertain' and 'abnormal_exception' are not things a mouse did: they are
# the pipeline saying it could not decide, which for a finished cohort means a human
# review that has not happened yet. They are never presented as an outcome.
PELLET_WORDS = {
    "retrieved": RETRIEVED,
    "displaced_sa": DISPLACED_IN,
    "displaced_outside": DISPLACED_OUT,
    "untouched": NEVER_TOUCHED,
    "missed": NEVER_TOUCHED,
    "triaged": missing.UNDETERMINED,
    "uncertain": missing.UNDETERMINED,
    "abnormal_exception": missing.UNDETERMINED,
}
MOVED = {RETRIEVED, DISPLACED_IN, DISPLACED_OUT}

REACH_RESULT_PV = "; ".join([RETRIEVED, DISPLACED_IN, DISPLACED_OUT, NO_EFFECT,
                             missing.UNDETERMINED])
PELLET_RESULT_PV = "; ".join([RETRIEVED, DISPLACED_IN, DISPLACED_OUT, NEVER_TOUCHED,
                              missing.UNDETERMINED])

# --- which columns travel, and under what name --------------------------------------
# (source column, name in this document). A source column that is absent is skipped,
# so a cohort missing a block still produces a file.
ANIMAL: List[Tuple[str, str]] = [
    ("SubjectID", "subject"),
    ("SpeciesTyp", "species"),
    ("SpeciesStrainTyp", "strain"),
    ("Animal_origin", "animal_source"),
    ("SexTyp", "sex"),
    ("BodyWgtMeasrVal", "body_weight_g"),
    ("InjGroupAssignTyp", "study_group"),
    ("Injury_Type", "injury_type"),
    ("Injury_level", "injury_level"),
    ("Injury_details", "injury_details"),
    ("Exclusion_in_origin_study", "excluded_from_study"),
    ("Exclusion_reason", "exclusion_reason"),
]
SESSION: List[Tuple[str, str]] = [
    ("Session_ID", "session"),
    ("Test_Date", "session_date"),
    ("Test_Type", "test_phase"),
    ("Test_Type_Grouped", "test_phase_group"),
    ("Days_Post_Injury", "days_post_injury"),
    ("Tray_ID", "tray"),
]
REACH_IDENTITY: List[Tuple[str, str]] = [
    ("video_name", "session_video"),
    ("segment_num", "pellet_number"),
    ("reach_num", "reach_number_in_pellet"),
    ("n_reaches_in_segment", "reaches_at_this_pellet"),
    ("is_first_reach", "is_first_reach_at_pellet"),
    ("is_last_reach", "is_last_reach_at_pellet"),
]
# Measured kinematics only. Every one of these is computed on real data; the flat
# columns that are declared but never computed are excluded by name, and their working
# replacements from the extended block are carried here instead.
KINEMATICS: List[Tuple[str, str]] = [
    ("start_frame", "start_frame"),
    ("end_frame", "end_frame"),
    ("duration_frames", "duration_frames"),
    ("ext_righthand_extension_past_nose_mm", "extension_past_nose_mm"),
    ("ext_righthand_lateral_deviation_mm", "lateral_deviation_mm"),
    ("ext_righthand_total_path_mm", "path_length_mm"),
    ("ext_righthand_peak_speed_mm_per_frame", "peak_speed_mm_per_frame"),
    ("ext_righthand_mean_speed_mm_per_frame", "mean_speed_mm_per_frame"),
    ("velocity_at_apex_mm_per_sec", "speed_at_apex_mm_per_sec"),
    ("trajectory_straightness", "trajectory_straightness"),
    ("ext_righthand_path_directness", "path_directness"),
    ("trajectory_smoothness", "trajectory_smoothness"),
    ("ext_paw_width_proxy_max_mm", "max_paw_spread_mm"),
    ("ext_paw_outline_area_max_mm2", "max_paw_outline_area_mm2"),
    ("hand_angle_at_apex_deg", "paw_angle_at_apex_deg"),
    ("hand_rotation_total_deg", "paw_rotation_total_deg"),
    ("head_width_at_apex_mm", "head_width_at_apex_mm"),
    ("nose_to_slit_at_apex_mm", "nose_to_slit_at_apex_mm"),
    ("head_angle_at_apex_deg", "head_angle_at_apex_deg"),
    ("head_angle_change_deg", "head_angle_change_deg"),
]
SESSION_TOTALS: List[Tuple[str, str]] = [
    ("Total_Swipes_AI", "reaches_in_session"),
    ("Video_Retrieved", "pellets_retrieved_in_session"),
    ("Video_Displaced", "pellets_displaced_in_session"),
    ("Video_Contacted", "pellets_moved_in_session"),
    ("Manual_Retrieved", "pellets_retrieved_hand_scored"),
    ("Manual_Displaced", "pellets_displaced_hand_scored"),
    ("Manual_Contacted", "pellets_moved_hand_scored"),
]

# Which word fills an empty cell, per column of THIS document.
REASONS: Dict[str, str] = {
    "days_post_injury": missing.NOT_APPLICABLE,      # sessions before the injury
    "exclusion_reason": missing.NOT_APPLICABLE,      # animals that were not excluded
    "pellets_retrieved_hand_scored": missing.NOT_RECORDED,
    "pellets_displaced_hand_scored": missing.NOT_RECORDED,
    "pellets_moved_hand_scored": missing.NOT_RECORDED,
}


def _pellet_result(segment_outcome: pd.Series) -> pd.Series:
    words = segment_outcome.astype("object").map(
        lambda v: PELLET_WORDS.get(str(v).strip().lower()) if not missing.is_blank(v) else None)
    return words.where(words.notna(), missing.UNDETERMINED)


def _reach_result(causal: pd.Series, pellet_result: pd.Series) -> pd.Series:
    """What THIS reach did to the pellet.

    Three cases, and the middle one is the whole point of the column:
      * the segment could not be decided        -> Undetermined
      * this reach decided a pellet that moved  -> what it did
      * anything else                           -> it did not move the pellet

    The last case is an answer, not an absence. It is written as 'did not move the
    pellet' and NOT as 'no contact', because the pipeline never measures contact for a
    non-causal reach -- it infers it from not being the causal one. Saying 'no contact'
    would assert something nobody looked at.
    """
    is_causal = causal.map(lambda v: str(v).strip().lower() in ("1", "true", "t", "yes"))
    out = pd.Series(NO_EFFECT, index=causal.index, dtype="object")
    out = out.where(pellet_result != missing.UNDETERMINED, missing.UNDETERMINED)
    moved_here = is_causal & pellet_result.isin(MOVED)
    out[moved_here] = pellet_result[moved_here]
    return out


def build(full: pd.DataFrame) -> Tuple[pd.DataFrame, List[dict]]:
    """(summary frame, dictionary rows) from the complete per-reach frame."""
    pairs = ANIMAL + SESSION + REACH_IDENTITY + KINEMATICS + SESSION_TOTALS
    present = [(src, name) for src, name in pairs if src in full.columns]

    data: Dict[str, pd.Series] = {}
    for src, name in ANIMAL + SESSION + REACH_IDENTITY:
        if src in full.columns:
            data[name] = full[src]

    pellet = _pellet_result(full["segment_outcome"]) if "segment_outcome" in full.columns \
        else pd.Series(missing.UNDETERMINED, index=full.index, dtype="object")
    causal = full["causal_reach"] if "causal_reach" in full.columns \
        else pd.Series(0, index=full.index)
    data["reach_result"] = _reach_result(causal, pellet)
    data["pellet_result"] = pellet
    data["reach_decided_the_pellet"] = causal.map(
        lambda v: "Yes" if str(v).strip().lower() in ("1", "true", "t", "yes") else "No")

    for src, name in KINEMATICS + SESSION_TOTALS:
        if src in full.columns:
            data[name] = full[src]

    order = ([n for s, n in ANIMAL + SESSION + REACH_IDENTITY if s in full.columns]
             + ["reach_result", "pellet_result", "reach_decided_the_pellet"]
             + [n for s, n in KINEMATICS + SESSION_TOTALS if s in full.columns])
    out = pd.DataFrame({k: data[k] for k in order}, columns=order)

    # No empty cells anywhere. Columns nobody classified are filled as not recorded,
    # except ones that are empty on EVERY row, which means no code computes them.
    reasons = dict(REASONS)
    for column in missing.all_blank_columns(out):
        reasons.setdefault(column, missing.NOT_MEASURED)
    out = missing.fill_frame(out, reasons)

    return out, _dictionary_rows(present, reasons)


def _dictionary_rows(present: List[Tuple[str, str]], reasons: Dict[str, str]) -> List[dict]:
    source_rows = {r["VariableName"]: r for r in
                   (dd.REACH_DATA + dd.ODC_REACH_ANIMAL + dd.ODC_REACH_SESSION
                    + dd.ODC_REACH_TOTALS)}
    rows = []
    for src, name in present:
        base = source_rows.get(src)
        note = missing.dictionary_comment(reasons, name)
        if base:
            row = dict(base)
            row["VariableName"] = name
            if note:
                row["Comments"] = (row.get("Comments", "") + " " + note).strip()
            rows.append(row)
        else:
            rows.append(dd._row(name, name.replace("_", " ").capitalize(),
                                "Per-reach measurement.", comments=note))
    answers = [
        dd._row("reach_result", "What this reach did to the pellet",
                "The committed answer for THIS reach. 'Did not move the pellet' is an "
                "answer, not a missing value: it means the pellet's fate was decided by "
                "another reach, or not at all. It is not a claim that the paw never "
                "touched the pellet, which is not measured.",
                "", "text", REACH_RESULT_PV,
                "Retrieved=the animal got the pellet; "
                "Displaced into the scoring area=knocked into the scoring area; "
                "Displaced out of reach=knocked away, unrecoverable; "
                "Did not move the pellet=another reach decided this pellet, or none did; "
                "Undetermined=not yet decided"),
        dd._row("pellet_result", "What happened to this pellet",
                "The fate of the pellet this reach was aimed at, repeated on every reach "
                "aimed at that pellet, so a session reads without joining tables.",
                "", "text", PELLET_RESULT_PV,
                "Retrieved=the animal got the pellet; "
                "Displaced into the scoring area=knocked into the scoring area; "
                "Displaced out of reach=knocked away, unrecoverable; "
                "Never touched=the pellet was not moved by any reach; "
                "Undetermined=not yet decided"),
        dd._row("reach_decided_the_pellet", "Did this reach decide the pellet",
                "Yes on the one reach credited with the pellet's fate, No on every other "
                "reach aimed at the same pellet.",
                "", "text", "Yes; No", "Yes=this reach decided the pellet's fate"),
    ]
    at = len([1 for s, n in present if s in dict(ANIMAL + SESSION + REACH_IDENTITY)])
    return rows[:at] + answers + rows[at:]
