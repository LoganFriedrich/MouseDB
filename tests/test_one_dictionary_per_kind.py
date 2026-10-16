"""One data dictionary per kind of document, never one per cohort.

WHY: every cohort's file of a kind has the same columns, so fourteen copies of
one dictionary were fourteen things to keep in step, and a folder a person had
to sort through (2026-10-16). Where a cohort's tracking sheet adds columns
another's lacks, the shared dictionary must describe the union -- every column
of every file still has its entry, which is what an ODC upload checks -- and
the manifest must say which variables only some cohorts have, because that
difference is a real issue to resolve at the sheets, not something to hide.
"""
import pandas as pd
import pytest

from mousedb.exporters import export_exclusions
from mousedb.exporters import odc_reaches as ox


def _reach_rows(subject, video, date):
    rows = []
    for seg, outcome in ((1, "retrieved"), (2, "untouched")):
        for n in (1, 2):
            rows.append({"subject_id": subject, "video_name": video, "session_date": date,
                         "tray_type": "P", "run_number": 1, "segment_num": seg,
                         "reach_id": len(rows) + 1, "reach_num": n, "segment_outcome": outcome,
                         "attention_score": 0.5, "test_phase": "Pillar", "phase_group": "Baseline",
                         "extended_features": "{}"})
    return rows


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    # The exclusion pass reads the pipeline and the sheets; nothing to leave out here.
    monkeypatch.setattr(export_exclusions, "compute", lambda *a, **k: export_exclusions.Exclusions())
    snap = tmp_path / "snap"
    snap.mkdir()
    reach = (_reach_rows("PROJA_01_01", "20250710_PROJA0101_P1", "2025-07-10")
             + _reach_rows("PROJA_02_01", "20250910_PROJA0201_P1", "2025-09-10"))
    pd.DataFrame(reach).to_parquet(snap / "reach_data.parquet", index=False)
    pd.DataFrame([{"subject_id": s, "sex": "F", "date_of_birth": None}
                  for s in ("PROJA_01_01", "PROJA_02_01")]).to_parquet(snap / "subjects.parquet", index=False)
    # Cohort 01's sheet has a contusion tab; cohort 02's has an injection tab instead.
    records = [("PROJA_01_01", "PROJA_01", "4_Contusion_Injury_Details", "Actual_kd", "65"),
               ("PROJA_01_01", "PROJA_01", "4_Contusion_Injury_Details", "Surgery_Date", "2025-07-02"),
               ("PROJA_02_01", "PROJA_02", "5_SC_Injection_Details", "Injected_Virus", "virus-b")]
    pd.DataFrame([{"subject_id": s, "cohort_id": c, "source_tab": t, "record_no": 1, "field": f, "value": v}
                  for s, c, t, f, v in records]).to_parquet(snap / "animal_records.parquet", index=False)
    return snap


def _header(path):
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def test_one_dictionary_per_kind_documents_every_column_of_every_cohort(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    out.mkdir(parents=True)
    # Left over from before: a per-cohort dictionary of each kind.
    (out / "ODC_reaches_PROJA_01_DATA_DICTIONARY.csv").write_text("old")
    (out / "reaches_PROJA_01_summary_DATA_DICTIONARY.csv").write_text("old")

    m = ox.export(snapshot, out)

    names = sorted(p.name for p in out.glob("*DATA_DICTIONARY.csv"))
    assert names == ["ODC_reaches_DATA_DICTIONARY.csv", "reaches_analysis_DATA_DICTIONARY.csv",
                     "reaches_summary_DATA_DICTIONARY.csv", "sessions_analysis_DATA_DICTIONARY.csv"]
    # Archived, never deleted.
    archived = sorted(p.name for p in (tmp_path / "_archived").rglob("*_DATA_DICTIONARY.csv"))
    assert archived == ["ODC_reaches_PROJA_01_DATA_DICTIONARY.csv",
                        "reaches_PROJA_01_summary_DATA_DICTIONARY.csv"]

    complete = set(pd.read_csv(out / "ODC_reaches_DATA_DICTIONARY.csv")["VariableName"])
    summary = set(pd.read_csv(out / "reaches_summary_DATA_DICTIONARY.csv")["VariableName"])
    for cohort in ("PROJA_01", "PROJA_02"):
        assert set(_header(out / ("ODC_reaches_%s.csv" % cohort))) <= complete, cohort
        assert set(_header(out / ("reaches_%s_summary.csv" % cohort))) <= summary, cohort

    # The sheet columns only one cohort has are named, with the cohort that has them.
    entry = m["files"]["ODC_reaches_DATA_DICTIONARY.csv"]
    only_some = entry["variables_not_in_every_cohort"]
    assert only_some["Contusion_Actual_kd"] == ["PROJA_01"]
    assert only_some["Injection_Injected_Virus"] == ["PROJA_02"]
    assert any("only some cohorts" in p for p in m["problems"])
    assert m["files"]["reaches_summary_DATA_DICTIONARY.csv"]["variables_not_in_every_cohort"] == {}
    assert m["files"]["ODC_reaches_PROJA_01.csv"]["dictionary"] == "ODC_reaches_DATA_DICTIONARY.csv"
    assert m["files"]["reaches_PROJA_02_summary.csv"]["dictionary"] == "reaches_summary_DATA_DICTIONARY.csv"


def test_a_one_cohort_export_keeps_the_other_cohorts_variables(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    out.mkdir(parents=True)
    ox.export(snapshot, out)
    ox.export(snapshot, out, cohorts=["PROJA_02"])
    complete = set(pd.read_csv(out / "ODC_reaches_DATA_DICTIONARY.csv")["VariableName"])
    assert set(_header(out / "ODC_reaches_PROJA_01.csv")) <= complete


def test_entries_that_disagree_are_reported_not_hidden():
    order, rows, seen, conflicts = [], {}, {}, []
    base = {"VariableName": "x", "Title": "X", "Description": "one thing", "Comments": "a"}
    ox._merge_dictionary(order, rows, [{"VariableName": "w", "Title": "W"}, base], "C1", seen, conflicts)
    ox._merge_dictionary(order, rows, [dict(base, Comments="a For CSV safety, b.")], "C2", seen, conflicts)
    assert conflicts == [] and rows["x"]["Comments"] == "a For CSV safety, b."
    ox._merge_dictionary(order, rows, [dict(base, Description="another thing"),
                                      {"VariableName": "y", "Title": "Y"}], "C3", seen, conflicts)
    assert len(conflicts) == 1 and "Description" in conflicts[0] and "C1's" in conflicts[0]
    assert rows["x"]["Description"] == "one thing"
    assert order == ["w", "x", "y"]                 # a new variable lands after its predecessor
