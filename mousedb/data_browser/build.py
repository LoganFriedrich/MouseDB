"""Build the data browser's private, read-only SQLite copy from the snapshot.

WHY A COPY (see the package docstring for the whole story): the viewer may be
left open for days, and an open file on Windows can stop the nightly snapshot
job from replacing it. So the viewer opens a file that belongs to this person
alone and that nothing else ever needs to replace.

WHERE: ``%LOCALAPPDATA%\\mousedb\\data_browser\\<stamp>\\mousedb.sqlite`` (or
``$MOUSEDB_BROWSER_CACHE``). Local application data is per person and is not
copied around with a roaming profile. The folder name is a fingerprint of the
snapshot files (name, size, time), so a new snapshot gets a new copy and an
unchanged one is reused without reading anything. Older copies are deleted once
nothing has them open.

WHAT IS LEFT OUT: ``reach_data.extended_features``, a block of about 165
numbers per reach stored as one JSON text. It is most of the snapshot's size,
and as one cell of JSON it cannot be sorted or filtered by anyone who does not
write code. The same values, one column each, are in the exports
(``reaches_<cohort>_summary.csv`` and friends; docs/WHERE_IS_MY_DATA.md).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

DB_NAME = "mousedb"                 # the name Datasette shows (/mousedb/...)
DB_FILE = DB_NAME + ".sqlite"
CACHE_ENV = "MOUSEDB_BROWSER_CACHE"

LEFT_OUT_COLUMNS = {"reach_data": ("extended_features",)}

# Tables that get a cohort_id column in the copy (the database keeps only the
# animal id on them). Described as added by the browser (site.py).
ADDED_COHORT = ("reach_data",)

# Snapshot tables the browser copies -- every table the snapshot job writes.
# Imported rather than repeated so the two lists cannot drift apart.
from ..exporters.refresh_snapshot import TABLES as SNAPSHOT_TABLES  # noqa: E402

# Indexes on the columns people filter and facet on most. Without them a click
# on a value in a 800,000-row table scans the whole table.
INDEXES = {
    "reach_data": ("subject_id", "cohort_id", "session_date", "video_name", "tray_type",
                   "segment_outcome", "test_phase"),
    "pellet_scores": ("subject_id", "session_date", "test_phase"),
    "video_pellets": ("subject_id", "video_name"),
    "weights": ("subject_id",),
}


class BrowserError(Exception):
    """Something the person can act on. The message is written for them:
    what went wrong, in plain words, and what to do about it."""


@dataclass
class BuildResult:
    path: Path                      # the private copy
    built_now: bool                 # False = an existing copy was reused
    snapshot_time: Optional[datetime]   # newest snapshot file's time
    tables: Dict[str, dict] = field(default_factory=dict)  # name -> rows, written


# --------------------------------------------------------------------------
# where things are
# --------------------------------------------------------------------------

def cache_root() -> Path:
    override = os.environ.get(CACHE_ENV)
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "mousedb" / "data_browser"
    return Path.home() / ".mousedb" / "data_browser"


def _resolved(p) -> Path:
    try:
        return Path(p).resolve()
    except OSError:
        return Path(os.path.abspath(p))


def _inside(child: Path, parent: Optional[Path]) -> bool:
    if parent is None:
        return False
    c, p = _resolved(child), _resolved(parent)
    return c == p or p in c.parents


def _live_db() -> Optional[Path]:
    from .. import config
    return config.db_path()


def check_not_live(path, what: str = "file") -> None:
    """Refuse the live database, and anything inside mousedb's own folder.

    WHY: the live database must never be held open by a viewer (package
    docstring), and nothing but mousedb's importers may write into its folder
    -- so the private copy may not be built there either. Called on the copy
    before it is built and again on the file just before it is served.
    """
    from .. import config
    live = _live_db()
    if live is not None and _resolved(path) == _resolved(live):
        raise BrowserError(
            "The data browser was asked to open the LIVE database (%s). It never "
            "does: a viewer left open on it blocks the imports. It opens a private "
            "copy of the snapshot instead. If you see this, the browser's setup is "
            "wrong -- tell whoever looks after mousedb." % live)
    root = config.mousedb_root()
    if _inside(path, root):
        raise BrowserError(
            "The data browser's %s (%s) would be inside mousedb's own folder (%s), "
            "where only mousedb's importers may write. Point %s at a folder of "
            "your own, or leave it unset." % (what, path, root, CACHE_ENV))


def check_cache_location(root: Path, snapshot_dir: Path) -> None:
    check_not_live(root, "private copy folder")
    if _inside(root, snapshot_dir):
        raise BrowserError(
            "The data browser's private copy folder (%s) is inside the snapshot "
            "folder (%s). The snapshot folder belongs to the nightly job; point %s "
            "somewhere else, or leave it unset." % (root, snapshot_dir, CACHE_ENV))


# --------------------------------------------------------------------------
# reading the snapshot without getting in the nightly job's way
# --------------------------------------------------------------------------
#
# While a file is open, Windows refuses to replace it -- whatever sharing the
# reader asked for (tested 2026-11-12, delete sharing included). So the copy
# reads each snapshot file once, for as short a time as possible (only the
# columns it keeps, straight into memory, then closed), and does not start
# while the snapshot job is in the middle of writing a table. The snapshot job,
# for its part, waits up to two minutes for a reader to let go before it
# swaps a table in (exporters.refresh_snapshot.swap_in). Between the two, a
# copy being made at the same moment as the refresh delays the refresh by
# seconds at most, and the open data browser never holds a snapshot file at all.

# How long to wait for a refresh that is writing the snapshot right now.
REFRESH_WAIT_SECONDS = 15 * 60


def refresh_in_progress(snapshot_dir: Path) -> bool:
    """The snapshot job writes each table beside its final name (``*.tmp``)
    and swaps it in; a ``.tmp`` present means a table is being written now."""
    return any(Path(snapshot_dir).glob("*.parquet.tmp"))


def wait_for_refresh(snapshot_dir: Path, progress, wait_seconds: float = None,
                     poll: float = 5.0) -> None:
    wait_seconds = REFRESH_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + wait_seconds
    said = False
    while refresh_in_progress(snapshot_dir):
        if time.monotonic() >= deadline:
            raise BrowserError(
                "The snapshot is being rewritten right now and has not finished after "
                "%d minutes. Try the data browser again later; the snapshot refresh "
                "normally finishes well within that." % (wait_seconds // 60))
        if not said:
            progress("The snapshot is being rewritten right now; waiting for it to "
                     "finish so your copy is complete (up to %d minutes)..."
                     % (wait_seconds // 60))
            said = True
        time.sleep(poll)


def snapshot_files(snapshot_dir: Path) -> Dict[str, Path]:
    return {t: Path(snapshot_dir) / ("%s.parquet" % t) for t in SNAPSHOT_TABLES
            if (Path(snapshot_dir) / ("%s.parquet" % t)).is_file()}


def fingerprint(files: Dict[str, Path]) -> str:
    h = hashlib.sha1()
    for name in sorted(files):
        st = files[name].stat()
        h.update(("%s|%d|%d;" % (name, st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()[:16]


def _read_table(path: Path, table: str):
    import pyarrow.parquet as pq
    # Opened, read and closed here, before any conversion or SQLite work, so the
    # file is held only for the read itself. Column projection skips the
    # left-out blob on disk (it is most of the file), keeping that to seconds.
    pf = pq.ParquetFile(str(path))
    try:
        cols = [c for c in pf.schema_arrow.names
                if c not in LEFT_OUT_COLUMNS.get(table, ())]
        data = pf.read(columns=cols)
    finally:
        pf.close()
    return data.to_pandas()


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------

def _make_writable(p: Path) -> None:
    try:
        os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
    except OSError:
        pass


def _remove_old_copies(root: Path, keep: Path) -> None:
    """Delete this person's previous copies. One still open in another of their
    browser windows cannot be deleted on Windows; it is left for next time."""
    for d in root.iterdir() if root.is_dir() else ():
        if not d.is_dir() or d == keep or d.name.startswith("."):
            continue
        for f in d.iterdir():
            _make_writable(f)
        shutil.rmtree(d, ignore_errors=True)


def build(snapshot_dir=None, root: Optional[Path] = None,
          progress: Callable[[str], None] = print) -> BuildResult:
    """Return a private copy of the current snapshot, building it if needed."""
    from .. import config
    from . import views

    if snapshot_dir is None:
        snapshot_dir = config.snapshot_dir()
        if snapshot_dir is None:
            raise BrowserError(
                "mousedb does not know where the snapshot is kept on this computer. "
                "Whoever looks after mousedb sets it once with:\n"
                "    mousedb config --set snapshot_dir <folder>")
    snapshot_dir = Path(snapshot_dir)
    wait_for_refresh(snapshot_dir, progress)
    files = snapshot_files(snapshot_dir)
    if "reach_data" not in files:
        raise BrowserError(
            "There is no snapshot to browse yet: %s has no reach_data.parquet.\n"
            "The snapshot is written by the scheduled snapshot refresh "
            "(mousedb-refresh-snapshot), or by \"Update the database now\" on the "
            "Where Is My Data tab. Once it has run, open the data browser again."
            % snapshot_dir)

    root = Path(root) if root is not None else cache_root()
    check_cache_location(root, snapshot_dir)
    stamp = fingerprint(files)
    folder = root / stamp
    target = folder / DB_FILE
    snap_time = datetime.fromtimestamp(max(p.stat().st_mtime for p in files.values()))

    if target.is_file():
        try:
            tables = _read_about(target)
            return BuildResult(target, False, snap_time, tables)
        except sqlite3.Error:
            _make_writable(target)
            target.unlink()          # an unreadable copy is rebuilt, not trusted

    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / ("building-%d.sqlite" % os.getpid())
    if tmp.exists():
        tmp.unlink()
    progress("Making your private copy of the snapshot (once per snapshot; "
             "usually under a minute)...")
    started = time.time()
    tables: Dict[str, dict] = {}
    con = sqlite3.connect(str(tmp))
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        for name, path in files.items():
            progress("  copying %s ..." % name)
            df = _read_table(path, name)
            if name in ADDED_COHORT and "cohort_id" not in df.columns and "subject_id" in df:
                # Stored, not computed in a view: the browser's cohort filter and
                # facet on 800,000 reaches then use an index (about a second
                # faster per click). Same rule as views._COHORT_FROM_ID.
                df.insert(1, "cohort_id", df["subject_id"].astype("string").str[:-3])
            df.to_sql(name, con, index=False, chunksize=20000)
            written = datetime.fromtimestamp(path.stat().st_mtime)
            tables[name] = {"rows": len(df), "written": written.isoformat(sep=" ",
                                                                          timespec="minutes")}
            del df
        for name, cols in INDEXES.items():
            if name not in tables:
                continue
            have = {r[1] for r in con.execute("PRAGMA table_info(%s)" % name)}
            for c in cols:
                if c in have:
                    con.execute('CREATE INDEX "ix_%s_%s" ON "%s"("%s")' % (name, c, name, c))
        progress("  making the ready-made tables ...")
        views.create(con, set(tables))
        _write_about(con, tables, snapshot_dir, snap_time)
        con.commit()
    except Exception:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()

    try:
        os.replace(tmp, target)
    except OSError:
        # Another window of this person's built the same copy first and has it
        # open: use theirs.
        tmp.unlink(missing_ok=True)
        if not target.is_file():
            raise
    # Read-only on disk as well as in the viewer: belt and braces.
    os.chmod(target, stat.S_IREAD)
    _remove_old_copies(root, folder)
    progress("  done in %d seconds." % (time.time() - started))
    return BuildResult(target, True, snap_time, tables)


ABOUT_TABLE = "about_this_copy"


def _write_about(con, tables, snapshot_dir, snap_time) -> None:
    con.execute('CREATE TABLE "%s" (table_name TEXT, rows INTEGER, '
                'snapshot_written TEXT, copy_made TEXT)' % ABOUT_TABLE)
    made = datetime.now().isoformat(sep=" ", timespec="minutes")
    con.executemany('INSERT INTO "%s" VALUES (?,?,?,?)' % ABOUT_TABLE,
                    [(n, t["rows"], t["written"], made) for n, t in sorted(tables.items())])


def _read_about(path: Path) -> Dict[str, dict]:
    con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        return {r[0]: {"rows": r[1], "written": r[2], "copy_made": r[3]}
                for r in con.execute('SELECT * FROM "%s"' % ABOUT_TABLE)}
    finally:
        con.close()
