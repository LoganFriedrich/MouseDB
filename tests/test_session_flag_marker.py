"""A person's Flag Session note travels into the database and the exports, as a marker.

MouseReach writes a top-level ``session_flag`` into every features file: null,
or {session_key, reason, flagged_by, flagged_at}. Owner decision D5-10
(2026-11-19): a flag is only RECORDED, so it can be found later -- nothing
leaves a flagged row out or treats it differently. So the reach import carries
it onto every reach row, the per-reach exports show it as session_flag
(Yes / No) with who, when and why, and the incremental export notices when it
changes.
"""
import json
import sqlite3

import pandas as pd

from mousedb import import_reaches as ir
from mousedb.database import Database
from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import odc_reaches as ox
from mousedb.exporters import reach_summary

VIDEO = "20260102_PROJA0101_P1"
SID = "PROJA_01_01"
FLAG = {"session_key": VIDEO, "reason": "animal distracted by noise",
        "flagged_by": "reviewer_a", "flagged_at": "2026-11-19T17:00:00"}


def _features(folder, flag):
    reach = {"reach_id": 1, "reach_num": 1, "start_frame": 10, "end_frame": 15,
             "duration_frames": 5, "causal_reach": True, "is_first_reach": True,
             "is_last_reach": True, "n_reaches_in_segment": 1, "flagged_for_review": False}
    data = {"video_name": VIDEO, "session_flag": flag,
            "segments": [{"segment_num": 1, "outcome": "retrieved", "reaches": [reach]}]}
    path = folder / (VIDEO + "_features.json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_db()
    conn = sqlite3.connect(str(db.db_path))
    with conn:
        ir.ensure_columns(conn)
    return conn


def test_the_import_records_the_flag_on_every_reach_row(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, FLAG))
    row = conn.execute("SELECT session_flag, session_flag_reason, session_flag_by, "
                       "session_flag_at FROM reach_data WHERE video_name = ?", (VIDEO,)).fetchone()
    assert row == (1, "animal distracted by noise", "reviewer_a", "2026-11-19T17:00:00")


def test_no_flag_is_recorded_as_none(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, None))
    row = conn.execute("SELECT session_flag, session_flag_reason FROM reach_data").fetchone()
    assert row == (None, None)
    assert ir.session_flag_fields({}) == {c: None for c in ir.SESSION_FLAG_COLUMNS}


def test_an_older_database_gets_the_columns(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    conn.execute("CREATE TABLE reach_data (id INTEGER PRIMARY KEY, video_name TEXT, subject_id TEXT, session_date TEXT, tray_type TEXT, segment_outcome TEXT, interaction_frame INTEGER)")
    ir.ensure_columns(conn)
    have = {r[1] for r in conn.execute("PRAGMA table_info(reach_data)")}
    assert set(ir.SESSION_FLAG_COLUMNS) <= have


def _reach_rows(flagged):
    rows = []
    for vid, run in (("20250710_PROJA0101_P1", 1), ("20250710_PROJA0101_P2", 2)):
        f = flagged and run == 1
        rows.append({"subject_id": SID, "video_name": vid, "session_date": "2025-07-10",
                     "tray_type": "P", "run_number": run, "segment_num": 1, "reach_id": 1,
                     "reach_num": 1, "outcome": "retrieved", "causal_reach": 1,
                     "segment_outcome": "retrieved", "start_frame": 1, "end_frame": 5,
                     "attention_score": 0.5,
                     "session_flag": 1 if f else None,
                     "session_flag_reason": FLAG["reason"] if f else None,
                     "session_flag_by": FLAG["flagged_by"] if f else None,
                     "session_flag_at": FLAG["flagged_at"] if f else None,
                     "extended_features": "{}"})
    return pd.DataFrame(rows)


def _build(flagged=True):
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    return ox.build_cohort("PROJA_01", _reach_rows(flagged), subjects, None, None,
                           lambda s: {}, "lab-x")


def test_every_per_reach_file_carries_the_marker_and_keeps_every_row(tmp_path):
    df, rows = _build()
    entry, label = ox.write_cohort(tmp_path, "PROJA_01", df, rows)
    files = {p.name: p for p in tmp_path.rglob("*.csv")}
    for name in ("ODC_reaches_PROJA_01.csv", "reaches_PROJA_01_summary.csv",
                 "reaches_PROJA_01_analysis.csv", "sessions_PROJA_01_analysis.csv"):
        f = pd.read_csv(files[name], dtype=str, keep_default_na=False)
        assert len(f) == 2, name                      # nothing left out
        video = "video_name" if "video_name" in f.columns else "session_video"
        by = f.set_index(video)
        assert by.loc["20250710_PROJA0101_P1", "session_flag"] == "Yes", name
        assert by.loc["20250710_PROJA0101_P2", "session_flag"] == "No", name
        assert by.loc["20250710_PROJA0101_P1", "session_flag_reason"] == FLAG["reason"], name
        assert by.loc["20250710_PROJA0101_P1", "session_flag_by"] == FLAG["flagged_by"], name
        assert by.loc["20250710_PROJA0101_P1", "session_flag_at"] == FLAG["flagged_at"], name
    for key in ("_complete_dictionary", "_summary_dictionary", "_analysis_dictionary",
                "_sessions_dictionary"):
        named = {r["VariableName"]: r for r in entry[key]}
        assert "MARKER ONLY" in named["session_flag"]["Description"], key
    # Not flagged: the note columns say why they are empty.
    complete = pd.read_csv(files["ODC_reaches_PROJA_01.csv"], dtype=str, keep_default_na=False)
    assert complete.set_index("video_name").loc["20250710_PROJA0101_P2",
                                                "session_flag_reason"] == "Not applicable"


def test_the_incremental_export_notices_a_new_flag(tmp_path):
    from mousedb.exporters import incremental
    fps = []
    for flagged in (False, True):
        t = {"reach": _reach_rows(flagged), "subjects": None, "records": None,
             "pellets": None, "video_pellets": None}
        fps.append(incremental.cohort_fingerprints(t, ["PROJA_01"], None, tmp_path, "lab-x"))
    assert fps[0]["PROJA_01"]["fingerprint"] != fps[1]["PROJA_01"]["fingerprint"]
