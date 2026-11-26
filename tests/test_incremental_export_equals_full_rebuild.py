"""The nightly export rewrites only what changed -- and what it writes is exactly
what a full rebuild from the same inputs would write.

WHY: rebuilding every per-reach file whenever anything changed took hours a
night; the request was "only add new records, don't rebuild".
But records also CHANGE (a video analyzed again, a sheet corrected, a cohort's
phase labels released, an exclusion appearing), so keeping a file is only
safe if keeping it can never keep a wrong byte. This is the gate for that:
every case below runs one night on top of the previous night's files
(incremental) and once from nothing (full), from the same inputs, and the two
folders must be identical byte for byte -- every CSV, every dictionary, the
exclusions report and the combined files; MANIFEST.json is compared without
its times and its record of how the run went (incremental.comparable_manifest).
Row order IS part of the contract (the files are compared as bytes), because
the files are sorted and carry a running entry index.

Each case also checks that the run really was incremental where it should
be: a cohort the change does not touch is kept, not rewritten.
"""
import json

import pandas as pd
import pytest

from mousedb.exporters import current, export_exclusions, incremental, layout
from mousedb.exporters import odc_reaches as ox


# ------------------------------------------------------------------ the world

def _ext(i, extra=None):
    d = {"k_speed": 10 + i, "k_len": 1.5 * i, "k_count": i % 3}
    d.update(extra or {})
    return json.dumps(d)


def _video(subject, date, tray="P", run=1, segments=2, start=0, extra=None):
    cohort_digits = subject.split("_")
    video = "%s_%s%s%s_%s%d" % (date.replace("-", ""), cohort_digits[0], cohort_digits[1],
                                cohort_digits[2], tray, run)
    rows = []
    for seg in range(1, segments + 1):
        outcome = ("retrieved", "displaced_sa", "untouched")[(seg + start) % 3]
        for n in (1, 2):
            i = start + len(rows)
            rows.append({"subject_id": subject, "video_name": video, "session_date": date,
                         "tray_type": tray, "run_number": run, "segment_num": seg,
                         "reach_id": i + 1, "reach_num": n, "segment_outcome": outcome,
                         "causal_reach": n == 2, "outcome": outcome if n == 2 else None,
                         "outcome_source": "algo", "attention_score": 0.25 * (n + seg),
                         "start_frame": 10 + i,
                         "test_phase": "Pre-Injury_Test_Pillar", "phase_group": "Baseline",
                         "extended_features": _ext(i, extra)})
    return rows


def _pellets(subject, date, tray="P", run=1, scores=(2, 1, 0)):
    return [{"subject_id": subject, "session_date": date, "tray_type": tray,
             "tray_number": run, "pellet_number": i + 1, "score": s,
             "test_phase": "Pre-Injury_Test_Pillar", "phase_group": "Baseline",
             "contact_group": "contacted" if s else "missed", "entered_by": "excel_import",
             "entered_at": date} for i, s in enumerate(scores)]


class World:
    """Every input the per-reach export reads, as plain lists the cases edit."""

    def __init__(self, tmp_path):
        self.snap = tmp_path / "snap"
        self.snap.mkdir()
        self.reach = (_video("PROJA_01_01", "2025-07-10") + _video("PROJA_01_02", "2025-07-10")
                      + _video("PROJA_01_01", "2025-07-11", start=4)
                      + _video("PROJA_02_01", "2025-09-10")
                      + _video("PROJB_01_01", "2025-10-10", segments=3))
        self.pellets = (_pellets("PROJA_01_01", "2025-07-10") + _pellets("PROJA_01_02", "2025-07-10")
                        + _pellets("PROJA_01_01", "2025-07-11")
                        + _pellets("PROJA_02_01", "2025-09-10")
                        + _pellets("PROJB_01_01", "2025-10-10"))
        self.subjects = [{"subject_id": s, "sex": "F", "date_of_birth": "2025-01-01"}
                         for s in ("PROJA_01_01", "PROJA_01_02", "PROJA_02_01", "PROJB_01_01")]
        self.records = [
            {"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01",
             "source_tab": "4_Contusion_Injury_Details", "record_no": 1,
             "field": "Actual_kd", "value": "65"},
            {"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01",
             "source_tab": "4_Contusion_Injury_Details", "record_no": 1,
             "field": "Surgery_Date", "value": "2025-08-01"},
            {"subject_id": "PROJB_01_01", "cohort_id": "PROJB_01",
             "source_tab": "0a_Metadata", "record_no": 1, "field": "Sex", "value": "F"}]
        self.weights = [{"subject_id": "PROJA_01_01", "date": "2025-07-10",
                         "weight_grams": 21.5, "weight_percent": 0.9}]
        self.declared = {}                       # cohort -> Study_Status
        self.drop = {}                           # video -> reason (exclusions)
        self.write()

    def video_pellets(self):
        rows = []
        for (v, s, d, t, r), g in pd.DataFrame(self.reach).groupby(
                ["video_name", "subject_id", "session_date", "tray_type", "run_number"]):
            for seg, o in g.groupby("segment_num")["segment_outcome"].first().items():
                rows.append({"video_name": v, "subject_id": s, "session_date": d,
                             "tray_type": t, "run_number": r, "segment_num": seg, "outcome": o})
        return rows

    def write(self):
        pd.DataFrame(self.reach).to_parquet(self.snap / "reach_data.parquet", index=False)
        pd.DataFrame(self.pellets).to_parquet(self.snap / "pellet_scores.parquet", index=False)
        pd.DataFrame(self.subjects).to_parquet(self.snap / "subjects.parquet", index=False)
        pd.DataFrame(self.records).to_parquet(self.snap / "animal_records.parquet", index=False)
        pd.DataFrame(self.weights).to_parquet(self.snap / "weights.parquet", index=False)
        pd.DataFrame(self.video_pellets()).to_parquet(self.snap / "video_pellets.parquet",
                                                      index=False)


@pytest.fixture
def world(tmp_path, monkeypatch):
    pipeline = tmp_path / "pipeline"
    (pipeline / "pipeline_records").mkdir(parents=True)
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(pipeline))
    monkeypatch.setenv("MOUSEDB_COHORT_SHEETS", str(sheets))
    monkeypatch.setenv("MOUSEDB_STUDY_FACTS", str(tmp_path / "facts.json"))
    monkeypatch.setenv("MOUSEDB_FIELD_DEFINITIONS", str(tmp_path / "defs.json"))
    from mousedb import cohort_sheets
    monkeypatch.setattr(cohort_sheets, "available_aspa_cohorts", lambda *a, **k: [])
    w = World(tmp_path)
    w.tmp = tmp_path
    monkeypatch.setattr(ox, "_declared_study_status", lambda cohort: w.declared.get(cohort, ""))

    def compute(*_a, **_k):
        ex = export_exclusions.Exclusions()
        for v, why in w.drop.items():
            ex.drop(v, why)
        return ex
    monkeypatch.setattr(export_exclusions, "compute", compute)
    # The exclusion checks are replaced above, so their real inputs never
    # change here; the skip signature is given the replacement's state, as the
    # real one carries the real checks' inputs.
    real_sig = current.odc_reaches_signature
    monkeypatch.setattr(current, "odc_reaches_signature",
                        lambda snap: dict(real_sig(snap), drop=dict(w.drop),
                                          declared=dict(w.declared)))
    return w


def _night(w, out, **kw):
    m = current.refresh_current(w.snap, out, db_ok=False, lock_dir=w.tmp / "locks", **kw)
    assert not m.get("failures"), m.get("failures")
    return m


def _identical_to_full(w, inc_out, name):
    full = w.tmp / ("full_" + name) / "current"
    _night(w, full, rebuild_all=True)
    diffs = incremental.compare_trees(inc_out, full)
    assert diffs == [], diffs


def _rewritten(m):
    return set(m["incremental"]["cohorts_rewritten"])


def _kept(m):
    return set(m["incremental"]["cohorts_kept"])


ALL = {"PROJA_01", "PROJA_02", "PROJB_01"}


# --------------------------------------------------------------- the cases

def _new_video(w):
    w.reach += _video("PROJA_01_02", "2025-07-12", start=8)
    w.pellets += _pellets("PROJA_01_02", "2025-07-12")


def _new_tray_same_day(w):
    # A second tray on a day already exported: the day's total of every reach
    # of that animal that day changes, not only the new video's rows.
    w.reach += _video("PROJA_01_01", "2025-07-10", run=2, start=20)
    w.pellets += _pellets("PROJA_01_01", "2025-07-10", run=2)


def _changed_video(w):
    for r in w.reach:
        if r["video_name"] == "20250910_PROJA0201_P1" and r["segment_num"] == 1:
            r["attention_score"] = 0.99
            r["extended_features"] = _ext(99)


def _withdrawn_video(w):
    w.reach = [r for r in w.reach if r["video_name"] != "20250711_PROJA0101_P1"]


def _phase_relabel(w):
    for r in w.reach:
        if r["subject_id"].startswith("PROJA_02_"):
            r["test_phase"], r["phase_group"] = "Post-Injury_Test_1", "Post-Injury"


def _cohort_still_running(w):
    w.declared["PROJB_01"] = "in_progress"       # its phase labels are withheld


def _exclusion_change(w):
    w.drop["20250910_PROJA0201_P1"] = "a reason found tonight"


def _hand_score_corrected(w):
    for p in w.pellets:
        if p["subject_id"] == "PROJA_02_01" and p["pellet_number"] == 2:
            p["score"] = 2


def _new_measurement(w):
    for r in w.reach:
        if r["subject_id"] == "PROJB_01_01" and r["segment_num"] == 3:
            r["extended_features"] = _ext(r["reach_id"], {"k_angle": 0.5 * r["reach_num"]})


def _new_sheet_column(w):
    w.records.append({"subject_id": "PROJA_02_01", "cohort_id": "PROJA_02",
                      "source_tab": "4_Contusion_Injury_Details", "record_no": 1,
                      "field": "Contusion_Location", "value": "T9"})


def _weight_added(w):
    w.weights.append({"subject_id": "PROJB_01_01", "date": "2025-10-10",
                      "weight_grams": 20.0, "weight_percent": 88.0})


def _cohort_emptied(w):
    w.reach = [r for r in w.reach if not r["subject_id"].startswith("PROJB_")]


# (change, cohorts it must rewrite, cohorts it must keep)
CASES = {
    "new_video": (_new_video, {"PROJA_01"}, {"PROJA_02", "PROJB_01"}),
    "new_tray_same_day": (_new_tray_same_day, {"PROJA_01"}, {"PROJA_02", "PROJB_01"}),
    "changed_video": (_changed_video, {"PROJA_02"}, {"PROJA_01", "PROJB_01"}),
    "withdrawn_video": (_withdrawn_video, {"PROJA_01"}, {"PROJA_02", "PROJB_01"}),
    "phase_relabel": (_phase_relabel, {"PROJA_02"}, {"PROJA_01", "PROJB_01"}),
    "cohort_still_running": (_cohort_still_running, {"PROJB_01"}, {"PROJA_01", "PROJA_02"}),
    "exclusion_change": (_exclusion_change, {"PROJA_02"}, {"PROJA_01", "PROJB_01"}),
    "hand_score_corrected": (_hand_score_corrected, {"PROJA_02"}, {"PROJA_01", "PROJB_01"}),
    "new_measurement": (_new_measurement, {"PROJB_01"}, {"PROJA_01", "PROJA_02"}),
    "new_sheet_column": (_new_sheet_column, {"PROJA_02"}, {"PROJA_01", "PROJB_01"}),
    "weight_added": (_weight_added, {"PROJB_01"}, {"PROJA_01", "PROJA_02"}),
    "cohort_emptied": (_cohort_emptied, set(), {"PROJA_01", "PROJA_02"}),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_one_change_rewrites_only_its_cohort_and_matches_a_full_rebuild(world, case):
    change, rewrite, keep = CASES[case]
    out = world.tmp / "inc" / "current"
    first = _night(world, out)
    assert first["incremental"]["mode"] == "full"            # no ledger yet
    change(world)
    world.write()
    m = _night(world, out)
    assert m["incremental"]["mode"] == "incremental"
    assert _rewritten(m) == rewrite, m["incremental"]
    assert _kept(m) == keep, m["incremental"]
    _identical_to_full(world, out, case)


def test_the_manifest_says_which_videos_were_appended_replaced_and_removed(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    _new_video(world)
    _withdrawn_video(world)
    for r in world.reach:
        if r["video_name"] == "20250710_PROJA0102_P1":
            r["start_frame"] += 100
    world.write()
    rec = _night(world, out)["incremental"]["cohorts_rewritten"]["PROJA_01"]
    assert rec["videos_appended"] == ["20250712_PROJA0102_P1"]
    assert rec["videos_replaced"] == ["20250710_PROJA0102_P1"]
    assert rec["videos_removed"] == ["20250711_PROJA0101_P1"]
    assert rec["why"] == ["reaches", "hand_scores", "video_pellets"]


def test_a_column_type_change_rewrites_every_cohort_it_touches(world):
    """A whole-number column that gains one blank is written 3.0 instead of 3
    on EVERY row of every cohort: every cohort must be rewritten."""
    out = world.tmp / "inc" / "current"
    _night(world, out)
    world.reach[-1]["start_frame"] = None
    world.write()
    m = _night(world, out)
    assert _rewritten(m) == ALL
    _identical_to_full(world, out, "dtype")


def test_new_exporter_code_rebuilds_everything(world, monkeypatch):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    monkeypatch.setattr(current, "exporter_code_hash", lambda root=None: "new code")
    m = _night(world, out)
    assert m["incremental"]["mode"] == "full"
    assert "code changed" in m["incremental"]["why_full"]
    assert _rewritten(m) == ALL
    _identical_to_full(world, out, "code")
    # ... and the night after, with nothing new, is incremental again.
    _new_video(world)
    world.write()
    m = _night(world, out)
    assert m["incremental"]["mode"] == "incremental" and _rewritten(m) == {"PROJA_01"}


def test_two_nights_in_a_row(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    for i, change in enumerate((_new_video, _phase_relabel, _cohort_still_running,
                                _exclusion_change)):
        change(world)
        world.write()
        m = _night(world, out)
        assert m["incremental"]["mode"] == "incremental"
        _identical_to_full(world, out, "night%d" % i)


def test_an_unchanged_project_keeps_its_combined_files_untouched(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    combined = layout.combined_file(out, "ODC_reaches", "PROJB")
    before = incremental.stamp(combined)
    _new_video(world)
    world.write()
    m = _night(world, out)
    assert incremental.stamp(combined) == before
    assert layout.key(out, combined) in m["incremental"]["combined_kept"]
    assert layout.key(out, combined) in m["files"]


def test_a_combined_file_keeps_its_unchanged_front(world):
    """PROJA_02 is the last PROJA cohort: the combined PROJA file keeps
    PROJA_01's part as it was and writes only PROJA_02's."""
    out = world.tmp / "inc" / "current"
    _night(world, out)
    _changed_video(world)
    world.write()
    m = _night(world, out)
    name = layout.key(out, layout.combined_file(out, "ODC_reaches", "PROJA"))
    assert m["incremental"]["combined_front_kept"].get(name) == 1
    _identical_to_full(world, out, "front")


def test_a_file_changed_by_hand_is_rebuilt(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    edited = out / "PROJB" / "PROJB_01" / "reaches_PROJB_01_summary.csv"
    edited.write_text(edited.read_text() + "edited by hand\n")
    _new_video(world)                    # something must change, or nothing runs
    world.write()
    m = _night(world, out)
    assert _rewritten(m) == {"PROJA_01", "PROJB_01"}
    assert m["incremental"]["cohorts_rewritten"]["PROJB_01"]["why"] == [
        "its files on disk are not the ones the ledger recorded"]
    _identical_to_full(world, out, "edited")


def test_a_run_that_stops_before_its_ledger_is_saved_is_put_right(world, monkeypatch):
    """The files of the rebuilt cohort were replaced, the ledger was not: the
    old ledger's sizes and times no longer match those files, so the next run
    rebuilds that cohort instead of keeping it."""
    out = world.tmp / "inc" / "current"
    _night(world, out)
    _new_video(world)
    world.write()
    real = incremental.save_ledger

    def crash(*a, **k):
        raise RuntimeError("stopped here")
    monkeypatch.setattr(incremental, "save_ledger", crash)
    m = current.refresh_current(world.snap, out, db_ok=False, lock_dir=world.tmp / "locks")
    assert m["complete"] is False
    monkeypatch.setattr(incremental, "save_ledger", real)
    _phase_relabel(world)                # a further change the next night
    world.write()
    m = _night(world, out)
    assert {"PROJA_01", "PROJA_02"} <= _rewritten(m)
    _identical_to_full(world, out, "crash")


def test_no_ledger_means_a_full_rebuild(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    (out / incremental.LEDGER_NAME).unlink()
    _new_video(world)
    world.write()
    m = _night(world, out)
    assert m["incremental"]["mode"] == "full" and _rewritten(m) == ALL
    _identical_to_full(world, out, "noledger")


def test_the_limited_exports_still_accept_the_result(world):
    out = world.tmp / "inc" / "current"
    _night(world, out)
    _new_video(world)
    world.write()
    m = _night(world, out)
    written = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
    assert written["complete"] is True and m["complete"] is True
    assert written["incremental"]["at"]


def test_a_named_cohort_export_is_never_incremental(world, tmp_path):
    """mousedb export-odc-reaches --cohort X rebuilds what it names; it neither
    reads nor writes the ledger."""
    out = tmp_path / "named"
    m = ox.export(world.snap, out, cohorts=["PROJA_02"])
    assert "incremental" not in m
    assert not (out / incremental.LEDGER_NAME).exists()


def test_the_check_command_finds_them_identical(world, capsys):
    """mousedb export-incremental-check, as a person would run it on real data:
    last night's exports copied, tonight's snapshot, an empty scratch folder."""
    prev = world.tmp / "prev" / "current"
    _night(world, prev)
    _new_video(world)
    world.write()
    rc = incremental.main(["--snapshot-dir", str(world.snap), "--work", str(world.tmp / "work"),
                           "--previous", str(prev)])
    printed = capsys.readouterr().out
    assert rc == 0, printed
    assert "[OK] identical" in printed and "time: incremental" in printed
    assert "PROJA_01" in printed                  # the cohort it rewrote
    printed.encode("ascii")                       # terminal output stays ASCII


def test_the_check_command_reports_a_difference(world, capsys, monkeypatch):
    # A fingerprint that forgets the exclusions keeps a cohort it must rewrite;
    # in place on both nights, so the forgotten part never differs.
    real = incremental.cohort_fingerprints

    def forgetful(*a, **k):
        out = real(*a, **k)
        for fp in out.values():
            fp["parts"]["exclusions"] = "forgotten"
            fp["fingerprint"] = incremental._h(incremental._json(
                [fp["parts"][p] for p in incremental.PARTS]))
        return out
    monkeypatch.setattr(incremental, "cohort_fingerprints", forgetful)
    prev = world.tmp / "prev" / "current"
    _night(world, prev)
    _exclusion_change(world)
    world.write()
    rc = incremental.main(["--snapshot-dir", str(world.snap), "--work", str(world.tmp / "work"),
                           "--previous", str(prev)])
    printed = capsys.readouterr().out
    assert rc == 1 and "bytes differ" in printed and "analysis_EXCLUSIONS.csv" in printed


def test_the_check_command_refuses_a_used_work_folder(world, tmp_path):
    used = tmp_path / "used"
    used.mkdir()
    (used / "x").write_text("x")
    assert incremental.main(["--snapshot-dir", str(world.snap), "--work", str(used),
                             "--base-snapshot", str(world.snap)]) == 2


def test_video_processing_progress_says_what_the_night_did():
    from mousedb.data_status import incremental_summary
    assert incremental_summary(None) == ""
    line = incremental_summary({"mode": "incremental", "at": "T", "cohorts_rewritten": {"A_01": {}},
                                "cohorts_kept": {"B_01": "t", "C_01": "t"}})
    assert "A_01 rewritten" in line and "2 cohort(s) kept" in line
    assert "rebuilt from nothing" in incremental_summary(
        {"mode": "full", "at": "T", "why_full": "the exporter's code changed"})
