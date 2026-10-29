"""The current exports are grouped by project, then cohort, with one combined file
per kind per project.

WHY (2026-10-29): every cohort's files side by side in one folder made finding one
cohort, or one project, a read through a hundred names; and a person wanting a
whole project had to stitch the cohort files together by hand. The combined file
must be exactly the cohort files put together -- the same cells, nothing
recomputed -- and must hold every cohort even after a one-cohort export.
"""
import pandas as pd
import pytest

from mousedb.exporters import export_exclusions, layout
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
    monkeypatch.setattr(export_exclusions, "compute", lambda *a, **k: export_exclusions.Exclusions())
    snap = tmp_path / "snap"
    snap.mkdir()
    subjects = [("PROJA_01_01", "20250710_PROJA0101_P1", "2025-07-10"),
                ("PROJA_02_01", "20250910_PROJA0201_P1", "2025-09-10"),
                ("PROJAB_01_01", "20251010_PROJAB0101_P1", "2025-10-10")]
    pd.DataFrame([r for s in subjects for r in _reach_rows(*s)]).to_parquet(
        snap / "reach_data.parquet", index=False)
    pd.DataFrame([{"subject_id": s, "sex": "F", "date_of_birth": None} for s, _, _ in subjects]
                 ).to_parquet(snap / "subjects.parquet", index=False)
    # Only cohort 01's sheet records a contusion: the column is absent from 02's file.
    pd.DataFrame([{"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01",
                   "source_tab": "4_Contusion_Injury_Details", "record_no": 1,
                   "field": "Actual_kd", "value": "65"}]).to_parquet(
        snap / "animal_records.parquet", index=False)
    return snap


def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_each_cohort_has_its_own_folder_inside_its_project(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    m = ox.export(snapshot, out)
    for project, cohort in (("PROJA", "PROJA_01"), ("PROJA", "PROJA_02"), ("PROJAB", "PROJAB_01")):
        for kind in layout.REACH_KINDS:
            f = out / project / cohort / layout.file_name(kind, cohort)
            assert f.is_file(), f
            assert "%s/%s/%s" % (project, cohort, f.name) in m["files"]
    # Nothing per cohort is left at the top; the shared files stay there.
    assert not [p.name for p in out.glob("*.csv") if "PROJA_0" in p.name or "PROJAB_0" in p.name]
    assert (out / "ODC_reaches_DATA_DICTIONARY.csv").is_file()
    assert (out / "analysis_EXCLUSIONS.csv").is_file()


def test_a_longer_project_code_is_its_own_project_not_a_prefix_match(snapshot, tmp_path):
    # PROJAB starts with PROJA; it is its own project and must not be filed under it.
    assert layout.project_of("PROJAB_02") == "PROJAB" and layout.project_of("PROJA_02") == "PROJA"
    assert layout.project_of_label("ASPA_04_D") == "ASPA"
    out = tmp_path / "exports" / "current"
    ox.export(snapshot, out)
    combined = _read(layout.combined_file(out, "ODC_reaches", "PROJA"))
    assert set(combined["SubjectID"] if "SubjectID" in combined else combined.iloc[:, 0]) \
        .isdisjoint({"PROJAB_01_01"})
    assert layout.combined_file(out, "ODC_reaches", "PROJAB").is_file()


def test_the_combined_file_is_the_cohort_files_one_after_the_other(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    m = ox.export(snapshot, out)
    for kind in layout.REACH_KINDS:
        parts = [_read(out / "PROJA" / c / layout.file_name(kind, c)) for c in ("PROJA_01", "PROJA_02")]
        path = layout.combined_file(out, kind, "PROJA")
        combined = _read(path)
        assert path.name == layout.file_name(kind, "PROJA_all_cohorts")
        assert len(combined) == sum(len(p) for p in parts), kind
        assert m["files"][layout.key(out, path)]["rows"] == len(combined)
        assert m["files"][layout.key(out, path)]["cohorts"] == ["PROJA_01", "PROJA_02"]
        start = 0
        for part in parts:
            got = combined.iloc[start:start + len(part)][list(part.columns)].reset_index(drop=True)
            if layout.ENTRY_INDEX in part.columns:
                got = got.drop(columns=[layout.ENTRY_INDEX])
                part = part.drop(columns=[layout.ENTRY_INDEX])
            pd.testing.assert_frame_equal(got, part)   # every cell exactly as in its cohort's file
            start += len(part)
        if layout.ENTRY_INDEX in combined.columns:
            # counted again over the combined rows, as its dictionary entry says
            assert combined[layout.ENTRY_INDEX].tolist() == [str(i) for i in range(1, len(combined) + 1)]


def test_a_column_only_some_cohorts_have_is_filled_and_reported(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    m = ox.export(snapshot, out)
    path = layout.combined_file(out, "ODC_reaches", "PROJA")
    combined = _read(path)
    assert "Contusion_Actual_kd" in combined.columns
    assert "Contusion_Actual_kd" not in _read(out / "PROJA" / "PROJA_02" / "ODC_reaches_PROJA_02.csv").columns
    cohort2 = combined[combined["SubjectID"] == "PROJA_02_01"] if "SubjectID" in combined else combined.tail(4)
    assert (cohort2["Contusion_Actual_kd"] == "Not recorded").all()
    assert (combined != "").all().all()       # an ODC document holds no empty cell
    filled = m["files"][layout.key(out, path)]["columns_filled_for_cohorts_without_them"]
    assert "Contusion_Actual_kd" in filled["PROJA_02"]["columns"]


def test_a_one_cohort_export_still_combines_every_cohort(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    ox.export(snapshot, out)
    m = ox.export(snapshot, out, cohorts=["PROJA_02"])
    entry = m["files"][layout.key(out, layout.combined_file(out, "reaches_analysis", "PROJA"))]
    assert entry["cohorts"] == ["PROJA_01", "PROJA_02"]


def test_files_from_the_flat_layout_are_archived_not_deleted(snapshot, tmp_path):
    out = tmp_path / "exports" / "current"
    out.mkdir(parents=True)
    (out / "ODC_reaches_PROJA_01.csv").write_text("old")
    (out / "reaches_PROJA_01_analysis.csv").write_text("old")
    (out / "reach_data.csv").write_text("kept")            # not per cohort: stays
    ox.export(snapshot, out)
    assert not (out / "ODC_reaches_PROJA_01.csv").exists()
    assert not (out / "reaches_PROJA_01_analysis.csv").exists()
    assert (out / "reach_data.csv").read_text() == "kept"
    archived = sorted(p.name for p in (tmp_path / "exports" / "_archived").rglob("*.csv")) \
        if (tmp_path / "exports" / "_archived").exists() else \
        sorted(p.name for p in (tmp_path / "_archived").rglob("*.csv"))
    assert archived == ["ODC_reaches_PROJA_01.csv", "reaches_PROJA_01_analysis.csv"]


def test_a_reader_asks_where_a_file_is_instead_of_writing_the_path(snapshot, tmp_path):
    # Readers outside the exporter use locate(), so the next move of the files
    # does not break them the way the 2026-10-29 move broke a hand-written path.
    out = tmp_path / "exports" / "current"
    ox.export(snapshot, out)
    assert layout.locate("reaches_analysis", "PROJA_02", out) == \
        out / "PROJA" / "PROJA_02" / "reaches_PROJA_02_analysis.csv"
    assert layout.locate("reaches_analysis", "proja", out) == \
        layout.combined_file(out, "reaches_analysis", "PROJA")
    with pytest.raises(FileNotFoundError):
        layout.locate("reaches_analysis", "PROJA_09", out)


def test_locate_finds_a_cohort_whose_folder_carries_a_letter(tmp_path):
    f = tmp_path / "PROJA" / "PROJA_04_D" / "reaches_PROJA_04_D_analysis.csv"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    (tmp_path / "PROJA" / "PROJA_40").mkdir()              # a longer number is not a match
    assert layout.locate("reaches_analysis", "PROJA_04", tmp_path) == f
