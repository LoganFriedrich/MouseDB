"""A session scored by hand in a tracking sheet must have its video somewhere in
the reaching pipeline; one that does not is listed for a person.

WHY: a cohort's sessions were scored by hand in its sheet, but their
recordings never reached the pipeline, and nothing noticed for over a year.
Every input here is synthetic, under tmp_path.
"""
import csv
import json
import os
from pathlib import Path

import openpyxl
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mousedb import attention, missing_videos as mv  # noqa: E402

VIEW = "2026-03-20T02:00:00"


def _hand(root, sessions, seen=("PROJA_01", "PROJA_02")):
    """sessions: (subject, YYYY-MM-DD, tray, cohort). One pellet row each."""
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


def _census(root, names, taken=VIEW):
    d = Path(root) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pipeline_census.json").write_text(json.dumps({
        "generated_at": taken,
        "sessions": {n: {"element": "analyzed", "finished": True} for n in names}}))


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "db"
    r.mkdir()
    monkeypatch.setenv("MOUSEDB_ROOT", str(r))
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path / "pipe"))
    return r


def test_a_hand_scored_session_with_no_video_anywhere_is_missing(root):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                 ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01")])
    _census(root, ["20260302_PROJA0101_P1"])
    gaps = mv.check()["gaps"]
    assert [g["video_key"] for g in gaps] == ["20260302_PROJA0102_P1"]
    assert gaps[0]["status"] == "missing"
    assert gaps[0]["days_after_session"] == 18


def test_the_grace_period_is_counted_to_the_census_not_to_today(root, monkeypatch):
    # 2026-03-17 is exactly 3 days before the view: not yet missing; 03-16 is.
    _hand(root, [("PROJA_01_01", "2026-03-17", "P1", "PROJA_01"),
                 ("PROJA_01_01", "2026-03-16", "P1", "PROJA_01")])
    _census(root, [])
    assert [g["session_date"] for g in mv.check()["gaps"]] == ["2026-03-16"]
    monkeypatch.setenv("MOUSEDB_MISSING_VIDEO_GRACE_DAYS", "0")
    assert len(mv.check()["gaps"]) == 2
    monkeypatch.setenv("MOUSEDB_MISSING_VIDEO_GRACE_DAYS", "three")
    with pytest.raises(mv.CannotCheck):
        mv.check()


def test_a_frozen_cohort_without_a_live_sheet_is_not_judged(root):
    _hand(root, [("PROJB_04_01", "2020-01-01", "P1", "PROJB_04"),
                 ("PROJA_01_01", "2025-01-01", "P1", "PROJA_01")], seen=("PROJA_01",))
    _census(root, [])
    assert {g["cohort_id"] for g in mv.check()["gaps"]} == {"PROJA_01"}


def test_the_same_animal_and_day_under_another_tray_is_pointed_out(root):
    _hand(root, [("PROJA_01_01", "2026-03-02", "E1", "PROJA_01")])
    _census(root, ["20260302_PROJA0101_F1"])
    rec = mv.recordings(mv.check()["gaps"])[0]
    assert "under tray F1" in mv.says(rec)


def test_each_missing_recording_is_one_fix_these_line_saying_what_to_do(root, tmp_path):
    _hand(root, [("PROJA_01_%02d" % i, "2025-08-14", t, "PROJA_01")
                 for i in (1, 2) for t in ("P1", "P2")])
    _census(root, [])
    found = [i for i in attention.items() if i["source"] == mv.SOURCE]
    assert len(found) == 2                      # P1 and P2, two animals each
    says = found[0]["says"]
    assert "PROJA_01 2025-08-14 tray P1" in says
    assert "OBS folder" in says and "20250814_PROJA0101,PROJA0102_P1.mkv" in says
    assert str(tmp_path / "pipe" / "Unanalyzed" / "Multi-Animal") in says
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    attention.write_all_fix_these(sheets, ["PROJA_01"])
    ws = openpyxl.load_workbook(sheets / "!! PROJA_01 - FIX THESE.xlsx").active
    lines = [r[0] for r in ws.iter_rows(min_row=5, values_only=True)]
    assert sum("no video" in (x or "") for x in lines) == 2


def test_the_tab_shows_one_red_line_per_cohort():
    from PyQt5.QtWidgets import QApplication, QLabel
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    from mousedb.gui.data_status_tab import DataStatusTab
    tab = SimpleNamespace(attention_label=QLabel(), ATTENTION_SHOWN=DataStatusTab.ATTENTION_SHOWN)
    found = [{"cohort_id": "PROJA_01", "source": mv.SOURCE, "kind": mv.KIND, "sessions": 16,
              "session_date": d, "says": "x"} for d in ("2025-08-14", "2025-08-15")]
    found.append({"cohort_id": "PROJA_02", "source": "sheet import", "says": "fix a date"})
    DataStatusTab._show_attention(tab, found)
    text = tab.attention_label.text()
    assert "Needs your attention (2)" in text
    assert "PROJA_01: 32 hand-scored session(s) in 2 recording(s) (2025-08-14 to 2025-08-15)" in text
    del app


def test_an_acknowledged_gap_stops_nagging_but_a_new_one_in_the_cohort_does_not(root):
    _hand(root, [("PROJA_01_01", "2025-08-14", "P1", "PROJA_01")])
    _census(root, [])
    with pytest.raises(ValueError):
        mv.acknowledge(["PROJA_01"], by="", reason="gone")
    with pytest.raises(ValueError):
        mv.acknowledge(["PROJA_09"], by="a person", reason="gone")
    assert mv.acknowledge(["PROJA_01"], by="a person", reason="recording PC wiped") \
        == ["20250814_PROJA0101_P1"]
    assert [i for i in attention.items() if i["source"] == mv.SOURCE] == []
    # a gap that opens later in the same cohort is listed
    _hand(root, [("PROJA_01_01", "2025-08-14", "P1", "PROJA_01"),
                 ("PROJA_01_02", "2026-03-01", "P2", "PROJA_01")])
    res = mv.check()
    assert {g["video_key"]: g["status"] for g in res["gaps"]} == {
        "20250814_PROJA0101_P1": "acknowledged", "20260301_PROJA0102_P2": "missing"}
    # the list file keeps both, the acknowledged one with who and why
    p = mv.write_csv(res)
    assert p == root / "exports" / "missing_videos" / "missing_session_videos.csv"
    rows = {r["video_key"]: r for r in csv.DictReader(open(p, encoding="utf-8"))}
    assert rows["20250814_PROJA0101_P1"]["acknowledged_reason"] == "recording PC wiped"
    assert "OBS folder" in rows["20260301_PROJA0102_P2"]["what_to_do"]


def test_no_census_is_said_never_read_as_nothing_missing(root):
    _hand(root, [("PROJA_01_01", "2025-08-14", "P1", "PROJA_01")])
    found = [i for i in attention.items() if i["source"] == mv.SOURCE]
    assert len(found) == 1 and found[0]["cohort_id"] is None
    assert "Could not check" in found[0]["says"]
    assert mv.write_list_quietly().startswith("FAILED")


def test_every_import_that_writes_rewrites_the_list(root, monkeypatch):
    from mousedb import sheet_sync
    _hand(root, [("PROJA_01_01", "2025-08-14", "P1", "PROJA_01")])
    _census(root, [])
    monkeypatch.setattr(sheet_sync, "WRITE_MISSING_VIDEOS", True)
    results = {}
    sheet_sync._after_import(results, None, None, full_run=False)
    assert results["missing_videos"].startswith("written")
    assert (root / "exports" / "missing_videos" / "missing_session_videos.csv").is_file()


def test_the_command_lists_per_cohort(root, capsys):
    _hand(root, [("PROJA_01_01", "2025-08-14", "P1", "PROJA_01")])
    _census(root, [])
    assert mv.main([]) == 0
    out = capsys.readouterr().out
    assert "PROJA_01      1 missing" in out
    out.encode("ascii")                          # terminal output stays ASCII
