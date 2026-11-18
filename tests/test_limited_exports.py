"""A limited export is a filtered copy of the current exports, never a recomputation.

WHY: people need slices of the full export (some cohorts, the last sessions
before the injury, one tray type, a few columns, a yes/no column per animal).
Made by hand each time, a slice had no record of what it was cut from, and it
could drift from the full export. These tests hold the rules the slice keeps:
every value is copied unchanged; the sessions come from the sheet's records of
the same snapshot generation as the files; a column that does not exist is
refused by name, never dropped; the old slice is archived, never deleted; the
definition lives in the lab settings file, never in the repository.
"""
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from mousedb import snapshot_generation
from mousedb.exporters import layout, subsets

# Cohort PROJA_01: animals 01, 02; first injury 2026-03-10 (animal 02's date is later).
# Cohort PROJA_02: animal 01; injury 2026-04-10.
# Letter cohort PROJB_04 (folder PROJB_04_D): its analysis files show animal D01.
SESSIONS = [  # (subject, date, tray type) the sheet records hand scores for
    ("PROJA_01_01", "2026-03-02", "P"), ("PROJA_01_01", "2026-03-03", "P"),
    ("PROJA_01_01", "2026-03-04", "P"), ("PROJA_01_01", "2026-03-05", "E"),
    ("PROJA_01_01", "2026-03-11", "P"),
    ("PROJA_01_02", "2026-03-03", "P"), ("PROJA_01_02", "2026-03-04", "P"),
    ("PROJA_02_01", "2026-04-01", "P"), ("PROJA_02_01", "2026-04-02", "P"),
    ("PROJB_04_01", "2020-01-02", "P"), ("PROJB_04_01", "2020-01-03", "P"),
]
SURGERIES = [("PROJA_01_01", "contusion", "2026-03-10"), ("PROJA_01_02", "contusion", "2026-03-12"),
             ("PROJA_01_01", "tracing", "2026-03-01"), ("PROJA_02_01", "contusion", "2026-04-10"),
             ("PROJB_04_01", "contusion", "2020-01-10")]


def _video(subject, date, tray, n=1):
    proj, c, s = subject.split("_")
    return "%s_%s%s%s_%s%d" % (date.replace("-", ""), proj, c, s, tray, n)


def _lab_id(subject):
    """How a letter cohort's analysis files show an animal (PROJB_04_01 -> D01)."""
    return "D" + subject[-2:] if subject.startswith("PROJB") else subject


def _write_snapshot(snap: Path, generation_holder: dict) -> None:
    staging = snap / "_staging"
    staging.mkdir(parents=True)
    pd.DataFrame([{"subject_id": s, "session_date": d, "tray_type": t, "tray_number": 1,
                   "pellet_number": 1, "score": 2} for s, d, t in SESSIONS]
                 ).to_parquet(staging / "pellet_scores.parquet", index=False)
    pd.DataFrame([{"subject_id": s, "surgery_type": t, "surgery_date": d} for s, t, d in SURGERIES]
                 ).to_parquet(staging / "surgeries.parquet", index=False)
    stamp = snapshot_generation.publish(snap, staging, {"pellet_scores": len(SESSIONS),
                                                        "surgeries": len(SURGERIES)},
                                        swap=lambda a, b: os.replace(a, b), log=lambda m: None)
    generation_holder["generation"] = stamp["generation"]


def _cohort_rows(cohort):
    return [(s, d, t) for s, d, t in SESSIONS if s.startswith(cohort)]


def _write_current(cur: Path, generation: str) -> None:
    """A full export as exporters.current writes it: per-cohort files of every
    kind, the dictionaries, the exclusions, MANIFEST.json."""
    for cohort, label in (("PROJA_01", "PROJA_01"), ("PROJA_02", "PROJA_02"), ("PROJB_04", "PROJB_04_D")):
        odc, summ, ana, ses, odcs = [], [], [], [], []
        for s, d, t in _cohort_rows(cohort):
            v = _video(s, d, t)
            for r in (1, 2):
                base = {"video_name": v, "session_date": d, "tray_type": t, "segment_num": "1",
                        "reach_num": str(r), "start_frame": str(100 * r), "end_frame": str(100 * r + 9)}
                o = dict(base, subject_id=s, SubjectID=_lab_id(s), outcome="retrieved")
                if cohort == "PROJA_01":
                    o["Contusion_Actual_kd"] = "61"           # a column only this cohort has
                odc.append(o)
                summ.append({"subject": _lab_id(s), "session_date": d, "tray_type": t,
                             "session_video": v, "reach_result": "missed"})
                ana.append({"entry_index": "0", "subject_id": _lab_id(s), "test_date": d,
                            "tray_type": t, "video_name": v, "swipe_length": "1.5"})
            ses.append({"entry_index": "0", "subject_id": _lab_id(s), "test_date": d,
                        "tray_type": t, "video_name": v, "manual_retrieved": "6",
                        "pellets_hand_scored": "20" if s != "PROJA_01_02" else "10"})
            odcs.append({"Animal": s, "Date": d, "Tray1_Type": t, "Tray2_Type": "",
                         "Daily_Retrieved": "3"})
        for kind, rows in (("ODC_reaches", odc), ("reaches_summary", summ),
                           ("reaches_analysis", ana), ("sessions_analysis", ses),
                           ("ODC_sessions", odcs)):
            f = layout.cohort_file(cur, kind, cohort, label)
            f.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_csv(f, index=False)
    for kind, cols in (("ODC_reaches", ["subject_id", "SubjectID", "video_name", "session_date",
                                        "tray_type", "segment_num", "reach_num", "start_frame",
                                        "end_frame", "outcome", "Contusion_Actual_kd"]),
                       ("reaches_summary", ["subject", "session_date", "tray_type",
                                            "session_video", "reach_result"]),
                       ("reaches_analysis", ["entry_index", "subject_id", "test_date",
                                             "tray_type", "video_name", "swipe_length"]),
                       ("sessions_analysis", ["entry_index", "subject_id", "test_date", "tray_type",
                                              "video_name", "manual_retrieved",
                                              "pellets_hand_scored"]),
                       ("ODC_sessions", ["Animal", "Date", "Tray1_Type", "Tray2_Type",
                                         "Daily_Retrieved"])):
        pd.DataFrame([{"VariableName": c, "Title": c, "Description": "about " + c,
                       "Unit_of_Measure": "", "DataType": "text", "PermittedValues": "",
                       "PVDescribed": "", "MinimumValue": "", "MaximumValue": "", "Comments": ""}
                      for c in cols]).to_csv(cur / ("%s_DATA_DICTIONARY.csv" % kind), index=False)
    pd.DataFrame([
        {"cohort": "PROJA_01", "left_out": "reach rows", "video_name": _video("PROJA_01_01", "2026-03-04", "P"),
         "subject_id": "PROJA_01_01", "date": "2026-03-04", "rows": "2", "fields": "", "reason": "kept: in window"},
        {"cohort": "PROJA_01", "left_out": "reach rows", "video_name": _video("PROJA_01_01", "2026-03-11", "P"),
         "subject_id": "PROJA_01_01", "date": "2026-03-11", "rows": "2", "fields": "", "reason": "after injury"},
        {"cohort": "PROJA_01", "left_out": "columns", "video_name": "", "subject_id": "",
         "date": "", "rows": "", "fields": "x", "reason": "whole cohort"},
        {"cohort": "PROJA_02", "left_out": "reach rows", "video_name": _video("PROJA_02_01", "2026-04-01", "P"),
         "subject_id": "PROJA_02_01", "date": "2026-04-01", "rows": "2", "fields": "", "reason": "other cohort"},
    ]).to_csv(cur / "analysis_EXCLUSIONS.csv", index=False)
    (cur / "MANIFEST.json").write_text(json.dumps(
        {"generated_at": "2026-05-01T00:05:00", "snapshot_generation": generation,
         "complete": True, "files": {}}), encoding="utf-8")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    cfg = tmp_path / "config" / "mousedb_lab_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"grants": {"GrantA": ["PROJA"]}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(cfg))
    snap = tmp_path / "snapshot"
    holder = {}
    _write_snapshot(snap, holder)
    cur = tmp_path / "exports" / "current"
    _write_current(cur, holder["generation"])
    out = tmp_path / "exports" / "subsets"
    locks = tmp_path / "locks"
    locks.mkdir()

    def make(name="pre", profile=None, **kw):
        kw.setdefault("rules", None)
        kw.setdefault("out_root", out)
        return subsets.make(name, profile, current=cur, snapshot_dir=snap,
                            lock_dir=locks, **kw)
    return {"root": tmp_path, "cfg": cfg, "snap": snap, "cur": cur, "out": out, "make": make,
            "locks": locks, "generation": holder["generation"]}


LAST2_PILLAR = {"cohorts": ["PROJA_01", "PROJA_02", "PROJB_04"], "tray_types": ["Pillar"],
                "window": {"type": "last_before_injury", "n": 2, "tray_type": "P"}}


def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def test_window_is_each_animals_last_sessions_before_the_cohorts_first_injury(lab):
    r = lab["make"]("pre", LAST2_PILLAR)
    folder = Path(r["folder"])
    odc = _read(folder / "ODC_reaches_pre.csv")
    got = sorted(set(zip(odc.subject_id, odc.session_date)))
    # PROJA_01's first injury is 2026-03-10 (animal 01's), so animal 02's later date
    # does not move it; 03-05 is an easy tray, 03-11 is after the injury.
    assert got == [("PROJA_01_01", "2026-03-03"), ("PROJA_01_01", "2026-03-04"),
                   ("PROJA_01_02", "2026-03-03"), ("PROJA_01_02", "2026-03-04"),
                   ("PROJA_02_01", "2026-04-01"), ("PROJA_02_01", "2026-04-02"),
                   ("PROJB_04_01", "2020-01-02"), ("PROJB_04_01", "2020-01-03")]
    # A letter cohort's decoded animal (D01) is matched through its video name.
    ana = _read(folder / "reaches_pre_analysis.csv")
    assert len(ana) == len(odc) and "D01" in set(ana.subject_id)
    m = json.loads((folder / "SUBSET_MANIFEST.json").read_text())
    assert m["session_window"]["first_injury_date"]["PROJA_01"] == "2026-03-10"
    assert m["source_snapshot_generation"] == lab["generation"]
    assert m["session_window"]["sessions_chosen"]["PROJA_01_01"] == ["2026-03-03", "2026-03-04"]
    assert m["files"]["ODC_reaches_pre.csv"]["rows"] == len(odc) == 16


def test_values_are_copied_unchanged_and_entry_index_is_counted_again(lab):
    folder = Path(lab["make"]("pre", LAST2_PILLAR)["folder"])
    src = _read(layout.locate("ODC_reaches", "PROJA_01", lab["cur"]))
    out = _read(folder / "ODC_reaches_pre.csv")
    mine = out[out.subject_id.str.startswith("PROJA_01")]
    joined = mine.merge(src, on=["video_name", "reach_num"], suffixes=("", "_src"))
    for c in src.columns:
        if c not in ("video_name", "reach_num"):
            assert (joined[c] == joined[c + "_src"]).all(), c
    ana = _read(folder / "reaches_pre_analysis.csv")
    assert list(ana.entry_index) == [str(i) for i in range(1, len(ana) + 1)]


def test_a_column_only_some_cohorts_have_is_filled_and_said(lab):
    folder = Path(lab["make"]("pre", LAST2_PILLAR)["folder"])
    odc = _read(folder / "ODC_reaches_pre.csv")
    assert set(odc[odc.subject_id.str.startswith("PROJA_02")].Contusion_Actual_kd) == {"Not recorded"}
    m = json.loads((folder / "SUBSET_MANIFEST.json").read_text())
    filled = m["files"]["ODC_reaches_pre.csv"]["columns_filled_for_cohorts_without_them"]
    assert filled["PROJA_02"]["columns"] == ["Contusion_Actual_kd"]


def test_a_column_the_full_export_lacks_is_refused_by_name_and_nothing_is_written(lab):
    bad = dict(LAST2_PILLAR, columns={"ODC_reaches": ["subject_id", "no_such_column"]})
    with pytest.raises(subsets.SubsetRefused, match="no_such_column"):
        lab["make"]("pre", bad)
    assert not (lab["out"] / "pre").exists()
    status = subsets.read_status(lab["out"])
    assert status["pre"]["ok"] is False and "no_such_column" in status["pre"]["result"]


def test_columns_and_dictionaries_are_cut_to_what_is_kept(lab):
    p = dict(LAST2_PILLAR, kinds=["ODC_reaches", "sessions_analysis"],
             columns={"ODC_reaches": ["video_name", "start_frame", "end_frame"]},
             flag={"column": "used_for_x", "rule": None})
    folder = Path(lab["make"]("pre", p)["folder"])
    assert not (folder / "reaches_pre_summary.csv").exists()
    odc = _read(folder / "ODC_reaches_pre.csv")
    assert list(odc.columns) == ["video_name", "start_frame", "end_frame", "used_for_x"]
    assert set(odc.used_for_x) == {"Not recorded"}                 # no rule: blank
    d = _read(folder / "ODC_reaches_DATA_DICTIONARY.csv")
    assert list(d.VariableName) == ["video_name", "start_frame", "end_frame", "used_for_x"]
    assert "no rule has been set" in d.Description.iloc[-1]
    s = _read(folder / "sessions_pre_analysis.csv")
    assert set(s.used_for_x) == {"NA"}


def test_exclusions_are_filtered_the_same_way(lab):
    p = dict(LAST2_PILLAR, cohorts=["PROJA_01"])
    folder = Path(lab["make"]("pre", p)["folder"])
    ex = _read(folder / "analysis_EXCLUSIONS.csv")
    assert sorted(ex.reason) == ["kept: in window", "whole cohort"]


def test_flag_rules_read_the_slice_and_never_change_it(lab):
    rules = {"listed_one": {"type": "listed", "animals": ["PROJA_01_02"]},
             "half": {"type": "share_at_least", "kind": "sessions_analysis",
                      "numerator": "manual_retrieved", "denominator": "pellets_hand_scored",
                      "percent": 50}}
    p = dict(LAST2_PILLAR, cohorts=["PROJA_01"], flag={"column": "chosen", "rule": "listed_one"})
    folder = Path(lab["make"]("a", p, rules=rules)["folder"])
    odc = _read(folder / "ODC_reaches_a.csv")
    assert dict(odc.groupby("subject_id").chosen.first()) == {"PROJA_01_01": "no", "PROJA_01_02": "yes"}
    p["flag"] = {"column": "chosen", "rule": "half"}
    folder = Path(lab["make"]("b", p, rules=rules)["folder"])
    odc = _read(folder / "ODC_reaches_b.csv")
    # 6 of 20 retrieved -> 30 %: no; 6 of 10 -> 60 %: yes.
    assert dict(odc.groupby("subject_id").chosen.first()) == {"PROJA_01_01": "no", "PROJA_01_02": "yes"}
    assert "at least 50 percent" in _read(folder / "ODC_reaches_DATA_DICTIONARY.csv").Description.iloc[-1]


def test_an_odc_session_day_is_kept_when_any_of_its_trays_is_chosen(lab):
    p = {"cohorts": ["PROJA_01"], "tray_types": ["E"], "kinds": ["ODC_sessions", "ODC_reaches"]}
    folder = Path(lab["make"]("easy", p)["folder"])
    days = _read(folder / "ODC_sessions_easy.csv")
    assert list(days.Date) == ["2026-03-05"]
    assert set(_read(folder / "ODC_reaches_easy.csv").tray_type) == {"E"}


def test_a_remake_archives_the_previous_slice_never_deletes_it(lab):
    first = Path(lab["make"]("pre", LAST2_PILLAR)["folder"])
    marker = (first / "SUBSET_MANIFEST.json").read_text()
    r = lab["make"]("pre", LAST2_PILLAR)
    assert r["archived"]
    arch = lab["root"] / "_archived" / "exports_subsets" / "pre"
    kept = list(arch.glob("*/SUBSET_MANIFEST.json"))
    assert len(kept) == 1 and kept[0].read_text() == marker
    assert (first / "ODC_reaches_pre.csv").is_file()


def test_unchanged_inputs_are_not_remade_when_asked(lab):
    lab["make"]("pre", LAST2_PILLAR)
    r = lab["make"]("pre", LAST2_PILLAR, if_changed=True)
    assert r["status"] == "unchanged" and not r["archived"]
    # A value that changed inside the slice makes it again.
    f = layout.locate("ODC_reaches", "PROJA_02", lab["cur"])
    f.write_text(f.read_text().replace("retrieved", "displaced", 1))
    r = lab["make"]("pre", LAST2_PILLAR, if_changed=True)
    assert r["status"] == "written" and r["archived"]
    # A person's own remake always writes.
    assert lab["make"]("pre", LAST2_PILLAR)["status"] == "written"


def _archived_versions(lab):
    arch = lab["root"] / "_archived" / "exports_subsets" / "pre"
    return sorted(arch.iterdir()) if arch.is_dir() else []


def test_a_source_rewritten_with_the_same_content_does_not_remake_it(lab):
    """The full export rewrites many of its files every night with the same
    content: new file times, same bytes. That must not archive an identical copy."""
    lab["make"]("pre", LAST2_PILLAR)
    f = layout.locate("ODC_reaches", "PROJA_01", lab["cur"])
    data = f.read_bytes()
    f.unlink()
    f.write_bytes(data)
    os.utime(f, (1_900_000_000, 1_900_000_000))
    before = _archived_versions(lab)
    r = lab["make"]("pre", LAST2_PILLAR, if_changed=True)
    assert r["status"] == "unchanged" and not r["archived"]
    assert _archived_versions(lab) == before
    # Checked once by content, the next check takes the fast path.
    assert lab["make"]("pre", LAST2_PILLAR, if_changed=True)["status"] == "unchanged"


def test_a_row_outside_the_slice_does_not_remake_it(lab):
    lab["make"]("pre", LAST2_PILLAR)
    f = layout.locate("ODC_reaches", "PROJA_01", lab["cur"])
    df = _read(f)
    extra = df.iloc[[0]].copy()
    extra["session_date"] = "2026-03-20"                          # after the injury
    extra["video_name"] = _video("PROJA_01_01", "2026-03-20", "P")
    pd.concat([df, extra]).to_csv(f, index=False)
    before = _archived_versions(lab)
    r = lab["make"]("pre", LAST2_PILLAR, if_changed=True)
    assert r["status"] == "unchanged" and _archived_versions(lab) == before


def test_sessions_from_another_snapshot_generation_are_refused(lab):
    m = json.loads((lab["cur"] / "MANIFEST.json").read_text())
    m["snapshot_generation"] = "20200101T000000-older"
    (lab["cur"] / "MANIFEST.json").write_text(json.dumps(m))
    with pytest.raises(subsets.SubsetRefused, match="different night"):
        lab["make"]("pre", LAST2_PILLAR)
    assert not (lab["out"] / "pre").exists()


def test_a_dry_run_writes_nothing_into_the_exports_or_the_lock_folder(lab):
    r = lab["make"]("pre", LAST2_PILLAR, dry_run=True, out_root=None)
    assert r["status"] == "dry run" and r["manifest"]["files"]["ODC_reaches_pre.csv"]["rows"] == 16
    assert not lab["out"].exists()
    assert list(lab["locks"].iterdir()) == []


def test_definitions_live_in_the_lab_settings_file_beside_its_other_settings(lab):
    subsets.save_profile("pre", LAST2_PILLAR)
    data = json.loads(lab["cfg"].read_text())
    assert data["grants"] == {"GrantA": ["PROJA"]}                  # kept
    saved = data["export_subsets"]["pre"]
    assert saved["tray_types"] == ["P"] and saved["window"]["n"] == 2
    assert not (Path.home() / ".mousedb" / "config.json").exists()
    r = lab["make"]("pre")                                          # made from the saved one
    assert r["status"] == "written"
    subsets.remove_profile("pre")
    assert "pre" not in subsets.profiles()
    assert (lab["out"] / "pre" / "ODC_reaches_pre.csv").is_file()  # its files stay


def test_saving_a_definition_does_not_change_what_the_full_export_is_built_from(lab):
    from mousedb import grants, sheet_projects
    before = (grants.grants(), sheet_projects.definitions())
    subsets.save_profile("pre", LAST2_PILLAR)
    assert (grants.grants(), sheet_projects.definitions()) == before


@pytest.mark.parametrize("bad, words", [
    ({"cohorts": ["proja-1"]}, "not a cohort id"),
    ({"cohorts": ["PROJA_01"], "tray_types": ["Ladder"]}, "unknown tray type"),
    ({"cohorts": ["PROJA_01"], "window": {"type": "last_before_injury", "n": 0, "tray_type": "P"}},
     "how many sessions"),
    ({"cohorts": ["PROJA_01"], "kinds": ["nonsense"]}, "unknown kind"),
    ({"cohorts": ["PROJA_01"], "flag": {"column": "x", "rule": "nobody"}}, "no yes/no rule"),
])
def test_a_bad_definition_is_refused_with_the_reason(lab, bad, words):
    with pytest.raises(subsets.SubsetRefused, match=words):
        subsets.validate("x", bad, rules={})


def test_a_flag_column_that_already_exists_is_refused(lab):
    p = dict(LAST2_PILLAR, flag={"column": "outcome", "rule": None})
    with pytest.raises(subsets.SubsetRefused, match="already a column"):
        lab["make"]("pre", p)


def test_the_program_reports_a_refusal_with_its_own_exit_code(lab, capsys):
    code = subsets.main(["pre", "--run", "--cohorts", "PROJA_01", "--columns",
                         "ODC_reaches=ghost_column", "--current-dir", str(lab["cur"]),
                         "--out-dir", str(lab["out"]), "--snapshot-dir", str(lab["snap"]),
                         "--dry-run"])
    assert code == subsets.EXIT_REFUSED
    assert "ghost_column" in capsys.readouterr().out


CSV = "a,b" + chr(10) + "1,2" + chr(10)


def test_the_analysis_mirror_leaves_the_subsets_folder_alone(tmp_path, monkeypatch):
    """import-analyses is the one job that archives things out of exports/. A
    slice folder is not an analysis (it never holds a registry.json), so the
    mirror must neither archive it nor report it, before or after a run in
    which an analysis is mirrored and one is withdrawn."""
    from mousedb import import_analyses as ia
    reg = tmp_path / "pipeline" / "Registry"
    analysis = reg / "exports" / "Alpha"
    analysis.mkdir(parents=True)
    (analysis / "registry.json").write_text(json.dumps({
        "analysis_name": "Alpha", "approved_method_hash": "h", "entries": {
            "S1": {"is_current": True, "method_hash": "h"}}}))
    (analysis / "m.csv").write_text(CSV)
    db = tmp_path / "db"
    monkeypatch.setenv("MOUSEDB_ROOT", str(db))
    monkeypatch.setenv("MOUSEBRAIN_PIPELINE_ROOT", str(tmp_path / "pipeline"))
    slice_dir = db / "exports" / "subsets" / "pre"
    slice_dir.mkdir(parents=True)
    (slice_dir / "ODC_reaches_pre.csv").write_text(CSV)
    (slice_dir / subsets.MANIFEST_NAME).write_text("{}")
    (db / "exports" / "subsets" / subsets.STATUS_NAME).write_text("{}")
    for _ in range(2):
        res = ia.run(log=lambda *a: None)
        assert res.errors == 0
    assert (slice_dir / "ODC_reaches_pre.csv").read_text() == CSV
    assert not (db / "_archived").exists() or not any((db / "_archived").rglob("ODC_reaches_pre.csv"))
    m = json.loads((db / "exports" / "ANALYSES_MANIFEST.json").read_text())
    assert [r["analysis_name"] for r in m["analyses"]] == ["Alpha"]
    assert "subsets" not in json.dumps(m.get("problems", []))


def test_a_definition_file_is_checked_without_being_saved(lab, tmp_path):
    f = tmp_path / "def.json"
    f.write_text(json.dumps(LAST2_PILLAR))
    code = subsets.main(["pre", "--dry-run", "--definition-file", str(f),
                         "--current-dir", str(lab["cur"]), "--snapshot-dir", str(lab["snap"])])
    assert code == subsets.EXIT_OK
    assert "pre" not in subsets.profiles()
    assert not lab["out"].exists()


def test_a_chosen_session_without_rows_is_listed(lab):
    f = layout.locate("ODC_reaches", "PROJA_02", lab["cur"])
    df = _read(f)
    df[df.session_date != "2026-04-02"].to_csv(f, index=False)    # no rows for that day
    folder = Path(lab["make"]("pre", LAST2_PILLAR)["folder"])
    m = json.loads((folder / "SUBSET_MANIFEST.json").read_text())
    assert m["files"]["ODC_reaches_pre.csv"]["chosen_sessions_with_no_rows"] == [
        "PROJA_02_01 2026-04-02"]
    assert m["files"]["reaches_pre_summary.csv"]["chosen_sessions_with_no_rows"] == []
    assert "1 chosen animal-day(s) have no rows" in (folder / "README.txt").read_text()


def _set_manifest(lab, **fields):
    m = json.loads((lab["cur"] / "MANIFEST.json").read_text())
    m.update(fields)
    (lab["cur"] / "MANIFEST.json").write_text(json.dumps(m))


@pytest.mark.parametrize("profile", [LAST2_PILLAR, {"cohorts": ["PROJA_01"]}])
def test_an_incomplete_full_export_is_refused_by_the_button_and_the_command(
        lab, capsys, monkeypatch, profile):
    """A part of the full export failed: its files are an earlier run's while the
    manifest carries the new generation. A slice of it would mix two runs."""
    monkeypatch.setenv("MOUSEDB_ROOT", str(lab["root"] / "db"))     # the command's lock folder
    _set_manifest(lab, complete=False, problems=["_odc_reaches_csvs: OSError: share full"])
    with pytest.raises(subsets.SubsetRefused, match="not complete"):
        lab["make"]("pre", profile)
    assert not (lab["out"] / "pre").exists()
    assert subsets.read_status(lab["out"])["pre"]["ok"] is False
    # The command (what the window's "Save and make now" runs) says so too.
    f = lab["root"] / "def.json"
    f.write_text(json.dumps(profile))
    code = subsets.main(["pre", "--run", "--definition-file", str(f),
                         "--current-dir", str(lab["cur"]), "--out-dir", str(lab["out"]),
                         "--snapshot-dir", str(lab["snap"])])
    assert code == subsets.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "not complete" in out and "share full" in out
    assert not (lab["out"] / "pre").exists()
