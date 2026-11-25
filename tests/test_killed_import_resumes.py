"""A reach import that is killed part-way and restarted carries on where it
stopped.

WHY: the scheduled import has a time limit and is restarted when it is killed.
The ledger used to be written only at the end of a run, so a run killed just
before the limit had recorded nothing and the restart imported every file
again. Now the ledger is saved during the run, never ahead of the database,
always whole; the cohorts still to re-label are kept until they are done; and
a video going back to review loses its ledger entry before its rows are
deleted.

A kill is simulated with a BaseException (KeyboardInterrupt), which no
``except Exception`` in the importer catches -- like the process ending.
"""
import contextlib
import json
import shutil
import sqlite3

import pytest

from mousedb import import_reaches as ir

from tests.test_videos_back_in_review_leave_the_database import (
    OTHER, _count, _features, _setup, VIDEO)

VIDEOS = ["20260102_PROJA0101_P1", "20260102_PROJA0102_P1", "20260102_PROJA0103_P1"]


def _ledger(tmp_path):
    p = tmp_path / "dbroot" / "logs" / ir.LEDGER_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _names(tmp_path):
    return sorted(k.rsplit("/", 1)[-1][:-len(ir.FEATURES_SUFFIX)] for k in _ledger(tmp_path))


def _pending(tmp_path):
    """The cohort names on the list still to re-label (the file is an object
    {cohort: queue count}; an older version wrote a plain list)."""
    p = tmp_path / "dbroot" / "logs" / ir.PENDING_PHASES_NAME
    return sorted(json.loads(p.read_text(encoding="utf-8"))) if p.is_file() else None


def _relabel_calls(monkeypatch, fail_with=None):
    import mousedb.backfill as bf
    calls = []

    def fake(**k):
        if fail_with is not None:
            raise fail_with
        calls.append(k["cohort_id"])
    monkeypatch.setattr(bf, "backfill_phases", fake)
    return calls


def test_killed_mid_loop_restart_imports_only_the_rest(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    for v in VIDEOS:
        _features(folder, v)
    monkeypatch.setattr(ir, "LEDGER_SAVE_SECONDS", 0)      # save after every video
    real = ir.import_file
    done = []

    def dies_on_third(conn, path, dry_run=False):
        if len(done) == 2:
            raise KeyboardInterrupt
        done.append(path.name)
        return real(conn, path, dry_run=dry_run)
    monkeypatch.setattr(ir, "import_file", dies_on_third)
    with pytest.raises(KeyboardInterrupt):
        ir.run(log=lambda *_: None)

    # The two committed videos are in the ledger; the cohort waits to be re-labeled.
    assert sorted(k.rsplit("/", 1)[-1] for k in _ledger(tmp_path)) == sorted(done)
    assert _pending(tmp_path) == ["PROJA_01"]

    monkeypatch.setattr(ir, "import_file", real)
    calls = _relabel_calls(monkeypatch)
    res = ir.run(log=lambda *_: None)
    assert res.imported == 1 and res.skipped == 2
    assert calls == ["PROJA_01"]
    assert _pending(tmp_path) == []
    for v in VIDEOS:
        assert _count(db, v) == (2, 20)


def test_ledger_never_runs_ahead_of_the_database(tmp_path, monkeypatch):
    """A video whose transaction did not commit is not in the ledger."""
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    _features(folder, VIDEOS[1])
    monkeypatch.setattr(ir, "LEDGER_SAVE_SECONDS", 0)
    real = ir.write_pellets
    n = []

    def dies_inside_second_transaction(conn, video_name, rows):
        n.append(video_name)
        if len(n) == 2:
            raise KeyboardInterrupt
        return real(conn, video_name, rows)
    monkeypatch.setattr(ir, "write_pellets", dies_inside_second_transaction)
    with pytest.raises(KeyboardInterrupt):
        ir.run(log=lambda *_: None)
    keys = [k.rsplit("/", 1)[-1] for k in _ledger(tmp_path)]
    assert keys == [VIDEOS[0] + ir.FEATURES_SUFFIX]
    assert _count(db, VIDEOS[0]) == (2, 20)
    assert _count(db, VIDEOS[1]) == (0, 0)                 # rolled back, not in the ledger


def test_killed_while_relabelling_restart_relabels(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    _relabel_calls(monkeypatch, fail_with=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        ir.run(log=lambda *_: None)
    assert len(_ledger(tmp_path)) == 1 and _pending(tmp_path) == ["PROJA_01"]

    calls = _relabel_calls(monkeypatch)
    said = []
    res = ir.run(log=said.append)
    assert res.imported == 0 and calls == ["PROJA_01"] and _pending(tmp_path) == []
    assert any("still to re-label" in s for s in said)


def test_a_failed_relabel_is_retried_next_run(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    _relabel_calls(monkeypatch, fail_with=RuntimeError("database is locked"))
    assert ir.run(log=lambda *_: None).errors == 1
    assert _pending(tmp_path) == ["PROJA_01"]
    calls = _relabel_calls(monkeypatch)
    assert ir.run(log=lambda *_: None).errors == 0
    assert calls == ["PROJA_01"]


def test_a_write_that_fails_leaves_the_old_ledger_whole(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    ir.run(log=lambda *_: None)
    before = _ledger(tmp_path)
    assert len(before) == 1

    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(ir.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        ir.save_ledger({"x": 1})
    assert _ledger(tmp_path) == before                     # still readable, unchanged


def test_killed_while_taking_out_a_video_it_stays_out_after_refiling(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)        # something stays filed: an empty listing withdraws nothing
    ir.run(log=lambda *_: None)
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    queue.mkdir(parents=True)
    shutil.move(str(filed / (VIDEO + "_features.json")), str(queue))

    real = ir._copy_rows
    monkeypatch.setattr(ir, "_copy_rows", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        ir.run(log=lambda *_: None)
    # Killed before the rows went: the ledger entry is already gone, on disk.
    assert _names(tmp_path) == [OTHER] and _count(db, VIDEO) == (2, 20)

    monkeypatch.setattr(ir, "_copy_rows", real)
    assert ir.run(log=lambda *_: None).withdrawn == 1
    assert _count(db, VIDEO) == (0, 0)
    # Filed again unchanged: imported, because no stale entry says it was.
    shutil.move(str(queue / (VIDEO + "_features.json")), str(filed))
    assert ir.run(log=lambda *_: None).imported == 1
    assert _count(db, VIDEO) == (2, 20)


def test_a_leftover_entry_of_a_video_in_review_with_no_rows_is_dropped(tmp_path, monkeypatch):
    """The state an older importer could leave: rows taken out, entry kept."""
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)        # something stays filed: an empty listing withdraws nothing
    ir.run(log=lambda *_: None)
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    queue.mkdir(parents=True)
    shutil.move(str(filed / (VIDEO + "_features.json")), str(queue))
    import sqlite3
    with contextlib.closing(sqlite3.connect(str(db))) as c, c:
        c.execute("DELETE FROM reach_data WHERE video_name = ?", (VIDEO,))
        c.execute("DELETE FROM video_pellets WHERE video_name = ?", (VIDEO,))
    assert _names(tmp_path) == sorted([VIDEO, OTHER])
    ir.run(log=lambda *_: None)
    assert _names(tmp_path) == [OTHER]
    shutil.move(str(queue / (VIDEO + "_features.json")), str(filed))
    assert ir.run(log=lambda *_: None).imported == 1


def test_a_dry_run_writes_no_ledger(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    monkeypatch.setattr(ir, "LEDGER_SAVE_SECONDS", 0)
    ir.run(dry_run=True, log=lambda *_: None)
    assert not (tmp_path / "dbroot" / "logs").exists()


# ---------------------------------------------------------------------------
# An unreadable ledger stops the run; it is never read as "nothing imported".
# WHY: an unreadable ledger used to be treated as empty, so the run imported
# every results file again (hours, under the database lock) and said nothing.
# ---------------------------------------------------------------------------

def _reach_rows(db):
    with contextlib.closing(sqlite3.connect(str(db))) as c:
        return c.execute("SELECT COUNT(*) FROM reach_data").fetchone()[0]


def _ledger_file(tmp_path):
    return tmp_path / "dbroot" / "logs" / ir.LEDGER_NAME


def test_a_damaged_ledger_stops_the_run_and_imports_nothing(tmp_path, monkeypatch, capsys):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    ir.run(log=lambda *_: None)
    _features(folder, VIDEOS[1])
    _ledger_file(tmp_path).write_text('{"Analyzed/Project/PROJA01/x', encoding="utf-8")
    damaged = _ledger_file(tmp_path).read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="nothing was imported"):
        ir.run(log=lambda *_: None)
    assert _count(db, VIDEOS[1]) == (0, 0)                 # nothing imported
    assert _ledger_file(tmp_path).read_text(encoding="utf-8") == damaged   # left as found

    assert ir.main([]) == 1
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("[FAIL] ") and str(_ledger_file(tmp_path)) in out[-1]
    assert "rename that file" in out[-1]
    out[-1].encode("ascii")                                # one console-safe line


def test_a_ledger_that_is_not_a_mapping_is_refused(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    _ledger_file(tmp_path).parent.mkdir(parents=True)
    _ledger_file(tmp_path).write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="nothing was imported"):
        ir.run(log=lambda *_: None)
    assert _reach_rows(db) == 0                           # nothing imported


def test_a_ledger_read_that_fails_briefly_is_retried(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    ir.run(log=lambda *_: None)
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    real = type(_ledger_file(tmp_path)).read_text
    fails = []

    def flaky(self, *a, **k):
        if self.name == ir.LEDGER_NAME and len(fails) < 2:
            fails.append(1)
            raise OSError("the network name is no longer available")
        return real(self, *a, **k)
    monkeypatch.setattr(type(_ledger_file(tmp_path)), "read_text", flaky)
    res = ir.run(log=lambda *_: None)
    assert len(fails) == 2 and res.imported == 0 and res.skipped == 1


def test_a_ledger_read_that_keeps_failing_stops_the_run(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    ir.run(log=lambda *_: None)
    _features(folder, VIDEOS[1])
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    real = type(_ledger_file(tmp_path)).read_text

    def down(self, *a, **k):
        if self.name == ir.LEDGER_NAME:
            raise OSError("the network name is no longer available")
        return real(self, *a, **k)
    monkeypatch.setattr(type(_ledger_file(tmp_path)), "read_text", down)
    with pytest.raises(RuntimeError, match="nothing was imported"):
        ir.run(log=lambda *_: None)
    assert _count(db, VIDEOS[1]) == (0, 0)


def test_no_ledger_at_all_imports_everything_and_says_so(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    said = []
    assert ir.run(log=said.append).imported == 1
    assert any("no import ledger yet" in s for s in said)


def test_an_empty_ledger_is_not_reported_as_missing(tmp_path, monkeypatch):
    """A ledger that exists and holds nothing (a run that found no results
    files leaves one) is not "no ledger yet": that line would send a person
    looking for a file that is there."""
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    ledger = _ledger_file(tmp_path)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("{}", encoding="utf-8")
    said = []
    assert ir.run(log=said.append).imported == 1
    assert not any("no import ledger yet" in s for s in said)


def test_a_damaged_list_of_cohorts_to_relabel_stops_the_run(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", VIDEOS[0])
    pending = tmp_path / "dbroot" / "logs" / ir.PENDING_PHASES_NAME
    pending.parent.mkdir(parents=True)
    pending.write_text('["PROJA_0', encoding="utf-8")
    with pytest.raises(RuntimeError, match="nothing was imported"):
        ir.run(log=lambda *_: None)
    assert _reach_rows(db) == 0                           # nothing imported


def test_a_list_damaged_part_way_says_the_run_stopped_part_way(tmp_path, monkeypatch):
    """Only a stop before the first import may say "nothing was imported"."""
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    _features(folder, VIDEOS[1])
    monkeypatch.setattr(ir, "LEDGER_SAVE_SECONDS", 0)
    pending = tmp_path / "dbroot" / "logs" / ir.PENDING_PHASES_NAME
    real = ir.import_file

    def damages_the_list(conn, path, dry_run=False):
        out = real(conn, path, dry_run=dry_run)
        pending.parent.mkdir(parents=True, exist_ok=True)
        pending.write_text('["PROJA_0', encoding="utf-8")
        return out
    monkeypatch.setattr(ir, "import_file", damages_the_list)
    said = []
    with pytest.raises(RuntimeError, match="stopped part-way") as e:
        ir.run(log=said.append)
    assert "nothing was imported" not in str(e.value)
    assert _reach_rows(db) == 4                            # both videos landed
    assert not any("[FAIL]" in s for s in said)            # no video counted as failed


# ---------------------------------------------------------------------------
# A network share that drops for a moment is not a missing ledger.
# WHY: on Windows an unreachable mapped drive answers "network path not found"
# (error 53) or "network name not found" (67), which Python raises as
# FileNotFoundError -- the same exception as a file that is truly not there.
# Read as "no ledger yet", one such moment re-imported every results file.
# ---------------------------------------------------------------------------

def share_unreachable(path):
    """The exception a read on an unreachable mapped share raises."""
    import errno
    import os
    if os.name == "nt":
        e = OSError(None, "The network path was not found", str(path), 53)
    else:
        e = FileNotFoundError(errno.ENOENT, "The network path was not found", str(path))
    assert isinstance(e, FileNotFoundError)
    return e


def _share_down(monkeypatch, path_type, name, logs_listable=False):
    """Reads of ``name`` fail as on an unreachable share; so does listing its
    folder unless ``logs_listable``."""
    import os
    real_read = path_type.read_text
    real_scandir = os.scandir

    def read(self, *a, **k):
        if self.name == name:
            raise share_unreachable(self)
        return real_read(self, *a, **k)

    def scandir(folder="."):
        if not logs_listable and os.path.basename(os.fspath(folder)) == "logs":
            raise share_unreachable(folder)
        return real_scandir(folder)
    monkeypatch.setattr(path_type, "read_text", read)
    monkeypatch.setattr(ir.os, "scandir", scandir)


def test_a_share_drop_while_reading_the_ledger_reimports_nothing(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    _features(folder, VIDEOS[1])
    assert ir.run(log=lambda *_: None).imported == 2
    before = _ledger_file(tmp_path).read_text(encoding="utf-8")
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    real = ir.import_file
    imported = []

    def spy(conn, path, dry_run=False):
        imported.append(path.name)
        return real(conn, path, dry_run=dry_run)
    monkeypatch.setattr(ir, "import_file", spy)
    _share_down(monkeypatch, type(folder), ir.LEDGER_NAME)
    with pytest.raises(RuntimeError, match="nothing was imported"):
        ir.run(log=lambda *_: None)
    assert imported == []                                  # nothing imported again
    monkeypatch.undo()
    assert _ledger_file(tmp_path).read_text(encoding="utf-8") == before


def test_a_ledger_reported_missing_but_listed_in_its_folder_is_retried(tmp_path, monkeypatch):
    """The folder answers and names the file: the read failed, the file is there."""
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEOS[0])
    ir.run(log=lambda *_: None)
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    real = type(folder).read_text
    fails = []

    def once(self, *a, **k):
        if self.name == ir.LEDGER_NAME and not fails:
            fails.append(1)
            raise share_unreachable(self)
        return real(self, *a, **k)
    monkeypatch.setattr(type(folder), "read_text", once)
    res = ir.run(log=lambda *_: None)
    assert fails == [1] and res.imported == 0 and res.skipped == 1
