"""Refresh the analysis snapshot -- a safe, offline copy of connectome.db.

WHY THIS EXISTS
----------------
``connectome.db`` sits on a network share (Y:) in SQLite rollback-journal mode
(``journal_mode=delete``), not WAL -- deliberately, because WAL is unreliable
over network filesystems and would risk real corruption, not just contention.
That means a writer (the MouseReach watcher, or any node with
``also_process`` on) blocks readers outright, and a read that dies partway
through leaves nothing usable behind.

The fix used throughout mousedb is to never read the live database
from an analysis script -- read a snapshot instead
(``<snapshot_dir>/*.parquet``, see ``mousedb config --show``). Until now that snapshot
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
      "<MouseDB env>\\python.exe -m mousedb.exporters.refresh_snapshot"
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import db_path as _db_path, snapshot_dir as _snapshot_dir, require

DEFAULT_DB = _db_path()                 # None until configured
DEFAULT_SNAPSHOT_DIR = _snapshot_dir()  # None until configured

# Tables refreshed here. Add to this list rather than writing a parallel
# export path if something else needs a snapshot of another table later.
TABLES = ["pellet_scores", "reach_data", "subjects", "cohorts"]


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


def refresh(db_path: Path = None, snapshot_dir: Path = None,
           tables=TABLES, force: bool = False) -> dict:
    """Export each table in ``tables`` from ``db_path`` to
    ``snapshot_dir/{table}.parquet``, overwriting in place.

    Returns {table: row_count} for what was written. Raises RuntimeError
    without touching anything if a watcher is active, UNLESS ``force`` --
    which exists for exactly one caller: the watcher's own single-threaded
    main loop, which blocks on this subprocess and therefore cannot be
    writing connectome.db at the same time. Anything else passing force
    reintroduces the reader-under-writer risk this guard exists for.
    """
    db_path = db_path or require("db_path")
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    if not force and watcher_running():
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


def import_sheets_first(force: bool = False) -> bool:
    """Pull the current cohort tracking sheets into connectome.db before the
    export, so a bench score entered on paper this morning is in tonight's
    snapshot without anyone remembering to run ``mousedb import``.

    Uses the same ``mousedb import --all`` path a human would (configured
    cohort_sheets_dir; that folder stays read-only -- import only READS the
    workbooks). Never raises: a failed import means the snapshot refreshes
    from the data already in the db, which is strictly better than nothing.
    Returns True if the import ran cleanly."""
    if not force and watcher_running():
        print("  [skip] import: watcher active (same guard as the export)")
        return False
    try:
        # Through sheet_sync so every cohort's outcome -- success OR the exact
        # error -- lands in the ledger the Tracking Sheets tab reads. The
        # previous 'mousedb import --all' subprocess rolled back CNT_05 every
        # hour for weeks and nobody could see it.
        from mousedb.sheet_sync import import_cohorts
        r = import_cohorts(triggered_by="hourly-refresh")
        ok = True
        for c in r.get("cohorts", []):
            if c.get("success"):
                print("  import %s: %s" % (c["cohort_id"], c.get("imported")))
            else:
                ok = False
                print("  [warn] import %s FAILED: %s" % (c["cohort_id"], c.get("error")))
        if r.get("problem"):
            print("  [warn] %s" % r["problem"])
            ok = False
        return ok
    except Exception as e:
        print("  [warn] import failed (%s); snapshotting existing db data" % e)
        return False


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--import-sheets", action="store_true",
                    help="Run 'mousedb import --all' first, so newly-entered "
                         "bench scores land in this refresh (skipped safely "
                         "if unconfigured or a watcher is active).")
    ap.add_argument("--force", action="store_true",
                    help="Skip the watcher-running guard. ONLY for the "
                         "watcher's own main loop, which blocks on this "
                         "process and so cannot be writing concurrently. "
                         "Also vouches the database is readable for the "
                         "per-cohort ODC session exports.")
    args = ap.parse_args(argv)

    print("Refreshing analysis snapshot at %s" % datetime.now().isoformat(timespec="seconds"))
    if args.import_sheets:
        import_sheets_first(force=args.force)
    # A running watcher makes refresh() raise, and that aborts this whole
    # run: nothing below may read the database while a writer is active.
    counts = refresh(force=args.force)
    snapshot_ok = True
    print("Done: %s" % ", ".join("%s=%d" % kv for kv in counts.items()))

    # The snapshot is for code; the CURRENT EXPORTS are for people ("where is
    # my data?"). Rewrite them from the snapshot just taken.
    #
    # WHY db_ok follows the snapshot and not --force: the per-cohort
    # ODC_sessions_*.csv files are the one export that needs the live
    # database. refresh() has just read that database safely -- it refused to
    # start if a watcher was running -- so after a successful snapshot the
    # database IS readable. The hourly scheduled task runs without --force,
    # and tying db_ok to --force meant those files were never refreshed and
    # MANIFEST.json said odc_sessions_refreshed=False every hour (found
    # 2026-08-28). The guard is asked once more because the snapshot can take
    # a while and a watcher may have started since; --force still overrides
    # (that caller vouches nothing else can be writing).
    db_ok = args.force or (snapshot_ok and not watcher_running())
    if not db_ok:
        print("  [!] a watcher started after the snapshot; the ODC session "
              "exports are left from the previous refresh")
    try:
        from mousedb.exporters.current import refresh_current
        m = refresh_current(db_ok=db_ok)
        print("Current exports: %d files, complete=%s%s" % (
            len(m["files"]), m["complete"],
            ("; problems: " + " | ".join(m["problems"])) if m["problems"] else ""))
    except Exception as e:
        print("  [warn] current exports not refreshed: %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
