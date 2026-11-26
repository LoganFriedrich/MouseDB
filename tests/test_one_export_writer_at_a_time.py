"""Only one refresh writes the current-exports folder at a time.

WHY: the nightly job, the "Refresh exports now" button and the command line
all write the same folder through one staging folder that each run empties
when it starts, and through fixed ".tmp" names. Two at once delete or publish
each other's half-written files, silently. A refresh that finds another one
writing must wait for it (nightly job, command line) or say it is busy and
change nothing (the button).
"""
import json
import threading

import pandas as pd
import pytest

from mousedb.exporters import current
from mousedb.output_folders import all_cohorts_dir


def _snapshot(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    pd.DataFrame({
        "id": [1], "subject_id": ["PROJ_01_01"], "video_name": ["20250101_PROJ0101_P1"],
        "session_date": ["2025-01-01"], "tray_type": ["P"], "run_number": [1],
        "segment_num": [1], "reach_id": [1], "reach_num": [1],
    }).to_parquet(snap / "reach_data.parquet", index=False)
    pd.DataFrame({
        "id": [1], "subject_id": ["PROJ_01_01"], "session_date": ["2025-01-01"],
        "tray_type": ["P"], "tray_number": [1], "pellet_number": [1], "score": [2],
    }).to_parquet(snap / "pellet_scores.parquet", index=False)
    return snap


def test_a_second_refresh_while_one_is_writing_changes_nothing(tmp_path, monkeypatch):
    """While one refresh is in the middle of writing, a second one started with
    no wait must leave the staging folder and the exports folder untouched."""
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    snap = _snapshot(tmp_path)
    out = tmp_path / "current"
    seen = {}
    real_manual = current._manual_scores_csv

    def manual_then_second_refresh(snapshot_dir, out_dir, manifest):
        # The first run is part-way through: a file of its own sits in the
        # staging folder, waiting to be copied into place.
        staged = current._staging_dir(snapshot_dir) / "half_written.csv"
        staged.write_text("first run's file", encoding="utf-8")
        before = (out_dir / "MANIFEST.json").exists()
        try:
            current.refresh_current(snapshot_dir, tmp_path / "current", db_ok=False)
            seen["second"] = "ran"
        except Exception as e:  # the second run must refuse
            seen["second"] = type(e).__name__
        seen["staged_survived"] = staged.exists()
        seen["manifest_written_by_second"] = (out_dir / "MANIFEST.json").exists() and not before
        real_manual(snapshot_dir, out_dir, manifest)

    monkeypatch.setattr(current, "_manual_scores_csv", manual_then_second_refresh)
    monkeypatch.setattr(current, "_odc_reaches_csvs", lambda *a: None)
    current.refresh_current(snap, out, db_ok=False)
    assert seen["second"] != "ran", "a second refresh wrote while the first was writing"
    assert seen["staged_survived"], "the second refresh emptied the first one's staging folder"
    assert not seen["manifest_written_by_second"]


def test_the_button_is_told_at_once_and_nothing_changes(tmp_path):
    snap = _snapshot(tmp_path)
    out = tmp_path / "current"
    out.mkdir()
    (out / "MANIFEST.json").write_text('{"previous": true}', encoding="utf-8")
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        with pytest.raises(current.ExportsBusy) as e:
            current.refresh_current(snap, out, db_ok=False, lock_wait_seconds=0)
        assert "already being written" in str(e.value)
    finally:
        holder.release()
    assert json.loads((out / "MANIFEST.json").read_text()) == {"previous": True}
    # Once the other refresh has finished, the button works.
    m = current.refresh_current(snap, out, db_ok=False, lock_wait_seconds=0)
    assert "reach_data.csv" in m["files"]


def test_a_waiting_refresh_runs_after_the_other_finishes(tmp_path, monkeypatch):
    """The nightly job and the command line wait instead of failing."""
    from mousedb import task_mutex
    monkeypatch.setattr(task_mutex, "POLL_SECONDS", 0.05)
    snap = _snapshot(tmp_path)
    out = tmp_path / "current"
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    # Release only once the second refresh is known to be WAITING (it has
    # looked at the holder's progress at least once). A fixed timer raced the
    # waiter: released too early, the test showed nothing about waiting.
    waiting = threading.Event()
    real_activity = task_mutex.TaskMutex._activity

    def activity(self, text, state):
        waiting.set()
        return real_activity(self, text, state)

    monkeypatch.setattr(task_mutex.TaskMutex, "_activity", activity)

    def release_when_waited_for():
        waiting.wait(30)
        holder.release()

    t = threading.Thread(target=release_when_waited_for)
    t.start()
    try:
        m = current.refresh_current(snap, out, db_ok=False, lock_wait_seconds=30)
    finally:
        t.join(30)
    assert waiting.is_set(), "the second refresh never waited"
    assert (out / "reach_data.csv").is_file() and "reach_data.csv" in m["files"]


def test_the_command_line_says_busy_with_its_own_exit_code(tmp_path, capsys):
    snap = _snapshot(tmp_path)
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        code = current.main(["--snapshot-dir", str(snap), "--out-dir", str(tmp_path / "c"),
                             "--no-wait"])
    finally:
        holder.release()
    assert code == current.EXIT_BUSY
    assert "already being written" in capsys.readouterr().out
    assert not (tmp_path / "c" / "MANIFEST.json").exists()


# A command that WAITS (no --no-wait) and runs out of wait -- its budget used
# up, or the other refresh showing no progress -- must say so in one plain line
# that names the exports lock, change nothing, and exit with its own code. It
# would otherwise end in a Python traceback that spoke of "the database-task lock", a
# different lock, which would send the person to look at the wrong jobs.

def test_the_command_line_says_when_its_wait_ran_out(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    snap = _snapshot(tmp_path)
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        try:
            code = current.main(["--snapshot-dir", str(snap), "--out-dir", str(tmp_path / "c")])
        except Exception as e:                  # noqa: BLE001 -- the old behavior
            pytest.fail("the command ended in a traceback: %r" % e)
    finally:
        holder.release()
    out = capsys.readouterr().out
    assert code not in (0, 1, current.EXIT_BUSY), code
    assert "still writing" in out and "current-exports lock" in out, out
    assert "database-task lock" not in out, out
    assert not (tmp_path / "c" / "MANIFEST.json").exists()


def test_the_per_reach_command_says_when_its_wait_ran_out(tmp_path, capsys, monkeypatch):
    from mousedb import config
    from mousedb.exporters import odc_reaches
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    snap = _snapshot(tmp_path)
    root = tmp_path / "root"
    monkeypatch.setattr(config, "require", lambda key: root)
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        try:
            code = odc_reaches.main(["--snapshot-dir", str(snap)])
        except Exception as e:                  # noqa: BLE001 -- the old behavior
            pytest.fail("the command ended in a traceback: %r" % e)
    finally:
        holder.release()
    out = capsys.readouterr().out
    assert code not in (0, 1), code
    assert "still writing" in out and "current-exports lock" in out, out
    assert not all_cohorts_dir(root).exists()


def test_the_per_reach_command_locks_the_live_folder_however_it_is_named(tmp_path, capsys,
                                                                          monkeypatch):
    """--out-dir naming the live exports folder (here through a '..' detour)
    is the live folder: the command must take the exports lock, not write
    beside a running refresh."""
    from mousedb import config
    from mousedb.exporters import odc_reaches
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    snap = _snapshot(tmp_path)
    root = tmp_path / "root"
    all_cohorts_dir(root).mkdir(parents=True)
    monkeypatch.setattr(config, "require", lambda key: root)
    live = all_cohorts_dir(root)
    spelled = live.parent / "other" / ".." / live.name
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        code = odc_reaches.main(["--snapshot-dir", str(snap), "--out-dir", str(spelled)])
    finally:
        holder.release()
    out = capsys.readouterr().out
    assert code == current.EXIT_LOCK_TIMEOUT, (code, out)
    assert "current-exports lock" in out, out
    assert not any(all_cohorts_dir(root).iterdir()), "it wrote without the lock"


def test_the_per_reach_command_into_a_private_folder_does_not_wait(tmp_path, monkeypatch):
    """A folder of one's own is not the live folder: no lock, no wait."""
    from mousedb import config
    from mousedb.exporters import odc_reaches
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    snap = _snapshot(tmp_path)
    root = tmp_path / "root"
    monkeypatch.setattr(config, "require", lambda key: root)
    holder = current.hold_exports_lock(lock_wait_seconds=0)
    try:
        code = odc_reaches.main(["--snapshot-dir", str(snap), "--out-dir", str(tmp_path / "mine")])
    finally:
        holder.release()
    assert code != current.EXIT_LOCK_TIMEOUT
