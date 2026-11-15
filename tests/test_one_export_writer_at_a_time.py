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
    threading.Timer(0.3, holder.release).start()
    m = current.refresh_current(snap, out, db_ok=False, lock_wait_seconds=30)
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
