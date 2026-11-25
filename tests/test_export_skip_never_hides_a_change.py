"""The current exports skip rebuilding only when NOTHING they are built from
has changed -- and a skip can actually happen.

WHY: the per-reach ODC and analysis files are large, so a refresh whose
inputs are unchanged keeps them. The signature that decides this would otherwise
cover four snapshot tables by size and row count, plus a few settings. The
export also reads the per-pellet table, the weights, the pipeline's collage
names, the name-audit record and the sheets folder: a change to any of those
alone would have left last night's files -- including the list of entries
left out of the analysis files -- in place. Meanwhile an informational note
about the data dictionary was filed as a "problem", which stopped the
signature ever being saved, so every night rewrote everything anyway.
"""
import json
import sqlite3

import pandas as pd
import pytest

from mousedb.exporters import current
from mousedb.exporters import odc_reaches


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A snapshot with every input table, and every outside input in tmp."""
    snap = tmp_path / "snap"
    snap.mkdir()
    for name in current.ODC_INPUT_TABLES:
        pd.DataFrame({"subject_id": ["PROJ_01_01"], "v": [1]}).to_parquet(
            snap / ("%s.parquet" % name), index=False)
    pipeline = tmp_path / "pipeline"
    (pipeline / "pipeline_records").mkdir(parents=True)
    con = sqlite3.connect(pipeline / "watcher.db")
    con.execute("CREATE TABLE collages (filename TEXT)")
    con.execute("INSERT INTO collages VALUES ('20260101_PROJ0101,PROJ0102_P1.mkv')")
    con.commit()
    con.close()
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(pipeline))
    monkeypatch.setenv("MOUSEDB_COHORT_SHEETS", str(sheets))
    monkeypatch.setenv("MOUSEDB_STUDY_FACTS", str(tmp_path / "facts.json"))
    monkeypatch.setenv("MOUSEDB_FIELD_DEFINITIONS", str(tmp_path / "defs.json"))
    from mousedb import cohort_sheets
    monkeypatch.setattr(cohort_sheets, "available_aspa_cohorts", lambda *a, **k: [])
    return {"snap": snap, "pipeline": pipeline, "sheets": sheets, "tmp": tmp_path}


def _sig(w):
    return current.odc_reaches_signature(w["snap"])


@pytest.mark.parametrize("table", ["video_pellets", "weights", "reach_data",
                                   "subjects", "pellet_scores", "animal_records"])
def test_every_snapshot_table_the_export_reads_is_signed(world, table):
    before = _sig(world)
    # Same rows, same columns, a different value: size and row count alone
    # could not be relied on to see this.
    pd.DataFrame({"subject_id": ["PROJ_01_01"], "v": [2]}).to_parquet(
        world["snap"] / ("%s.parquet" % table), index=False)
    assert _sig(world) != before


def test_the_name_audit_record_is_signed(world):
    before = _sig(world)
    (world["pipeline"] / "pipeline_records" / "archive_name_conflicts.json").write_text(
        json.dumps({"conflicts": []}))
    assert _sig(world) != before


def test_the_collage_names_are_signed(world):
    before = _sig(world)
    con = sqlite3.connect(world["pipeline"] / "watcher.db")
    con.execute("INSERT INTO collages VALUES ('20260102_PROJ0101,PROJ0103_P1.mkv')")
    con.commit()
    con.close()
    assert _sig(world) != before


def test_the_sheets_folder_is_signed(world, monkeypatch):
    before = _sig(world)
    from mousedb import cohort_sheets
    monkeypatch.setattr(cohort_sheets, "available_cohorts", lambda *a, **k: ["PROJ_01"])
    assert _sig(world) != before
    monkeypatch.setenv("MOUSEDB_COHORT_SHEETS", str(world["tmp"] / "unreachable"))
    assert _sig(world)["exclusion_inputs"]["sheets"] == "unavailable"


def test_settings_that_decide_the_output_are_signed(world):
    before = _sig(world)
    (world["tmp"] / "defs.json").write_text(json.dumps({"tab::field": "a definition"}))
    after_defs = _sig(world)
    assert after_defs != before
    (world["tmp"] / "facts.json").write_text(json.dumps({"PROJ": {"StudyLeader": "x"}}))
    assert _sig(world) != after_defs


def test_unchanged_inputs_give_the_same_signature(world):
    assert _sig(world) == _sig(world)


# -- the skip itself ---------------------------------------------------------

def _fake_export(calls, problems=(), from_inputs=(), notes=(), failures=()):
    def export(snapshot_dir, out_dir, manifest=None, staging_dir=None, **_k):
        calls.append(1)
        (out_dir / "PROJ").mkdir(parents=True, exist_ok=True)
        (out_dir / "PROJ" / "ODC_reaches_PROJ_01.csv").write_text("a\n1\n")
        manifest["files"]["PROJ/ODC_reaches_PROJ_01.csv"] = {"rows": 1}
        manifest["problems"].extend(problems)
        manifest.setdefault("from_inputs", []).extend(from_inputs)
        manifest.setdefault("notes", []).extend(notes)
        if failures:
            manifest.setdefault("failures", []).extend(failures)
        return manifest
    return export


def _run(world, out):
    m = {"files": {}, "problems": [], "notes": []}
    current._odc_reaches_csvs(world["snap"], out, m)
    return m


def test_a_dictionary_note_does_not_stop_the_skip(world, monkeypatch, tmp_path):
    """The note says nothing is wrong; it must not keep every night rewriting."""
    calls = []
    note = "ODC_reaches_DATA_DICTIONARY.csv: 3 variable(s) are columns in only some cohorts' files"
    monkeypatch.setattr(odc_reaches, "export", _fake_export(calls, notes=[note]))
    out = tmp_path / "out"
    first = _run(world, out)
    assert note in first["notes"] and not first["problems"]
    second = _run(world, out)
    assert calls == [1]                                  # skipped the second time
    assert "odc_reaches_skipped" in second
    assert second["files"] == first["files"] and second["notes"] == [note]


def test_a_check_that_could_not_run_is_carried_over_on_a_skip(world, monkeypatch, tmp_path):
    calls = []
    msg = "analysis exclusions: archive-name check not run: no name-audit record"
    monkeypatch.setattr(odc_reaches, "export",
                        _fake_export(calls, problems=[msg], from_inputs=[msg]))
    out = tmp_path / "out"
    _run(world, out)
    second = _run(world, out)
    assert calls == [1] and msg in second["problems"]
    # ... and the record appearing is a change: the export runs again.
    (world["pipeline"] / "pipeline_records" / "archive_name_conflicts.json").write_text("{}")
    _run(world, out)
    assert calls == [1, 1]


def test_a_failed_run_is_never_skipped_next_time(world, monkeypatch, tmp_path):
    calls = []
    out = tmp_path / "out"
    monkeypatch.setattr(odc_reaches, "export", _fake_export(calls))
    _run(world, out)                                     # a good run saves a signature
    assert (out / ".odc_reaches_signature.json").exists()
    pd.DataFrame({"subject_id": ["PROJ_01_01"], "v": [3]}).to_parquet(
        world["snap"] / "weights.parquet", index=False)
    msg = "ODC_reaches_PROJ_01: KeyError: 'subject_id'"
    monkeypatch.setattr(odc_reaches, "export",
                        _fake_export(calls, problems=[msg], failures=[msg]))
    _run(world, out)
    assert not (out / ".odc_reaches_signature.json").exists()
    _run(world, out)                                     # rebuilt, not skipped
    assert calls == [1, 1, 1]
