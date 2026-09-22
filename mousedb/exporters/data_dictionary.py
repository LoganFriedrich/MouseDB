"""Data dictionaries for the current exports, in ODC-SCI's required format.

WHY THIS EXISTS
---------------
An ODC-SCI submission is TWO files: the dataset and a Data Dictionary. A
dataset whose dictionary is missing fails to upload. Until 2026-08-28 no
dictionary existed anywhere in this project (the doc some exporters pointed
at, REACH_KINEMATIC_DATA_DICTIONARY.md, was never written). This module is
the single source of every column's definition; the current-exports
refresher writes one dictionary CSV beside each dataset CSV, and REFUSES to
call an export complete if a column has no dictionary row -- undocumented
columns are listed in the export MANIFEST, loudly.

FORMAT (see Databases/docs/ODC-SCI_submission_standard.md): nine columns,
case-sensitive headers, VariableName / Title / Description required on every
row. PVDescribed is requested since Spring 2026.

Descriptions of the reach-level columns are condensed from MouseReach's
docs/KINEMATICS_FIELDS.md, which is the measured, line-cited account of how
each value is computed and how often it is actually filled. Where that
document says a column is never computed, the dictionary says so too --
a submission must not imply a value exists when it never does.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List

ODC_DICTIONARY_COLUMNS = [
    "VariableName", "Title", "Description", "Unit_of_Measure", "DataType",
    "PermittedValues", "PVDescribed", "MinimumValue", "MaximumValue", "Comments",
]


def _row(name, title, desc, unit="", dtype="", pv="", pvd="", mn="", mx="", comments=""):
    return {"VariableName": name, "Title": title, "Description": desc,
            "Unit_of_Measure": unit, "DataType": dtype, "PermittedValues": pv,
            "PVDescribed": pvd, "MinimumValue": mn, "MaximumValue": mx,
            "Comments": comments}


NEVER_COMPUTED = ("Declared in the data model but never computed by any code; "
                  "always empty. Kept so the column set is stable.")


def never_computed_names(rows=None) -> set:
    """Columns that no code computes, by their own dictionary entry.

    WHY this is asked of the dictionary rather than of the data: a column that happens
    to be empty in one cohort is not the same as a column nothing computes. The frozen
    ASPA workbooks record no species, so SpeciesTyp is empty in those files -- but
    species is a fact a source failed to carry, not a measurement the code declines to
    take, and an export that calls it 'not measured' tells the reader the lab failed to
    look at a mouse. Emptiness is a symptom; this list is the diagnosis.
    """
    rows = rows if rows is not None else (
        REACH_DATA + MANUAL_SCORES + ODC_REACH_ANIMAL + ODC_REACH_SESSION
        + ODC_REACH_TOTALS)
    return {r["VariableName"] for r in rows
            if str(r.get("Description", "")).strip() == NEVER_COMPUTED.strip()}

# ---------------------------------------------------------------------------
# reach_data: one row per detected reach
# ---------------------------------------------------------------------------

REACH_DATA = [
    _row("subject_id", "Subject identifier", "Animal id in PROJECT_CC_SS form (project, cohort number, subject number), e.g. CNT_05_03.", "", "text"),
    _row("video_name", "Video name", "Session video the reach was detected in: YYYYMMDD_{PROJECT}{CCSS}_{tray type}{run}, e.g. 20250624_CNT0115_P2.", "", "text"),
    _row("session_date", "Session date", "Date of the recording, from the video name.", "", "date"),
    _row("tray_type", "Tray type", "Which tray was presented: P = pillar (the scored task), E = easy, F = flat (training trays).", "", "text", "P; E; F", "P=pillar; E=easy; F=flat"),
    _row("run_number", "Run number", "Which run of that tray type in the session (1-4).", "", "integer", "", "", "1", "4"),
    _row("segment_num", "Segment number", "Which pellet presentation the reach belongs to; segment N contains pellet N. Segment boundaries are the frames where the scoring area advances.", "count", "integer", "", "", "1"),
    _row("reach_id", "Reach id", "Number of this reach, unique within the video.", "count", "integer"),
    _row("reach_num", "Reach number in segment", "Position of this reach within its segment, counting from 1.", "count", "integer", "", "", "1"),
    _row("outcome", "Pellet outcome (causal reach only)", "What happened to the pellet, recorded on the reach that decided it: retrieved, displaced_sa (knocked into the scoring area), displaced_outside, missed, or a non-committal label. Empty on every reach that was not the causal reach.", "", "text", "retrieved; displaced_sa; displaced_outside; missed; triaged; uncertain; abnormal_exception", "retrieved=eaten; displaced_sa=knocked into scoring area; displaced_outside=knocked out of the apparatus; missed=untouched on its pillar; triaged/uncertain=algorithm did not commit; abnormal_exception=pellet moved by something other than a reach"),
    _row("causal_reach", "Is the causal reach", "True if this is the reach that decided the pellet's fate (moved or retrieved it, or was the last reach of a missed pellet).", "", "boolean", "TRUE; FALSE", "TRUE=this reach decided the outcome"),
    _row("interaction_frame", "Interaction frame", "Video frame at which the paw met the pellet. Recorded on the causal reach only.", "frame index", "integer"),
    _row("distance_to_interaction", "Frames from apex to interaction", NEVER_COMPUTED, "frames", "integer", "", "", "", "", "Always empty (see KINEMATICS_FIELDS.md 5.2)."),
    _row("is_first_reach", "First reach of segment", "True if this is the first reach in its segment.", "", "boolean", "TRUE; FALSE"),
    _row("is_last_reach", "Last reach of segment", "True if this is the last reach in its segment.", "", "boolean", "TRUE; FALSE"),
    _row("n_reaches_in_segment", "Reaches in segment", "How many reaches were detected in this reach's segment.", "count", "integer", "", "", "1"),
    _row("start_frame", "Reach start frame", "Video frame the reach begins (paw leaves the slit region).", "frame index", "integer", "", "", "0"),
    _row("apex_frame", "Reach apex frame", "Video frame of maximum extension.", "frame index", "integer", "", "", "0"),
    _row("end_frame", "Reach end frame", "Video frame the reach ends (paw withdrawn).", "frame index", "integer", "", "", "0"),
    _row("duration_frames", "Reach duration", "end_frame - start_frame + 1.", "frames", "integer", "", "", "1"),
    _row("max_extent_pixels", "Maximum extent (pixels)", "How far the paw reached, in pixels. Empty on every reach from the current detector (only legacy detector output carries a value, and that value measured the sideways axis).", "pixels", "number", "", "", "", "", "See KINEMATICS_FIELDS.md 5.3 before using."),
    _row("max_extent_ruler", "Maximum extent (ruler lengths)", "max_extent_pixels divided by the 9 mm ruler length in pixels. Same coverage caveat as max_extent_pixels.", "ruler lengths (1.0 = 9 mm)", "number"),
    _row("max_extent_mm", "Maximum extent (mm)", "max_extent_ruler x 9.0. Same coverage caveat as max_extent_pixels.", "mm", "number"),
    _row("velocity_at_apex_px_per_frame", "Wrist speed at apex (px/frame)", "Wrist (RightHand point) displacement in the frame step starting at the apex. Empty when the apex is the reach's last frame.", "pixels per frame", "number", "", "", "0"),
    _row("velocity_at_apex_mm_per_sec", "Wrist speed at apex (mm/s)", "velocity_at_apex_px_per_frame converted with the 9 mm ruler and an assumed 30 frames per second.", "mm per second", "number", "", "", "0", "", "Assumes 30 fps."),
    _row("peak_velocity_px_per_frame", "Peak wrist speed", "Largest single-frame wrist displacement during the reach.", "pixels per frame", "number", "", "", "0"),
    _row("mean_velocity_px_per_frame", "Mean wrist speed", "Mean single-frame wrist displacement during the reach.", "pixels per frame", "number", "", "", "0"),
    _row("trajectory_straightness", "Trajectory straightness", "Straight-line distance from first to last wrist position divided by the total path length. 1.0 = perfectly straight.", "dimensionless", "number", "", "", "0", "1"),
    _row("trajectory_smoothness", "Trajectory smoothness", "1 / (1 + mean absolute third difference of wrist position). 1.0 = perfectly smooth. Empty for reaches shorter than 3 frames.", "dimensionless", "number", "", "", "0", "1"),
    _row("hand_angle_at_apex_deg", "Paw angle at apex", "Orientation of the paw at the apex, from the angle between the two paw-side points.", "degrees", "number", "", "", "-180", "180"),
    _row("hand_rotation_total_deg", "Total paw rotation", "Sum of absolute frame-to-frame paw angle changes over the reach (wraparound handled).", "degrees", "number", "", "", "0"),
    _row("grasp_aperture_max_mm", "Maximum grasp aperture", NEVER_COMPUTED, "mm", "number", "", "", "", "", "Always empty; paw_width_proxy_max_mm in the extended features is the working replacement."),
    _row("grasp_aperture_at_contact_mm", "Grasp aperture at contact", NEVER_COMPUTED, "mm", "number", "", "", "", "", "Always empty."),
    _row("head_width_at_apex_mm", "Head width at apex", "Ear-to-ear distance at the apex; a body-size proxy. Empty unless both ears tracked with likelihood above 0.7.", "mm", "number", "", "", "0"),
    _row("nose_to_slit_at_apex_mm", "Nose to slit at apex", "Distance from the nose to the right edge of the slit at the apex. Same likelihood gate.", "mm", "number", "", "", "0"),
    _row("head_angle_at_apex_deg", "Head angle at apex", "Ear-to-ear angle at the apex. Same likelihood gate.", "degrees", "number", "", "", "-180", "180"),
    _row("head_angle_change_deg", "Head angle change", "Head angle at apex minus head angle at reach start, wrapped to -180..180.", "degrees", "number", "", "", "-180", "180"),
    _row("apex_distance_to_pellet_mm", "Apex distance to pellet", NEVER_COMPUTED, "mm", "number", "", "", "", "", "Always empty."),
    _row("lateral_deviation_mm", "Lateral deviation", NEVER_COMPUTED, "mm", "number", "", "", "", "", "Always empty; righthand_lateral_deviation_mm in the extended features is the working replacement."),
    _row("mean_likelihood", "Mean tracking confidence", "Mean DeepLabCut confidence for the wrist point across the reach.", "dimensionless", "number", "", "", "0", "1"),
    _row("frames_low_confidence", "Low-confidence frames", "Number of frames in the reach where wrist confidence was below 0.5.", "frames", "integer", "", "", "0"),
    _row("tracking_quality_score", "Tracking quality score", NEVER_COMPUTED, "", "number", "", "", "", "", "Always empty."),
    _row("flagged_for_review", "Flagged for review (reach)", "Per-reach review flag. Never set by any code (always FALSE); the segment-level flag is segment_outcome_flagged.", "", "boolean", "TRUE; FALSE", "", "", "", "Always FALSE."),
    _row("flag_reason", "Flag reason", "Never set by any code; always empty.", "", "text"),
    _row("segment_outcome", "Segment outcome", "The pellet outcome of the whole segment this reach belongs to (same vocabulary as outcome), on every reach of the segment.", "", "text", "retrieved; displaced_sa; displaced_outside; missed; triaged; uncertain; abnormal_exception", "see outcome"),
    _row("segment_outcome_confidence", "Segment outcome confidence", "The outcome detector's confidence in segment_outcome.", "dimensionless", "number", "", "", "0", "1"),
    _row("segment_outcome_flagged", "Segment flagged for review", "True if the outcome detector could not commit and flagged the segment for a human.", "", "boolean", "TRUE; FALSE"),
    _row("attention_score", "Attention score", "Outcome detector's estimate of how engaged the animal was with the pellet in this segment. Observed values run above 1 (tens), so treat it as a relative score, not a 0-1 fraction.", "", "number", "", "", "0"),
    _row("pellet_position_idealness", "Pellet position idealness", "Outcome detector's estimate of how well the pellet sat on its pillar (1 = ideal).", "dimensionless", "number", "", "", "0", "1"),
    _row("source_file", "Source file", "The _features.json file the row was synced from.", "", "text"),
    _row("extractor_version", "Extractor version", "Version of the kinematics extractor that produced the row.", "", "text"),
    _row("imported_at", "Imported at", "When the row was written to the database.", "", "datetime"),
    _row("processed_by", "Processed by", "Machine that ran the pipeline for this video.", "", "text"),
    _row("mousereach_version", "MouseReach version", "Version of the MouseReach package that processed the video.", "", "text"),
    _row("dlc_scorer", "DLC scorer", "DeepLabCut model (scorer string) that produced the pose used.", "", "text"),
    _row("segmenter_version", "Segmenter version", "Version of the segmentation algorithm used.", "", "text"),
    _row("reach_detector_version", "Reach detector version", "Version of the reach detection algorithm used.", "", "text"),
    _row("outcome_detector_version", "Outcome detector version", "Version of the pellet outcome algorithm used.", "", "text"),
    _row("outcome_source", "Outcome source", "Where this segment's outcome came from: algo (algorithm only), human_review (a reviewer answered in the triage/causal review tool), ground_truth (exhaustive ground-truth annotation).", "", "text", "algo; human_review; ground_truth", "algo=algorithm only; human_review=reviewer answer; ground_truth=exhaustive annotation"),
    _row("reviewed_by", "Reviewed by", "Username of the human reviewer, when outcome_source is not algo.", "", "text"),
    _row("algo_outcome", "Algorithm's original outcome", "What the algorithm said before a human overrode it (empty when no override).", "", "text"),
    _row("algo_causal_reach_id", "Algorithm's original causal reach", "Reach id the algorithm blamed before a human overrode it. Empty on current data.", "count", "integer"),
    _row("test_phase", "Test phase", "Phase of the experiment the session belongs to, derived from the cohort's date structure (see manual_scores dictionary for values).", "", "text"),
    _row("phase_group", "Phase group", "Statistical grouping of test_phase (Baseline, Post_Injury_2-4, Post_Rehab_Test, ...).", "", "text"),
    _row("contact_group", "Contact group (reach outcome)", "missed if the reach's outcome is missed, contacted if the pellet was displaced or retrieved; empty on non-causal reaches.", "", "text", "missed; contacted", "missed=pellet untouched; contacted=displaced or retrieved"),
    _row("segment_contact_group", "Contact group (segment outcome)", "missed / contacted derived from segment_outcome, on every reach of the segment.", "", "text", "missed; contacted", "missed=pellet untouched; contacted=displaced or retrieved"),
    _row("extended_features", "Extended features (JSON)", "The full per-reach extended feature block (161 values: per-paw-point kinematics) as a JSON string. See KINEMATICS_FIELDS.md section 6.", "", "text (JSON)"),
]

# ---------------------------------------------------------------------------
# manual_scores: one row per pellet scored by hand from the tray
# ---------------------------------------------------------------------------

MANUAL_SCORES = [
    _row("subject_id", "Subject identifier", "Animal id in PROJECT_CC_SS form, e.g. CNT_05_03.", "", "text"),
    _row("session_date", "Session date", "Date of the testing session.", "", "date"),
    _row("test_phase", "Test phase", "Phase of the experiment the session belongs to, derived from the cohort's date structure (not the operator's entry).", "", "text", "Flat_Training; Pillar_Training; Pillar; Post_Injury_1; Post_Injury_2; Post_Injury_3; Post_Injury_4; Rehab_Easy; Rehab_Flat; Rehab_Pillar; unassigned", "Training phases precede injury; Pillar=pre-injury baseline; Post_Injury_N=post-injury test N; Rehab_*=rehabilitation on that tray; unassigned=could not be derived"),
    _row("phase_group", "Phase group", "Statistical grouping of test_phase (Baseline, Post_Injury_2-4, Post_Rehab_Test, ...).", "", "text"),
    _row("tray_type", "Tray type", "P = pillar, E = easy, F = flat.", "", "text", "P; E; F", "P=pillar; E=easy; F=flat"),
    _row("tray_number", "Tray number", "Which tray of the session (1-4).", "", "integer", "", "", "1", "4"),
    _row("pellet_number", "Pellet number", "Pellet position on the tray (1-20).", "", "integer", "", "", "1", "20"),
    _row("score", "Manual score", "Post-hoc tray inspection score: 0 = missed (still on its pillar), 1 = displaced (moved but not eaten), 2 = retrieved (eaten).", "", "integer", "0; 1; 2", "0=missed; 1=displaced; 2=retrieved", "0", "2"),
    _row("contact_group", "Contact group", "missed if score is 0, else contacted.", "", "text", "missed; contacted", "missed=score 0; contacted=score 1 or 2"),
    _row("entered_by", "Entered by", "Who or what wrote the row (excel_import for the sheet importer).", "", "text"),
    _row("entered_at", "Entered at", "When the row was written to the database.", "", "datetime"),
    _row("id", "Row id", "Database row identifier.", "", "integer"),
]

# ---------------------------------------------------------------------------
# ODC sessions: one row per animal per session (the 2_ODC_Animal_Tracking shape)
# ---------------------------------------------------------------------------

def _odc_session_rows() -> List[dict]:
    rows = [
        _row("Animal", "Animal", "Animal id in PROJECT_CC_SS form.", "", "text"),
        _row("Cohort", "Cohort", "Cohort id, e.g. CNT_05.", "", "text"),
        _row("Sex", "Sex", "Animal sex.", "", "text", "M; F", "M=male; F=female"),
        _row("Date", "Session date", "Date of the testing session.", "", "date"),
        _row("Test_Phase", "Test phase", "Phase of the experiment the session belongs to (see manual_scores dictionary for values).", "", "text"),
        _row("Days_Post_Injury", "Days post injury", "Days since the injury surgery; empty before injury.", "days", "integer"),
        _row("Weight_g", "Body weight", "Body weight recorded for the session.", "g", "number", "", "", "0"),
        _row("Weight_Pct", "Body weight, percent of baseline", "Weight as a percentage of the animal's baseline weight.", "percent", "number", "", "", "0"),
        _row("Injury_Date", "Injury date", "Date of the contusion injury surgery; empty if not yet injured.", "", "date"),
        _row("Injury_Force_kDyn", "Injury force", "Impactor force of the contusion.", "kilodyne", "number"),
        _row("Injury_Displacement_um", "Injury displacement", "Impactor displacement of the contusion.", "micrometre", "number"),
    ]
    for i in range(1, 5):
        p = "Tray%d" % i
        rows += [
            _row(p + "_Type", "Tray %d type" % i, "Tray type presented as tray %d (P/E/F); empty if fewer trays." % i, "", "text", "P; E; F"),
            _row(p + "_Presented", "Tray %d pellets presented" % i, "Pellets presented on tray %d (20 per tray)." % i, "count", "integer", "", "", "0", "20"),
            _row(p + "_Missed", "Tray %d missed" % i, "Pellets scored missed (0) on tray %d." % i, "count", "integer", "", "", "0", "20"),
            _row(p + "_Displaced", "Tray %d displaced" % i, "Pellets scored displaced (1) on tray %d." % i, "count", "integer", "", "", "0", "20"),
            _row(p + "_Retrieved", "Tray %d retrieved" % i, "Pellets scored retrieved (2) on tray %d." % i, "count", "integer", "", "", "0", "20"),
            _row(p + "_Contacted", "Tray %d contacted" % i, "Displaced + retrieved on tray %d." % i, "count", "integer", "", "", "0", "20"),
            _row(p + "_Miss_Pct", "Tray %d percent missed" % i, "Missed / presented x 100 on tray %d." % i, "percent", "number", "", "", "0", "100"),
            _row(p + "_Displaced_Pct", "Tray %d percent displaced" % i, "Displaced / presented x 100.", "percent", "number", "", "", "0", "100"),
            _row(p + "_Retrieved_Pct", "Tray %d percent retrieved" % i, "Retrieved / presented x 100.", "percent", "number", "", "", "0", "100"),
            _row(p + "_Contacted_Pct", "Tray %d percent contacted" % i, "Contacted / presented x 100.", "percent", "number", "", "", "0", "100"),
        ]
    for k, t in (("Presented", "presented"), ("Missed", "scored missed"), ("Displaced", "scored displaced"),
                 ("Retrieved", "scored retrieved"), ("Contacted", "contacted (displaced + retrieved)")):
        rows.append(_row("Daily_" + k, "Daily %s" % k.lower(), "Pellets %s across all trays in the session." % t, "count", "integer", "", "", "0"))
    for k in ("Miss", "Displaced", "Retrieved", "Contacted"):
        rows.append(_row("Daily_%s_Pct" % k, "Daily percent %s" % k.lower(), "Session total for that category / pellets presented x 100.", "percent", "number", "", "", "0", "100"))
        rows.append(_row("Avg_%s_Pct" % k, "Average per-tray percent %s" % k.lower(), "Mean of the per-tray percentages for that category.", "percent", "number", "", "", "0", "100"))
    return rows


ODC_SESSIONS = _odc_session_rows()

# ---------------------------------------------------------------------------
# ODC reaches: one row per reach, animal + session details repeated on each row
# (the per-reach shared-dataset shape). Reach columns reuse REACH_DATA; columns
# copied from the tracking sheets are described by sheet_column_row().
# ---------------------------------------------------------------------------

ODC_REACH_ANIMAL = [
    _row("SubjectID", "Subject identifier", "The lab's own animal id (a frozen letter cohort's animals decoded back to letter + number, e.g. K03; otherwise PROJECT_CC_SS).", "", "text"),
    _row("SpeciesTyp", "Species", "Species. From the sheet's ODC tab when it has one, else the study facts (mousedb study-facts).", "", "text"),
    _row("SpeciesStrainTyp", "Strain", "Strain or line. From the sheet's ODC tab when it has one, else the study facts.", "", "text"),
    _row("Animal_origin", "Animal origin", "Supplier or colony. From the sheet's ODC tab when it has one, else the study facts.", "", "text"),
    _row("AgeVal", "Age", "Age in weeks at the session: the date of birth subtracted from the session date. Where no date of birth is recorded, the age at surgery as written in the sheet's ODC tab is used instead.", "weeks", "text", "", "", "", "",
         "'Not recorded' in this column means no date of birth is recorded for the animal and the sheet carries no age at surgery either, so the age cannot be worked out from anything held. One date of birth per animal in the tracking sheet fills it for every session that animal ever ran."),
    _row("BodyWgtMeasrVal", "Body weight at surgery", "Weight recorded on the injury surgery row of the tracking sheet.", "g", "number", "", "", "0"),
    _row("SexTyp", "Sex", "Animal sex, from the database subject record, else the tracking sheet.", "", "text"),
    _row("InjGroupAssignTyp", "Group assignment", "Study group from the sheet's ODC tab when present, else the cohort id.", "", "text"),
    _row("Laboratory", "Laboratory", "The laboratory (mousedb config lab_name).", "", "text"),
    _row("StudyLeader", "Study leader", "Study leader (study facts).", "", "text"),
    _row("Exclusion_in_origin_study", "Exclusion in origin study", "Total exclusion when a surgery row records Survived = N; No exclusion when every recorded surgery survived; empty when survival is not recorded.", "", "text", "Total exclusion; No exclusion", "Total exclusion=did not survive a surgery; No exclusion=survived every recorded surgery"),
    _row("Exclusion_reason", "Exclusion reason", "Why the animal was excluded, when Exclusion_in_origin_study says so.", "", "text"),
    _row("Cause_of_Death", "Cause of death", "Not derived by this export (empty); see the surgery columns for survival.", "", "text"),
    _row("Injury_device", "Injury device", "Injury device from the sheet's ODC tab when it names one, else the study facts.", "", "text"),
    _row("Injury_level", "Injury level", "Spinal level of the injury from the tracking sheet.", "", "text"),
    _row("Injury_details", "Injury details", "Measured injury parameters from the tracking sheet (force, displacement, velocity, dwell) as text.", "", "text"),
]

ODC_REACH_SESSION = [
    _row("Test_Date", "Test date", "Date of the recording session (YYYY-MM-DD).", "", "date"),
    _row("Tray_ID", "Tray id", "Tray type letter and run number within the session: P = pillar, E = easy, F = flat; P2 = the second pillar tray that day.", "", "text"),
    _row("Session_ID", "Session id", "SubjectID-YYYYMMDD-Tray_ID; unique per animal, day and tray.", "", "text"),
    _row("Injury_Type", "Injury type", "Injury type from the tracking sheet, with the intended force for a contusion (e.g. Contusion-60kD).", "", "text"),
    _row("Test_Type", "Test type", "Phase of the experiment the session belongs to (same as test_phase; see manual_scores dictionary).", "", "text"),
    _row("Test_Type_Grouped", "Test type grouped", "Statistical grouping of Test_Type (same as phase_group).", "", "text"),
    _row("Days_Post_Injury", "Days post injury", "Days from the injury surgery date to the session; empty before the injury or when no injury date is recorded.", "days", "integer", "", "", "0"),
]

ODC_REACH_TOTALS = [
    _row("Total_Swipes_AI", "Reaches in the video", "Number of reaches the pipeline detected in this session video.", "count", "integer", "", "", "0"),
    _row("Attention_AI", "Mean attention score", "Mean over the video's pellet segments of the outcome detector's attention score (same scale as attention_score).", "", "number", "", "", "0"),
    _row("Video_Displaced", "Pellets displaced (video)", "Pellet segments in this video whose outcome is displaced_sa or displaced_outside. Counts pellets, not reaches.", "count", "integer", "", "", "0", "20"),
    _row("Video_Retrieved", "Pellets retrieved (video)", "Pellet segments in this video whose outcome is retrieved.", "count", "integer", "", "", "0", "20"),
    _row("Video_Contacted", "Pellets contacted (video)", "Video_Displaced + Video_Retrieved.", "count", "integer", "", "", "0", "20"),
    _row("Manual_Displaced", "Pellets displaced (hand score)", "Pellets scored 1 (displaced) by post-hoc tray inspection for the same animal, date, tray type and tray number.", "count", "integer", "", "", "0", "20"),
    _row("Manual_Retrieved", "Pellets retrieved (hand score)", "Pellets scored 2 (retrieved) for the same tray.", "count", "integer", "", "", "0", "20"),
    _row("Manual_Contacted", "Pellets contacted (hand score)", "Manual_Displaced + Manual_Retrieved.", "count", "integer", "", "", "0", "20"),
    _row("Contacted_Match", "Contacted difference", "Absolute difference between Manual_Contacted and Video_Contacted; empty when either is missing.", "count", "integer", "", "", "0", "20"),
]


def sheet_column_row(column: str, tab: str, field: str, record_no: int = 1) -> dict:
    """Dictionary row for a column copied from a tracking-sheet tab (see animal_records)."""
    which = "" if record_no == 1 else " (the animal's row %d on that tab)" % record_no
    return _row(column, "%s: %s" % (tab, field),
                "Copied as written from the '%s' column of the '%s' tab of this cohort's "
                "tracking sheet%s. Text; dates as YYYY-MM-DD." % (field, tab, which), "", "text")


def write_rows(rows: List[dict], out_path: Path) -> Path:
    """Write dictionary rows built at run time (the per-reach export's columns vary by cohort)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ODC_DICTIONARY_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out_path

DICTIONARIES: Dict[str, List[dict]] = {
    "reach_data": REACH_DATA,
    "manual_scores": MANUAL_SCORES,
    "ODC_sessions": ODC_SESSIONS,
}


def write_dictionary(name: str, out_path: Path) -> Path:
    rows = DICTIONARIES[name]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ODC_DICTIONARY_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out_path


def undocumented_columns(name: str, columns: Iterable[str]) -> List[str]:
    """Columns present in a dataset that the dictionary does not describe."""
    known = {r["VariableName"] for r in DICTIONARIES[name]}
    return [c for c in columns if c not in known]
