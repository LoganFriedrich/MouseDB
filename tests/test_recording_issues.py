"""A session whose recording FAILED is reported as "recording failed", never
"video missing", is recorded in the database, and its session row in the
exports says why it has no kinematics -- with its hand scores kept.

WHY: the reaching pipeline now sets aside a recording that is empty or a
fraction of a second long (a "recording issue") instead of cutting it. Without
this, the hand-scored sessions of such a recording would be listed as videos
to go looking for, and the exports would carry hand scores with no word about
why the reaches are absent. Every input here is synthetic, under tmp_path.
"""
import csv
import datetime as dt
import json
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from mousedb import attention, missing_videos as mv, recording_issues as ri

VIEW = "2026-03-20T02:00:00"
REC = "20260302_PROJA0101,PROJA0102,PROJA0001,PROJA0002,PROJA0003,PROJA0004,PROJA0005,PROJA0006_P2.mkv"
REASON = "recording issue: 4 frames, 0.08 s -- the recording failed"
KEYS = ["20260302_PROJA0101_P2", "20260302_PROJA0102_P2"]


def _hand(root, sessions, seen=("PROJA_01",)):
    d = Path(root) / "exports" / "hand_scores"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "hand_pellet_scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video_key", "pellet", "score", "subject_id", "cohort_id", "project",
                    "session_date", "tray"])
        for sid, day, tray, cohort in sessions:
            key = "%s_%s_%s" % (day.replace("-", ""), sid.replace("_", ""), tray)
            for p in (1, 2):
                w.writerow([key, p, 0, sid, cohort, cohort.split("_")[0], day, tray])
    (d / "hand_pellet_scores.json").write_text(json.dumps({"sheets_seen": list(seen)}))


def _census_dict(known=(), issues=True):
    c = {"generated_at": VIEW,
         "sessions": {n: {"element": "analyzed", "finished": True} for n in known}}
    if issues:
        for k in KEYS:
            c["sessions"][k] = {"element": "recording_issue", "finished": False,
                                "recording_issue": {"collage": REC, "reason": REASON}}
        c["recording_issues"] = [{"collage": REC, "reason": REASON,
                                  "flagged_by": "intake check on NODE-A",
                                  "flagged_at": "2026-03-02T16:00:00", "sessions": KEYS}]
    return c


def _census(root, **kw):
    d = Path(root) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pipeline_census.json").write_text(json.dumps(_census_dict(**kw)))


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "db"
    r.mkdir()
    monkeypatch.setenv("MOUSEDB_ROOT", str(r))
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path / "pipe"))
    return r


# ------------------------------------------------ "recording failed", not "missing"

def test_a_failed_recording_is_recording_failed_not_missing(root):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P2", "PROJA_01")])
    _census(root)
    gaps = {g["video_key"]: g for g in mv.check()["gaps"]}
    assert gaps["20260302_PROJA0101_P2"]["status"] == "recording_failed"
    assert gaps["20260302_PROJA0101_P2"]["hand_scored"] == "yes"
    assert gaps["20260302_PROJA0102_P2"]["status"] == "recording_failed"   # not scored
    assert gaps["20260302_PROJA0101_P2"]["recording_issue_reason"] == REASON
    assert not [g for g in gaps.values() if g["status"] == "missing"]
    # and nothing tells a person to go looking for it on the recording PC
    found = [i for i in attention.items() if i["source"] == mv.SOURCE]
    assert [i["kind"] for i in found] == [mv.KIND_FAILED]
    assert found[0]["says"].startswith("recording failed: %s, %s" % (REC, REASON))
    assert "OBS folder" not in found[0]["says"]


def test_without_the_census_list_the_same_session_would_be_missing(root):
    """The failing-before case: the census of a pipeline that did not report
    recording issues dropped the set-aside recording's sessions."""
    _hand(root, [("PROJA_01_01", "2026-03-02", "P2", "PROJA_01")])
    _census(root, issues=False)
    assert [g["status"] for g in mv.check()["gaps"]] == ["missing"]


def test_fix_these_file_says_recording_failed_and_acknowledging_stops_it(root, tmp_path):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P2", "PROJA_01")])
    _census(root)
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    attention.write_all_fix_these(sheets, ["PROJA_01"])
    ws = openpyxl.load_workbook(sheets / "!! PROJA_01 - FIX THESE.xlsx").active
    lines = [r[0] for r in ws.iter_rows(min_row=5, values_only=True)]
    assert any((x or "").startswith("recording failed: " + REC) for x in lines)
    # Acknowledged once, by the recording's name, with the missing-videos command.
    assert sorted(mv.acknowledge([REC], by="a person", reason="OBS crashed")) == KEYS
    assert [i for i in attention.items() if i["source"] == mv.SOURCE] == []
    res = mv.check()
    assert {g["status"] for g in res["gaps"]} == {"acknowledged"}
    rows = list(csv.DictReader(open(mv.write_csv(res), encoding="utf-8")))
    assert rows[0]["recording"] == REC and rows[0]["recording_issue_reason"] == REASON
    assert rows[0]["acknowledged_reason"] == "OBS crashed"


def test_the_tab_shows_the_failed_recording_line():
    from PyQt5.QtWidgets import QApplication, QLabel
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    from mousedb.gui.data_status_tab import DataStatusTab
    tab = SimpleNamespace(attention_label=QLabel(), ATTENTION_SHOWN=DataStatusTab.ATTENTION_SHOWN)
    found = [{"cohort_id": "PROJA_01", "source": mv.SOURCE, "kind": mv.KIND_FAILED,
              "sessions": 2, "session_date": "2026-03-02",
              "says": "recording failed: %s, %s" % (REC, REASON)}]
    DataStatusTab._show_attention(tab, found)
    assert "recording failed: " + REC in tab.attention_label.text()
    del app


# ------------------------------------------------------------- the database

@pytest.fixture
def db(tmp_path, monkeypatch):
    from mousedb import backfill
    from mousedb.database import Database
    monkeypatch.setattr(backfill, "backfill_phases", lambda *a, **k: None)
    d = Database(tmp_path / "test.db")
    d.init_db()
    return d


def test_the_record_is_imported_updated_and_removed(db):
    c = ri.import_from_census(_census_dict(), db=db, hold_lock=False)
    assert c == {"added": 2, "updated": 0, "removed": 0}
    from mousedb.schema import SessionRecordingStatus
    with db.session() as s:
        rows = {r.video_key: (r.subject_id, r.session_date, r.tray_type, r.tray_number,
                              r.status, r.reason, r.collage_name, r.detected_at)
                for r in s.query(SessionRecordingStatus).all()}
    assert rows["20260302_PROJA0101_P2"] == (
        "PROJA_01_01", dt.date(2026, 3, 2), "P", 2, "recording_failed", REASON, REC,
        "2026-03-02T16:00:00")
    assert ri.import_from_census(_census_dict(), db=db, hold_lock=False) == \
        {"added": 0, "updated": 0, "removed": 0}
    # A census from a pipeline that does not report recording issues changes nothing.
    assert ri.import_from_census(_census_dict(issues=False) | {"x": 1}, db=db,
                                 hold_lock=False)["skipped"]
    # A person released the recording: it is no longer listed, so it goes.
    empty = _census_dict(issues=False)
    empty["recording_issues"] = []
    assert ri.import_from_census(empty, db=db, hold_lock=False)["removed"] == 2


def _scored_day(db):
    from mousedb.schema import Cohort, PelletScore, Project, Subject
    with db.session() as s:
        if not s.query(Project).filter_by(project_code="PROJA").first():
            s.add(Project(project_code="PROJA", project_name="test project"))
        s.flush()
        s.add(Cohort(cohort_id="PROJA_01", project_code="PROJA", start_date=dt.date(2026, 1, 1)))
        s.flush()
        s.add(Subject(subject_id="PROJA_01_01", cohort_id="PROJA_01", sex="F"))
        s.flush()
        for day in (dt.date(2026, 3, 2), dt.date(2026, 3, 3)):
            for p in range(1, 21):
                s.add(PelletScore(subject_id="PROJA_01_01", session_date=day, tray_type="P",
                                  tray_number=2, pellet_number=p, score=2 if p <= 5 else 0))


def test_the_session_row_of_the_export_carries_status_and_reason(db, root, tmp_path):
    """Hand scores stay; the session row says the recording failed and why."""
    from mousedb.exporters import data_dictionary as dd
    from mousedb.exporters.cohort_reports import export_odc_format
    _scored_day(db)
    ri.import_from_census(_census_dict(), db=db, hold_lock=False)
    out = export_odc_format(db, "PROJA_01", tmp_path / "odc.xlsx")
    df = pd.read_excel(out).set_index("Date")
    failed = df.loc[pd.Timestamp("2026-03-02")]
    assert failed["Recording_Status"] == "recording_failed"
    assert "tray P2" in failed["Recording_Issue_Reason"] and REASON in failed["Recording_Issue_Reason"]
    assert failed["Daily_Retrieved"] == 5          # the hand scores are kept
    ok = df.loc[pd.Timestamp("2026-03-03")]
    assert ok["Recording_Status"] == "no_issue_recorded"
    # Every column documented: an ODC upload fails on one that is not.
    assert dd.undocumented_columns("ODC_sessions", df.reset_index().columns) == []


def test_an_export_from_a_database_without_the_table_still_works(db, root, tmp_path):
    from mousedb.exporters.cohort_reports import export_odc_format
    _scored_day(db)
    out = export_odc_format(db, "PROJA_01", tmp_path / "odc.xlsx")
    assert set(pd.read_excel(out)["Recording_Status"]) == {"no_issue_recorded"}


def test_the_census_step_imports_then_lists(monkeypatch):
    from mousedb.exporters import refresh_snapshot as rs
    calls = []
    monkeypatch.setattr(rs, "IMPORT_RECORDING_ISSUES", True)
    monkeypatch.setattr("mousedb.recording_issues.import_quietly",
                        lambda: calls.append("import") or "imported: 2 added, 0 updated, 0 removed")
    rs.import_recording_issues()
    assert calls == ["import"]


def test_parse_key():
    assert ri.parse_key("20260302_PROJA0102_P2") == {
        "subject_id": "PROJA_01_02", "session_date": dt.date(2026, 3, 2),
        "tray_type": "P", "tray_number": 2}
    assert ri.parse_key("nonsense")["subject_id"] is None


def test_the_counts_and_the_weekly_page_count_a_failed_recording():
    """Readers of the census that do not know the element would drop the
    sessions from their totals or count them as not started."""
    from mousedb import pipeline_census, weekly_report
    census = _census_dict()
    joined = pipeline_census.join_with_db(census, set())
    assert joined["by_element"]["recording_issue"] == 2
    by_project, _by_cohort = weekly_report.census_tables(
        {"sessions": {k: dict(v, project="PROJA") for k, v in census["sessions"].items()}})
    page = weekly_report._table("Project", by_project)
    assert "Recording failed (never analyzed)" in page
    assert by_project["PROJA"]["recording_issue"] == 2
    assert "not_started_pillar" not in by_project["PROJA"]
