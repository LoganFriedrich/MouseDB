"""Refresh the analysis snapshot -- a safe, offline copy of connectome.db.

WHY THIS EXISTS
----------------
``connectome.db`` is normally kept on a network share, in SQLite
rollback-journal mode (``journal_mode=delete``) rather than WAL -- deliberately,
because WAL is unreliable over network filesystems and would risk real
corruption, not just contention. That means a writer (the MouseReach watcher,
or any node with ``also_process`` on) blocks readers outright, and a read that
dies partway through leaves nothing usable behind.

The fix used throughout mousedb is to never read the live database
from an analysis script -- read a snapshot instead
(``<snapshot_dir>/*.parquet``, see ``mousedb config --show``). This script is
what keeps that snapshot current: run it periodically (through the operating
system's scheduler; see the bottom of this docstring for the command) or the
snapshot goes permanently stale and every analysis quietly works from the day
it was first taken.

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

On a schedule, as the same user the watcher runs as (on Windows):
    schtasks /Create /SC DAILY /ST 00:00 /TN "MouseDB Snapshot Refresh" /TR ^
      "<MouseDB env>\\python.exe -m mousedb.exporters.refresh_snapshot"

How OFTEN is an installation's choice, not a property of this script: a full
refresh is proportional to the size of the database and the speed of the share,
and can take over an hour. Time one run, then schedule it with room to spare --
see INSTALL.md, "Scheduled jobs".
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import (at_import as _at_import, db_path as _db_path,
                      snapshot_dir as _snapshot_dir, require)

DEFAULT_DB = _at_import(_db_path)                 # None until configured
DEFAULT_SNAPSHOT_DIR = _at_import(_snapshot_dir)  # None until configured

# Tables refreshed here. Add to this list rather than writing a parallel
# export path if something else needs a snapshot of another table later.
# "weights" was missing until 2026-10-14, and with it the only per-session
# measurement the lab records by hand for every animal on every testing day: body
# weight in grams and as a percentage of free-feeding weight. The exports read
# this snapshot, so a table absent from here is absent from every export that
# reads it, however complete the database is. Food restriction state is not a
# small covariate in a task where the animal works for food.
TABLES = ["pellet_scores", "reach_data", "subjects", "cohorts", "animal_records",
          "weights", "video_pellets",
          "projects", "surgeries", "brain_samples", "region_counts",
          "elife_region_counts"]
# "video_pellets" (2026-10-16): one row per pellet the pipeline cut each video
# into, reached for or not. Without it the exports could only count pellets that
# had a reach, and "pellets presented" came out below 20 in most sessions.
#
# "projects" .. "elife_region_counts" (2026-11-12): read by the data browser
# (mousedb.data_browser), which shows the snapshot and NEVER the live
# database -- so a table missing from here is a table nobody can browse. They
# are small (one row per project, surgery, brain, and brain region), so they
# add seconds to a refresh. No export reads them; detected_cells (one row per
# cell) is left out on purpose because of its size.


def watcher_blocks_db() -> bool:
    """Block only when the running watcher can WRITE the central database
    (central_db configured in ~/.mousereach/config.json). A DLC-only watcher
    must not stall the hourly snapshot forever. See bench_scan.watcher_blocks_db."""
    from mousedb.bench_scan import watcher_blocks_db as _wb
    return _wb()


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


# How long a swap waits for readers of the old file to let go. Readers of the
# snapshot (the Where Is My Data tab, the bench scan, the data browser making
# its private copy) hold a table file for seconds, so two minutes is generous;
# a reader that holds one longer than that is a defect to find, and the job
# still fails loudly rather than waiting forever.
SWAP_WAIT_SECONDS = 120

# How long this job waits for the cross-task lock while another job holds it
# and is still working (task_mutex: "HOW LONG A JOB WAITS"). Longer than the
# other jobs' default because this is the job that runs after the imports, so
# it is the one most often queued behind a long import; and a refresh that
# gives up is a whole day without new exports, since the scheduler does not
# re-run a job that started and failed. Size it so the worst case fits the
# task's time limit (INSTALL.md): lock wait + the prefetch + the refresh that
# met an outside database lock + the retry window + the refresh that succeeds
# + the wait for the exports lock (EXPORTS_LOCK_WAIT_MINUTES) + exports and
# census. That is: the time limit, minus the retry window, minus the prefetch,
# minus TWO refreshes, minus the exports-lock wait, minus exports and census.
# Sizing it as "the limit minus one successful refresh" leaves no room for a
# retry, and the scheduler then stops the job part way through rewriting the
# exports. (It was 180 before the exports lock existed; the exports-lock wait
# was taken out of it, so the worst case of the whole job did not grow.)
DEFAULT_LOCK_WAIT_MINUTES = 165

# How long this job waits for ANOTHER refresh of the current exports (a
# "Refresh exports now" press, a terminal command) to finish before it writes
# them itself. A bound of its own, deliberately short: without one it reused
# the job's database-lock budget above, so a button press late in the evening
# could add up to that many minutes again to a job sized without them -- and
# the scheduler would stop the job part way through the exports, leaving one
# night's cohort files beside another night's combined files and manifest.
# Giving up is not free, and this job says [FAIL] so the night is not silent:
# this job reaches its exports only AFTER it has replaced the snapshot, so a
# refresh still writing then almost always started from the PREVIOUS
# snapshot. It ends with the previous snapshot's data, or (the snapshot having
# been replaced under it) with complete=false in MANIFEST.json -- either way
# the night's imports are not in the exports until someone presses "Refresh
# exports now" after it finishes, or the next nightly run writes them. The
# operator docs (WHERE_IS_MY_DATA.md) say so. The bound stays short anyway:
# a late night costs one day's freshness, a stopped job costs a mixed folder.
EXPORTS_LOCK_WAIT_MINUTES = 15


def swap_in(tmp: Path, out: Path, wait_seconds: float = None) -> None:
    """Move ``tmp`` onto ``out``, waiting while a reader has ``out`` open.

    WHY: on Windows, replacing a file fails ("Access is denied") while ANY
    program has it open -- whatever sharing that program asked for (tested
    2026-11-12: even a reader that opened it with delete sharing blocks the
    replace). Snapshot readers open these files at any time of day. Without a
    wait, a person refreshing the Where Is My Data tab, or opening the data
    browser, at the moment of the swap would fail the whole nightly refresh.
    A reader lets go within seconds, so the swap retries once a second.
    """
    wait_seconds = SWAP_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + wait_seconds
    said = False
    while True:
        try:
            os.replace(tmp, out)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            if not said:
                print("  %s is open in another program; waiting up to %d s for it "
                      "to let go ..." % (out.name, wait_seconds), flush=True)
                said = True
            time.sleep(1)


def _refuse_while_watcher_runs(force: bool) -> None:
    """Raise RuntimeError while a MouseReach watcher is active (unless force;
    see refresh)."""
    if not force and watcher_blocks_db():
        raise RuntimeError(
            "Refusing to read connectome.db: a MouseReach watcher is active. "
            "The database is on a network share in rollback-journal mode, so "
            "a writer blocks readers outright and a read that dies partway "
            "through leaves a corrupt snapshot behind. Try again once the "
            "watcher is idle.")


def refresh(db_path: Path = None, snapshot_dir: Path = None,
           tables=TABLES, force: bool = False, hold_lock: bool = True) -> dict:
    """Export each table in ``tables`` from ``db_path`` to
    ``snapshot_dir/{table}.parquet`` as ONE new generation.

    Every table is written to a staging folder first; only when all of them
    are written are they moved into place together and the generation stamp
    written (mousedb.snapshot_generation). A run that stops while reading the
    database leaves the previous generation untouched.

    Returns {table: row_count} for what was written. Raises RuntimeError
    without touching anything if a watcher is active, UNLESS ``force`` --
    which exists for exactly one caller: the watcher's own single-threaded
    main loop, which blocks on this subprocess and therefore cannot be
    writing connectome.db at the same time. Anything else passing force
    reintroduces the reader-under-writer risk this guard exists for.

    ``hold_lock`` False: the caller already holds the cross-task mutex for
    the whole job (``main`` does, across its retries -- see there).
    """
    db_path = db_path or require("db_path")
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    _refuse_while_watcher_runs(force)

    snapshot_dir = Path(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    from .. import snapshot_generation as generation

    # Under the cross-task mutex: these SELECT * reads are exactly the long
    # SHARED locks that put a concurrently-committing import into PENDING
    # starvation (22 minutes of "database is locked" for every reader,
    # 2026-09-02). Serialized, this runs the moment the import finishes.
    import contextlib
    from ..task_mutex import hold
    counts = {}
    with (hold(waiting_for="the reach/brain import") if hold_lock
          else contextlib.nullcontext()):
        # Staging is emptied under the mutex, so two refreshes never share it.
        # Leftover per-table ``.tmp`` files from versions that swapped one
        # table at a time are removed too: the data browser reads one as "a
        # refresh is writing now" and would wait for it.
        import shutil
        staging = snapshot_dir / generation.STAGING_NAME
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for orphan in snapshot_dir.glob("*.parquet.tmp"):
            try:
                orphan.unlink()
            except OSError:
                pass
        read_started = datetime.now().isoformat(timespec="seconds")
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=600)
        try:
            for table in tables:
                # flush=True: the scheduled task sends this output to a log
                # file, where Python buffers it until the process ends -- a
                # run that took an hour on a busy share looked exactly like a
                # hung one (only its header in the log), and one was stopped
                # by hand for that reason (2026-09-14).
                # A table newer than this database (animal_records before the first
                # sheet import that creates it) is skipped, not a failed snapshot.
                if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                   (table,)).fetchone():
                    print("  %s: not in this database yet; skipped" % table, flush=True)
                    continue
                print("  reading %s ..." % table, flush=True)
                df = pd.read_sql("SELECT * FROM %s" % table, con)
                # Into staging, not onto the final name. WHY: readers open
                # these files at any time, and the task's time limit or a
                # person can stop this process at any moment. Swapping each
                # table in as it was read left a folder of some new tables and
                # some old ones whenever a run stopped part way -- a mix no
                # reader could detect. Now nothing a reader sees changes until
                # every table is written.
                staged = staging / ("%s.parquet" % table)
                df.to_parquet(staged, index=False)
                counts[table] = len(df)
                print("  %s: %d rows read" % (table, len(df)), flush=True)
                del df
        finally:
            con.close()
        # Still under the mutex, so two refreshes never interleave their
        # swaps. The swap is renames on one disk: seconds, not the hour the
        # reads above take.
        generation.publish(snapshot_dir, staging, counts, swap=swap_in,
                           read_started=read_started)
        shutil.rmtree(staging, ignore_errors=True)

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
    if not force and watcher_blocks_db():
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
    ap.add_argument("--skip-census", action="store_true",
                    help="Do not retake the pipeline census after the exports "
                         "(it is a 5-25 minute read-only scan of the pipeline "
                         "folders). The nightly job leaves it on.")
    ap.add_argument("--lock-wait-minutes", type=float, default=None,
                    help="How long to wait for another mousedb job (an import) "
                         "that holds the database-task lock and is still working "
                         "(default %d). Set it so the worst case fits this task's "
                         "time limit: the limit, minus --retry-minutes, minus "
                         "reading the database file once to warm the cache, "
                         "minus two refreshes (the one that met another "
                         "program's lock and the retry that succeeds), minus "
                         "the %d-minute wait for another refresh of the current "
                         "exports, minus the exports and census (INSTALL.md). "
                         "The environment "
                         "variable MOUSEDB_LOCK_WAIT_MINUTES overrides it."
                         % (DEFAULT_LOCK_WAIT_MINUTES, EXPORTS_LOCK_WAIT_MINUTES))
    ap.add_argument("--retry-minutes", type=int, default=25,
                    help="How long to keep retrying if another program has the "
                         "database locked (default 25), counted from the first "
                         "time it is found locked. A retry restarts this "
                         "whole refresh, so set this to the gap between this job "
                         "and the next one on your schedule, minus how long a "
                         "successful refresh takes. 0 disables retrying.")
    args = ap.parse_args(argv)
    from .. import task_mutex
    task_mutex.set_wait_minutes(DEFAULT_LOCK_WAIT_MINUTES if args.lock_wait_minutes is None
                                else args.lock_wait_minutes)

    print("Refreshing analysis snapshot at %s" % datetime.now().isoformat(timespec="seconds"),
          flush=True)
    if args.import_sheets:
        import_sheets_first(force=args.force)
    # A running watcher makes refresh() raise, and that aborts this whole
    # run: nothing below may read the database while a writer is active.
    #
    # The retry wrapper is for the OTHER kind of blockage: a program this tool
    # does not control holding a database lock (see db_contention). The
    # cross-task mutex above cannot help there, and without a retry one locked
    # moment cost a whole day's refresh -- three days running, at which point
    # every number anybody looked at was three days old and nothing said so.
    # Re-reading from the start is safe: a refresh that could not begin wrote
    # nothing, and each table is swapped into place whole.
    #
    # A SHORT window on purpose, unlike the other jobs. Two reasons, both
    # measured rather than guessed:
    #
    #  * This job is the long one. A successful refresh of a multi-gigabyte
    #    database over a network share took 67 minutes on the machine this was
    #    written for. A retry restarts it from the first table, so each extra
    #    attempt costs another full run, and a generous window here would leave
    #    the job still going when the next scheduled job starts -- the exact
    #    overlap the mutex exists to prevent.
    #  * A retry only ever rescues a SHORT lock. The locks that actually broke
    #    this job lasted over an hour, and no window anybody would accept would
    #    have outlived them. What catches those is the snapshot's age being
    #    reported and flagged (see data_status.STALE_SNAPSHOT_HOURS), not more
    #    waiting here.
    #
    # Size it to the clear air between this job and the next one on YOUR
    # schedule, minus how long a successful run takes.
    from ..db_contention import (DEFAULT_GAP_MINUTES, DatabaseBusy,
                                 retry_while_locked)
    # Everything below scans the database file: the table reads here and the
    # per-cohort session exports in refresh_current. Read the file once
    # sequentially first and keep it open until the end, so those scans run
    # from the operating system's cache instead of 4 KB at a time over the
    # share (3 hours cold against minutes warm; see mousedb.prefetch).
    from ..prefetch import warmed
    #
    # The cross-task mutex is taken HERE, once, around every attempt -- not by
    # each attempt. WHY: refresh() used to take it itself, so a retry queued
    # for it all over again; an import that slipped in during the pause could
    # make the retry wait the whole --lock-wait-minutes a second time, and the
    # job overran its time limit part way through rewriting the exports. Held
    # across the retries, the job's time is bounded by
    #     lock wait + prefetch + up to two runs + retry window
    #     + exports-lock wait (EXPORTS_LOCK_WAIT_MINUTES) + exports
    # (the run that met the outside lock, then the retry that succeeds;
    # INSTALL.md). Keeping it during a pause makes this tool's other jobs wait
    # longer, and one waiting behind a pause of over STALL_MINUTES may give up
    # on it as "may be hung" -- but only while an outside program keeps the
    # database locked, which would refuse that job's own reads as well.
    #
    # The prefetch runs INSIDE the lock: warmed before a wait of up to hours
    # behind an import that rewrites the file, the cache would be stale or
    # evicted by the time the tables are read.
    from ..task_mutex import hold
    # Refuse a running watcher BEFORE queueing for the mutex, which can take
    # hours; refresh() checks again at every attempt.
    _refuse_while_watcher_runs(args.force)
    try:
        with hold(waiting_for="the reach/brain import"), \
                warmed(require("db_path"), log=lambda m: print(m, flush=True)):
            counts = retry_while_locked(
                lambda: refresh(force=args.force, hold_lock=False),
                what="The analysis snapshot refresh",
                window_minutes=args.retry_minutes,
                gap_minutes=min(DEFAULT_GAP_MINUTES, max(1, args.retry_minutes // 3)),
                # The window opens at the first outside lock, not when this
                # run started: the reads before it (an hour on a large
                # database) used to spend the window, and so -- when
                # refresh() still took the mutex inside the attempt -- did
                # the wait for the import ahead of it, before one retry.
                from_first_lock=True,
                announce=lambda m: print(m, flush=True))
            # The per-cohort session exports read the database file too: they
            # run while it is still warm, and under the lock, so their long
            # reads cannot overlap an import's commits -- the stall this lock
            # exists to prevent. (Before the lock moved here, it was released
            # between the snapshot and these reads.)
            rc = _finish(counts, args.force)
    except DatabaseBusy as busy:
        print("  [FAIL] %s" % busy, flush=True)
        return 1
    except task_mutex.LockWaitTimeout as waited:
        # Loud and non-zero: the snapshot and the exports were NOT
        # refreshed, and nothing will re-run this job before its next
        # scheduled time (see task_mutex, "HOW LONG A JOB WAITS").
        print("  [FAIL] snapshot not refreshed: %s" % waited, flush=True)
        return 1
    # Outside the database-task lock: a limited export reads only the export
    # files and the snapshot, and building one can take minutes that the
    # imports waiting for that lock should not spend waiting.
    rc = _after_exports(rc)
    if not args.skip_census and retake_census():
        write_missing_videos_list()
    return rc


def write_missing_videos_list() -> None:
    """Rewrite the list of hand-scored sessions with no video
    (missing_videos.py) from the census just taken. Never raises and never
    changes this job's exit code, for the same reason as retake_census; a
    failure is printed as [FAIL]."""
    from mousedb.missing_videos import write_list_quietly
    said = write_list_quietly()
    print(("  [FAIL] list of sessions without a video not rewritten: %s" % said)
          if said.startswith("FAILED") else ("List of sessions without a video %s" % said),
          flush=True)


def _after_exports(rc: int) -> int:
    """This job's exit code once the saved limited exports have been remade --
    only when the full export just written is complete (``rc`` 0)."""
    if rc != 0:
        return rc
    return _remake_limited_exports()


def retake_census() -> bool:
    """Retake the pipeline census for the Where Is My Data tab, after the
    snapshot it is joined with. Never raises; True when it was retaken.

    WHY here: the census was taken only when someone pressed 'Refresh pipeline
    view', and the tab showed a census seven weeks old as its pipeline counts
    (found 2026-10-22). This job already runs nightly and the census joins with
    the snapshot just written, so it goes last. It reads pipeline folders only
    -- no database lock is held. A failure does NOT change this job's exit
    code: the scheduler would re-run the whole snapshot for it. It is printed
    as [FAIL] here, and the tab shows the census's age in red once it is over
    data_status.STALE_CENSUS_HOURS.
    """
    print("Retaking the pipeline census (read-only folder scan, 5-25 minutes)...",
          flush=True)
    try:
        from mousedb.pipeline_census import refresh as census_refresh
        c = census_refresh()
        t = c.get("totals") or {}
        print("Pipeline census taken %s: %s sessions expected, %s finished on disk "
              "(scan %ss)" % (c.get("generated_at"), t.get("expected"),
                              t.get("finished_files"), c.get("scan_seconds")), flush=True)
        return True
    except Exception as e:
        print("  [FAIL] pipeline census not retaken: %s. The Where Is My Data tab "
              "keeps the previous census and shows its age." % e, flush=True)
        return False


def _finish(counts: dict, force: bool) -> int:
    """Rewrite the current exports from the snapshot just taken. Returns this
    job's exit code: 1 when the exports could not be rewritten or came out
    incomplete (MANIFEST.json ``complete`` False), else 0.

    WHY non-zero: the exports are what people read ("where is my data?"),
    and this job is what keeps them current. A run that refreshed the
    snapshot but not the exports used to print a [warn] and exit 0, so the
    task looked healthy while ``exports/current`` stayed as it was; nothing
    that checks the scheduled jobs looks for [warn]. A failure now prints
    [FAIL] and fails the task."""
    snapshot_ok = True
    # flush: a run stopped during the exports must still show this line.
    print("Done: %s" % ", ".join("%s=%d" % kv for kv in counts.items()), flush=True)

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
    db_ok = force or (snapshot_ok and not watcher_blocks_db())
    if not db_ok:
        print("  [!] a watcher started after the snapshot; the ODC session "
              "exports are left from the previous refresh")
    try:
        from mousedb.exporters.current import refresh_current
        # Its own short bound, never the database-lock budget (see
        # EXPORTS_LOCK_WAIT_MINUTES).
        m = refresh_current(db_ok=db_ok, lock_wait_seconds=EXPORTS_LOCK_WAIT_MINUTES * 60)
    except Exception as e:
        print("  [FAIL] current exports not refreshed: %s: %s. The files in "
              "exports/current are still those of the last good refresh."
              % (type(e).__name__, e), flush=True)
        return 1
    print("Current exports: %d files, complete=%s%s" % (
        len(m["files"]), m["complete"],
        ("; problems: " + " | ".join(m["problems"])) if m["problems"] else ""), flush=True)
    for note in m.get("notes") or []:
        print("  [note] %s" % note, flush=True)
    if not m["complete"]:
        print("  [FAIL] current exports are incomplete (MANIFEST.json says complete=False; "
              "the problems are listed above and in the manifest)", flush=True)
        return 1
    return 0


def _remake_limited_exports() -> int:
    """Remake every saved limited export (exporters.subsets) from the exports
    just written. Returns 1 when one could not be made, else 0.

    WHY here, nightly: a limited export is a filtered copy of the full export,
    and a copy that is not remade goes stale the first night anything it holds
    changes (a video leaves review, a sheet row is corrected) -- while still
    looking current to whoever opens it. Remade right after the full export,
    it is never older than the full export it was cut from.

    WHY only when the full export is complete (_after_exports): a slice cut
    from a partial export would be partial too.

    WHY "if changed": each remake moves the previous slice into the archive
    (never deleted), so remaking an unchanged slice every night would fill the
    archive with identical copies. A slice whose files would come out byte for
    byte the same is kept as it is (subsets.make, if_changed). A definition can
    opt out with "nightly": false.

    Runs after the database-task lock and the exports lock are both released
    (main); each slice takes the exports lock again while it reads. A slice
    that fails prints [FAIL], is recorded in exports/subsets/SUBSETS_STATUS.json
    (shown on the Where Is My Data tab), and fails the job, because a slice
    someone relies on has not been updated. The pipeline census is still
    retaken after it."""
    try:
        from mousedb.exporters import subsets
        failed = subsets.make_saved(nightly_only=True, if_changed=True,
                                    log=lambda m: print(m, flush=True))
    except Exception as e:
        print("  [FAIL] limited exports not remade: %s: %s" % (type(e).__name__, e), flush=True)
        return 1
    if failed:
        print("  [FAIL] %d limited export(s) could not be remade (see above)" % failed, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
