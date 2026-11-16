"""The hand-scores file the reaching pipeline reads before it files a video.

WHY: the pipeline compares each video's pellets with the session's hand
scores, and it is a separate tool that never imports mousedb -- so the scores
reach it as one file, keyed the way the pipeline names its videos, with the
time of each cohort's last successful sheet import beside it.
"""
import contextlib
import csv
import json
from datetime import date

import pytest

from mousedb import hand_scores_file as hsf
from mousedb import sheet_sync, task_mutex


def test_video_key_is_the_pipelines_video_name():
    assert hsf.video_key("CNT_02_07", date(2025, 7, 9), "P", 1) == "20250709_CNT0207_P1"
    assert hsf.video_key("ASPA_10_11", "2022-08-11", "P", 3) == "20220811_ASPA1011_P3"


def test_every_score_is_written_with_its_cohorts_last_import(tmp_path, monkeypatch):
    from mousedb.database import init_database
    from mousedb.schema import Cohort, PelletScore, Subject
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(e) for e in [
        {"cohort_id": "CNT_02", "success": True, "dry_run": False, "finished": "2026-10-25T20:05:00"},
        {"cohort_id": "CNT_02", "success": False, "dry_run": False, "finished": "2026-10-26T20:05:00"},
        {"cohort_id": "CNT_02", "success": True, "dry_run": True, "finished": "2026-10-26T21:00:00"},
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(sheet_sync, "LEDGER", ledger)
    db = init_database(tmp_path / "scratch.db")
    with db.session() as s:
        s.add(Cohort(cohort_id="CNT_02", project_code="CNT", start_date=date(2025, 6, 29)))
        s.add(Subject(subject_id="CNT_02_07", cohort_id="CNT_02"))
        for p, score in ((1, 2), (2, 0), (3, 1)):
            s.add(PelletScore(subject_id="CNT_02_07", session_date=date(2025, 7, 9),
                              test_phase="Pillar", tray_type="P", tray_number=1,
                              pellet_number=p, score=score, entered_by="excel_import"))
        s.commit()

    m = hsf.write(db_file=tmp_path / "scratch.db", dest=tmp_path / "out", log=lambda *a: None)

    with open(tmp_path / "out" / hsf.FILE_NAME, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["video_key"], r["pellet"], r["score"]) for r in rows] == [
        ("20250709_CNT0207_P1", "1", "2"), ("20250709_CNT0207_P1", "2", "0"),
        ("20250709_CNT0207_P1", "3", "1")]
    assert rows[0]["cohort_id"] == "CNT_02" and rows[0]["project"] == "CNT"
    assert m["rows"] == 3 and m["videos"] == 1
    # the failed import and the dry run do not count
    assert m["last_successful_import"] == {"CNT_02": "2026-10-25T20:05:00"}
    assert json.loads((tmp_path / "out" / hsf.MANIFEST_NAME).read_text()) == m


def _import_nothing(tmp_path, monkeypatch):
    """import_cohorts over an empty sheets folder with one (stubbed) ASPA cohort."""
    d = tmp_path / "sheets"
    d.mkdir()
    monkeypatch.setattr(sheet_sync, "WRITE_HAND_SCORES", True)
    monkeypatch.setattr(sheet_sync, "_import_aspa_one",
                        lambda text, dry_run, by: {"cohort_id": "ASPA_10", "success": True})
    return sheet_sync.import_cohorts(["ASPA_10"], sheets_dir=d)


def test_an_import_rewrites_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hsf, "write", lambda **k: {"rows": 3, "videos": 1})
    r = _import_nothing(tmp_path, monkeypatch)
    assert r["hand_scores"] == {"rows": 3, "videos": 1}
    assert sheet_sync.import_succeeded(r)


def test_a_failed_rewrite_fails_the_import(tmp_path, monkeypatch):
    def broken(**k):
        raise OSError("share unreachable")
    monkeypatch.setattr(hsf, "write", broken)
    r = _import_nothing(tmp_path, monkeypatch)
    assert "share unreachable" in r["hand_scores_error"]
    assert not sheet_sync.import_succeeded(r)
    lines = []
    sheet_sync.print_import_result(r, log=lines.append)
    assert any(l.startswith("FAIL hand scores") for l in lines)


def test_only_an_import_of_every_sheet_moves_full_import_at(tmp_path, monkeypatch):
    """WHY: a cohort with no import at all is settled only by an import of
    EVERY sheet (and the list of cohorts that have one) -- a hand import of
    one cohort rewrites the file too and says nothing about the others."""
    from mousedb.database import init_database
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    init_database(tmp_path / "scratch.db")
    w = lambda **k: hsf.write(db_file=tmp_path / "scratch.db", dest=tmp_path / "out",
                              log=lambda *a: None, **k)
    m1 = w(full_run=True, sheets_seen=["CNT_02", "CNT_01"])
    assert m1["full_import_at"] == m1["written_at"] and m1["sheets_seen"] == ["CNT_01", "CNT_02"]
    m2 = w()                                    # e.g. one cohort imported by hand
    assert m2["full_import_at"] == m1["full_import_at"] and m2["sheets_seen"] == ["CNT_01", "CNT_02"]


def _database_with_an_unfinished_save(tmp_path):
    """A database file with a stopped writer's unfinished save beside it: 3
    saved rows, plus an abandoned change that would have made them 2003."""
    import shutil
    import sqlite3
    live = tmp_path / "live.db"
    con = sqlite3.connect(str(live), isolation_level=None)
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA cache_size = 1")
    con.execute("CREATE TABLE t (n INTEGER, pad TEXT)")
    con.execute("BEGIN")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, "x" * 900) for i in range(3)])
    con.execute("COMMIT")
    con.execute("BEGIN")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, "y" * 900) for i in range(2000)])
    con.execute("UPDATE t SET pad = 'changed'")
    # Copied while the writer is mid-save: exactly what a killed writer leaves.
    stopped = tmp_path / "stopped.db"
    shutil.copy(live, stopped)
    shutil.copy(str(live) + "-journal", str(stopped) + "-journal")
    con.execute("ROLLBACK")
    con.close()
    return stopped


def test_the_read_gets_past_a_stopped_writers_unfinished_save(tmp_path):
    import sqlite3
    import pytest
    stopped = _database_with_an_unfinished_save(tmp_path)
    q = "SELECT n, pad FROM t ORDER BY n"
    # What the old code did: a read-only connection is refused outright.
    con = sqlite3.connect("file:%s?mode=ro" % stopped.as_posix(), uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute(q).fetchall()
    con.close()
    # The read undoes the unfinished save and returns only what was saved.
    assert hsf._read_rows(stopped, q) == [(0, "x" * 900), (1, "x" * 900), (2, "x" * 900)]


def test_the_read_still_cannot_change_a_row(tmp_path, monkeypatch):
    import sqlite3
    import pytest
    stopped = _database_with_an_unfinished_save(tmp_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        hsf._read_rows(stopped, "DELETE FROM t")


def test_another_failure_of_the_read_is_not_retried(tmp_path):
    import sqlite3
    import pytest
    db = tmp_path / "plain.db"
    sqlite3.connect(str(db)).close()
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        hsf._read_rows(db, "SELECT * FROM nothing_here")


def _scratch_writer(tmp_path, monkeypatch):
    from mousedb.database import init_database
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    init_database(tmp_path / "scratch.db")
    return lambda **k: hsf.write(db_file=tmp_path / "scratch.db", dest=tmp_path / "out",
                                 log=lambda *a: None, **k)


def test_the_manifest_carries_the_hash_of_its_scores_file(tmp_path, monkeypatch):
    """WHY: the scores file and its manifest are two renames. A reader that
    catches one new and one old must be able to tell, so the manifest names the
    exact scores file it was written with."""
    import hashlib
    w = _scratch_writer(tmp_path, monkeypatch)
    m = w()
    data = (tmp_path / "out" / hsf.FILE_NAME).read_bytes()
    assert m["scores_sha256"] == hashlib.sha256(data).hexdigest()
    on_disk = json.loads((tmp_path / "out" / hsf.MANIFEST_NAME).read_text())
    assert on_disk["scores_sha256"] == m["scores_sha256"]


def test_a_rename_refused_while_a_reader_has_the_file_open_is_retried(tmp_path, monkeypatch):
    """WHY: on Windows a rename over a file another program has open fails for
    that moment. Failing the write then left a new scores file beside an old
    manifest; the rename is retried instead."""
    import os as _os
    w = _scratch_writer(tmp_path, monkeypatch)
    real = _os.replace
    refused = []

    def busy_once(src, dst):
        if str(dst).endswith(hsf.MANIFEST_NAME) and not refused:
            refused.append(dst)
            raise PermissionError(13, "The process cannot access the file")
        return real(src, dst)

    monkeypatch.setattr(hsf, "REPLACE_WAIT_SECONDS", 0, raising=False)
    monkeypatch.setattr(hsf.os, "replace", busy_once)
    m = w()
    assert refused                                    # the first try was refused
    assert json.loads((tmp_path / "out" / hsf.MANIFEST_NAME).read_text()) == m


def test_a_cohort_seen_once_stays_on_the_sheets_list(tmp_path, monkeypatch):
    """WHY: a cohort with a sheet waits for its first import; one with none is
    settled as having no hand scores and its videos are filed uncompared. A
    sheet missing from ONE folder listing used to take its cohort off the list."""
    w = _scratch_writer(tmp_path, monkeypatch)
    w(full_run=True, sheets_seen=["PROJA_77", "PROJA_78"])
    m = w(full_run=True, sheets_seen=["PROJA_78", "PROJA_79"])   # PROJA_77 missing this time
    assert m["sheets_seen"] == ["PROJA_77", "PROJA_78", "PROJA_79"]


def test_a_manifest_that_cannot_be_read_stops_the_write_and_keeps_the_list(
        tmp_path, monkeypatch):
    """WHY: an unreadable manifest used to be taken as empty, so one bad read
    shrank the list of cohorts with a sheet to this import's listing -- and a
    never-imported cohort missing from it had its videos filed uncompared.
    A damaged manifest now fails the write and leaves both files as they were;
    a read refused for a moment is retried."""
    w = _scratch_writer(tmp_path, monkeypatch)
    w(full_run=True, sheets_seen=["PROJA_77", "PROJA_78"])
    out = tmp_path / "out"
    csv_before = (out / hsf.FILE_NAME).read_bytes()
    (out / hsf.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception, match="damaged"):
        w(full_run=True, sheets_seen=["PROJA_78"])
    assert (out / hsf.MANIFEST_NAME).read_text(encoding="utf-8") == "{not json"
    assert (out / hsf.FILE_NAME).read_bytes() == csv_before

    # A read refused once (the file open elsewhere for a moment) is retried
    # and the list carried on.
    (out / hsf.MANIFEST_NAME).write_text(json.dumps(
        {"sheets_seen": ["PROJA_77", "PROJA_78"]}), encoding="utf-8")
    real = type(out).read_text
    refused = []

    def busy_once(self, *a, **k):
        if self.name == hsf.MANIFEST_NAME and not refused:
            refused.append(self)
            raise PermissionError(13, "The process cannot access the file")
        return real(self, *a, **k)

    monkeypatch.setattr(hsf, "REPLACE_WAIT_SECONDS", 0, raising=False)
    monkeypatch.setattr(type(out), "read_text", busy_once)
    m = w(full_run=True, sheets_seen=["PROJA_78"])
    assert refused and m["sheets_seen"] == ["PROJA_77", "PROJA_78"]


def test_a_folder_other_than_the_configured_one_settles_nothing(tmp_path, monkeypatch):
    """WHY: an import of a folder named by hand is not an import of the sheets
    folder: it must not add to sheets_seen nor move full_import_at."""
    import mousedb.cohort_sheets as cs
    configured = tmp_path / "sheets"
    configured.mkdir()
    (configured / "Connectome_77_Animal_Tracking.xlsx").write_bytes(b"x")
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "Connectome_78_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(configured))
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(sheet_sync, "WRITE_HAND_SCORES", True)
    monkeypatch.setattr(sheet_sync, "_import_one", lambda cid, sheet, *a, **k: {
        "cohort_id": cid, "success": True, "imported": {}, "warnings": []})
    calls = []
    monkeypatch.setattr(hsf, "write", lambda **k: calls.append(k) or {"rows": 0, "videos": 0})
    sheet_sync.import_cohorts(None, sheets_dir=other)
    assert calls[-1]["full_run"] is False and calls[-1]["sheets_seen"] is None
    sheet_sync.import_cohorts(None)
    assert calls[-1]["full_run"] is True and calls[-1]["sheets_seen"] == ["CNT_77"]


def test_an_import_that_saved_the_scores_counts_even_if_a_later_step_failed(tmp_path, monkeypatch):
    """WHY: a video with no hand score for its session is filed only once an
    import after it was ready brought its cohort's scores in. An import whose
    animal-details step failed AFTER the scores were saved used to count as no
    import at all, so those videos kept waiting on accessory information."""
    w = _scratch_writer(tmp_path, monkeypatch)
    sheet_sync._append_ledger({"cohort_id": "PROJA_77", "success": True, "dry_run": False,
                               "finished": "2026-01-01T20:00:00"})
    sheet_sync._append_ledger({"cohort_id": "PROJA_77", "success": False, "dry_run": False,
                               "scores_saved": True, "finished": "2026-01-02T20:00:00"})
    sheet_sync._append_ledger({"cohort_id": "PROJA_77", "success": False, "dry_run": False,
                               "scores_saved": False, "finished": "2026-01-03T20:00:00"})
    sheet_sync._append_ledger({"cohort_id": "PROJA_78", "success": True, "dry_run": False,
                               "scores_saved": False, "finished": "2026-01-03T20:00:00"})
    m = w()
    assert m["last_successful_import"] == {"PROJA_77": "2026-01-02T20:00:00"}


def test_the_import_records_that_its_scores_were_saved_when_the_details_fail(tmp_path, monkeypatch):
    import mousedb.animal_records as ar
    import mousedb.cohort_sheets as cs
    d = tmp_path / "sheets"
    d.mkdir()
    (d / "Connectome_77_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())

    class Saves:
        db = object()

        def import_cohort_file(self, *a, **k):
            return {"success": True, "imported": {"pellet_scores": 20}, "warnings": [], "errors": []}

    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: {
        "records": 0, "warnings": [], "error": "tab unreadable"})
    import mousedb.importers as imp
    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Saves())
    r = sheet_sync.import_cohorts(["CNT_77"])
    c = r["cohorts"][0]
    assert c["success"] is False and c["scores_saved"] is True
    line = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])
    assert line["scores_saved"] is True
    assert hsf._last_successful_imports() == {"CNT_77": line["finished"]}
