"""Current exports + data dictionaries.

WHY: an ODC-SCI submission is dataset + data dictionary; a dataset with an
undocumented column fails upload. These tests pin that every column the
exports write has a dictionary row, that the dictionaries are in ODC's
9-column shape, and that the folder is rebuilt from a snapshot alone.
"""
import json
from pathlib import Path

import pandas as pd

from mousedb.exporters import data_dictionary as dd
from mousedb.exporters.current import refresh_current


def test_dictionaries_have_the_nine_odc_columns_and_required_fields():
    for name, rows in dd.DICTIONARIES.items():
        assert rows, name
        for r in rows:
            assert list(r.keys()) == dd.ODC_DICTIONARY_COLUMNS, name
            assert r["VariableName"] and r["Title"] and r["Description"], (name, r)


def test_never_computed_columns_say_so():
    txt = {r["VariableName"]: r["Description"] for r in dd.REACH_DATA}
    for col in ("tracking_quality_score", "grasp_aperture_max_mm",
                "apex_distance_to_pellet_mm", "distance_to_interaction"):
        assert "never computed" in txt[col].lower(), col


def _snapshot(tmp_path):
    rd = pd.DataFrame({
        "id": [1, 2], "subject_id": ["CNT_05_01"] * 2, "video_name": ["20260601_CNT0501_P1"] * 2,
        "session_date": ["2026-06-01"] * 2, "tray_type": ["P"] * 2, "run_number": [1, 1],
        "segment_num": [1, 1], "reach_id": [1, 2], "reach_num": [1, 2],
        "outcome": [None, "retrieved"], "causal_reach": [False, True],
        "segment_outcome": ["retrieved"] * 2, "outcome_source": ["algo"] * 2,
        "extended_features": ["{}"] * 2, "test_phase": ["Pillar"] * 2,
    })
    ps = pd.DataFrame({
        "id": [1], "subject_id": ["CNT_05_01"], "session_date": ["2026-06-01"],
        "test_phase": ["Pillar"], "phase_group": ["Baseline"], "tray_type": ["P"],
        "tray_number": [1], "pellet_number": [1], "score": [2], "contact_group": ["contacted"],
        "entered_by": ["excel_import"], "entered_at": ["2026-06-02"],
    })
    snap = tmp_path / "snap"
    snap.mkdir()
    rd.to_parquet(snap / "reach_data.parquet", index=False)
    ps.to_parquet(snap / "pellet_scores.parquet", index=False)
    return snap


def test_refresh_from_snapshot_alone_is_complete(tmp_path):
    out = tmp_path / "current"
    m = refresh_current(_snapshot(tmp_path), out, db_ok=False)
    assert (out / "reach_data.csv").is_file()
    assert (out / "reach_data_DATA_DICTIONARY.csv").is_file()
    assert (out / "manual_scores.csv").is_file()
    assert (out / "MANIFEST.json").is_file() and (out / "README.txt").is_file()
    assert m["files"]["reach_data.csv"]["rows"] == 2
    assert m["files"]["reach_data.csv"]["undocumented_columns"] == []
    assert m["files"]["manual_scores.csv"]["undocumented_columns"] == []
    assert m["complete"] is True
    # the JSON blob is NOT in the CSV (it made the real file 3 GB); it lives
    # in a parquet sidecar keyed by the identity columns
    header = (out / "reach_data.csv").read_text().splitlines()[0].split(",")
    assert "extended_features" not in header and header[0] == "subject_id"
    side = pd.read_parquet(out / "reach_data_extended.parquet")
    assert list(side.columns) == ["subject_id", "video_name", "segment_num", "reach_id", "extended_features"]
    assert m["files"]["reach_data_extended.parquet"]["rows"] == 2
    # ODC sessions honestly reported as not refreshed without db access
    assert any("ODC_sessions" in p for p in m["problems"])
    assert json.loads((out / "MANIFEST.json").read_text())["odc_sessions_refreshed"] is False


def test_an_undocumented_column_makes_the_export_incomplete(tmp_path):
    snap = _snapshot(tmp_path)
    rd = pd.read_parquet(snap / "reach_data.parquet")
    rd["mystery_column"] = 1
    rd.to_parquet(snap / "reach_data.parquet", index=False)
    m = refresh_current(snap, tmp_path / "current", db_ok=False)
    assert m["complete"] is False
    assert "mystery_column" in m["files"]["reach_data.csv"]["undocumented_columns"]
    assert any("would fail" in p for p in m["problems"])
