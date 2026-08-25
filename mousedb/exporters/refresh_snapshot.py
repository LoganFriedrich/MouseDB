"""Refresh the analysis snapshot -- a safe, offline copy of connectome.db.

WHY THIS EXISTS
----------------
``connectome.db`` sits on a network share (Y:) in SQLite rollback-journal mode
(``journal_mode=delete``), not WAL -- deliberately, because WAL is unreliable
over network filesystems and would risk real corruption, not just contention.
That means a writer (the MouseReach watcher, or any node with
``also_process`` on) blocks readers outright, and a read that dies partway
through leaves nothing usable behind.

The fix used throughout ``mousedb.recipes`` is to never read the live database
from an analysis script -- read a snapshot instead
(``C:/LAB_ROOT/_analysis_snapshot/*.parquet``). Until now that snapshot
was a one-time file from 2026-08-20 with no refresh mechanism anywhere in
either repo, so anything reading it never saw a bench score entered after
that date. This script is that mechanism -- run it periodically (Windows Task
Scheduler; see the module docstring bottom for the exact command) to keep the
snapshot from going permanently stale.

SAFETY
------
Refuses to run while a watcher is active anywhere on this pipeline
(``watcher_running()``, the same check every recipe in this package already
uses) -- the risk this script is designed around is exactly the one a
concurrent write would reintroduce. Reads with ``mode=ro`` and a generous
timeout, same as ``manual_scoring_accuracy.shared.load_paired_pellets``.

USAGE
-----
    python -m mousedb.exporters.refresh_snapshot
    mousedb-refresh-snapshot

Windows Task Scheduler, hourly, running as the same user the watcher would
run as:
    schtasks /Create /SC HOURLY /TN "MouseDB Snapshot Refresh" /TR ^
      "C:\\LAB_ROOT\\envs\\MouseDB\\python.exe -m mousedb.exporters.refresh_snapshot"
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DEFAULT_DB = Path(r"Y:\LAB_ROOT\Databases\connectome.db")
DEFAULT_SNAPSHOT_DIR = Path("C:/LAB_ROOT/_analysis_snapshot")

# Tables refreshed here. Add to this list rather than writing a parallel
# export path if something else needs a snapshot of another table later.
TABLES = ["pellet_scores", "reach_data"]


def watcher_running() -> bool:
    """Is a MouseReach watcher running anywhere this process can see?

    Fails SAFE: if this cannot be determined it answers True, because wrongly
    saying "no" risks a read colliding with a live writer, while wrongly
    saying "yes" only means skipping a refresh cycle.
    """
    try:
        import psutil
    except ImportError:
        return True
    try:
        for proc in psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "mousereach-watch" in cmd or "mousereach.watcher" in cmd:
                return True
        return False
    except Exception:
        return True


def refresh(db_path: Path = DEFAULT_DB, snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR,
           tables=TABLES) -> dict:
    """Export each table in ``tables`` from ``db_path`` to
    ``snapshot_dir/{table}.parquet``, overwriting in place.

    Returns {table: row_count} for what was written. Raises RuntimeError
    without touching anything if a watcher is active.
    """
    if watcher_running():
        raise RuntimeError(
            "Refusing to read connectome.db: a MouseReach watcher is active. "
            "The database is on a network share in rollback-journal mode, so "
            "a writer blocks readers outright and a read that dies partway "
            "through leaves a corrupt snapshot behind. Try again once the "
            "watcher is idle.")

    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=180)
    counts = {}
    try:
        for table in tables:
            df = pd.read_sql("SELECT * FROM %s" % table, con)
            out = snapshot_dir / ("%s.parquet" % table)
            df.to_parquet(out, index=False)
            counts[table] = len(df)
            print("  %s: %d rows -> %s" % (table, len(df), out))
    finally:
        con.close()

    return counts


def main():
    print("Refreshing analysis snapshot at %s" % datetime.now().isoformat(timespec="seconds"))
    counts = refresh()
    print("Done: %s" % ", ".join("%s=%d" % kv for kv in counts.items()))


if __name__ == "__main__":
    main()
