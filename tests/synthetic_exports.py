"""A folder of export files written by the real exporters from synthetic inputs.

WHY: some checks are about the files the exports ship -- no byte that breaks a
CSV reader, no column name ODC refuses. They would otherwise read this computer's live
exports folder, so they ran only where the lab's data was configured, read the
lab's data while doing it, and tested nothing anywhere else. They now run on
files the same code writes here, from inputs that include the awkward values
(commas, quotes, tabs, characters outside ASCII) the write path must clean.
"""
from pathlib import Path

import pandas as pd

# One value carrying every hazard a cell can bring: a comma, a double quote, a
# tab and a character outside plain ASCII (written as an escape so this file
# stays ASCII).
AWKWARD = 'C5, "rostral"\tside µ'


def _snapshot(snap: Path) -> Path:
    snap.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "id": [1, 2], "subject_id": ["PROJA_01_01"] * 2,
        "video_name": ["20260601_PROJA0101_P1"] * 2, "session_date": ["2026-06-01"] * 2,
        "tray_type": ["P"] * 2, "run_number": [1, 1], "segment_num": [1, 1],
        "reach_id": [1, 2], "reach_num": [1, 2], "outcome": [None, "retrieved"],
        "causal_reach": [False, True], "segment_outcome": ["retrieved"] * 2,
        "outcome_source": ["algo"] * 2, "extended_features": ["{}"] * 2,
        "test_phase": ["Pillar"] * 2,
    }).to_parquet(snap / "reach_data.parquet", index=False)
    pd.DataFrame({
        "id": [1], "subject_id": ["PROJA_01_01"], "session_date": ["2026-06-01"],
        "test_phase": ["Pillar"], "phase_group": ["Baseline"], "tray_type": ["P"],
        "tray_number": [1], "pellet_number": [1], "score": [2],
        "contact_group": ["contacted"], "entered_by": ["excel_import"],
        "entered_at": ["2026-06-02"],
    }).to_parquet(snap / "pellet_scores.parquet", index=False)
    return snap


def _cohort_tables():
    """One cohort's per-reach table and its dictionary rows, built by the
    exporter's own build_cohort from synthetic reaches and sheet records."""
    from mousedb.exporters import odc_reaches as ox
    reach = []
    for seg, outcome in enumerate(["retrieved", "displaced_sa"], 1):
        for n in (1, 2):
            reach.append({"id": len(reach), "subject_id": "PROJA_01_01",
                          "video_name": "20250710_PROJA0101_P1", "session_date": "2025-07-10",
                          "tray_type": "P", "run_number": 1, "segment_num": seg,
                          "reach_id": len(reach), "reach_num": n, "segment_outcome": outcome,
                          "attention_score": 0.5, "test_phase": "Pillar",
                          "phase_group": "Baseline", "extended_features": "{}"})
    records = pd.DataFrame([
        {"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01", "source_tab": t,
         "record_no": 1, "field": f, "value": v}
        for t, f, v in (("4_Contusion_Injury_Details", "Surgery_Date", "2025-07-02"),
                        ("4_Contusion_Injury_Details", "Subject_Weight (g)", "22.4"),
                        ("4_Contusion_Injury_Details", "Actual_kd", "65"),
                        ("4_Contusion_Injury_Details", "Contusion_Location", AWKWARD))])
    subjects = pd.DataFrame([{"subject_id": "PROJA_01_01", "sex": "F", "date_of_birth": None}])
    return ox.build_cohort("PROJA_01", pd.DataFrame(reach), subjects, records, None,
                           lambda sid: {"SpeciesStrainTyp": "strain-x"}, "lab-x")


def build(out: Path) -> Path:
    """Write the export files into ``out`` and return it."""
    from mousedb.exporters import current, odc_reaches as ox
    from mousedb.exporters import data_dictionary as dd

    out = Path(out)
    # reach_data / manual_scores and their dictionaries, as the refresh writes
    # them. The per-reach ODC step is written separately below, from a cohort
    # table built for the purpose (this snapshot holds only two tables).
    real = current._odc_reaches_csvs
    current._odc_reaches_csvs = lambda *a, **k: None
    try:
        # lock_dir: this runs from a module-scoped fixture, outside the per-test
        # fixture that moves the exports lock out of the production folder.
        current.refresh_current(_snapshot(out.parent / "snapshot"), out, db_ok=False,
                                lock_dir=out.parent / "exports_lock")
    finally:
        current._odc_reaches_csvs = real
    df, rows = _cohort_tables()
    entry, _label = ox.write_cohort(out, "PROJA_01", df, rows)
    # Its dictionaries: the rows write_cohort hands back, written by the same
    # writer export() uses for the shared dictionaries.
    for key, name in (("_complete_dictionary", "ODC_reaches_DATA_DICTIONARY.csv"),
                      ("_summary_dictionary", "reaches_summary_DATA_DICTIONARY.csv"),
                      ("_analysis_dictionary", "reaches_analysis_DATA_DICTIONARY.csv"),
                      ("_sessions_dictionary", "sessions_analysis_DATA_DICTIONARY.csv")):
        if entry.get(key):
            dd.write_rows(entry[key], out / name)
    for name in dd.DICTIONARIES:
        target = out / ("%s_DATA_DICTIONARY.csv" % name)
        if not target.exists():
            dd.write_dictionary(name, target)
    return out


def csv_files(folder: Path):
    """Every CSV in the folder and below, datasets and dictionaries alike."""
    return sorted(Path(folder).rglob("*.csv"))
