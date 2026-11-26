"""Which refresh of the analysis snapshot a reader is looking at -- one, never two.

WHY THIS EXISTS
----------------
The snapshot is a folder of parquet files, one per database table, rewritten
by the refresh job (``exporters.refresh_snapshot``). The job would otherwise replace
them one at a time as it read each table. A refresh stopped part way -- a
locked database, the scheduler's time limit, a person ending it -- left the
first tables from tonight and the rest from an earlier night: reaches labeled
by one night's phase rules joined with animals, weights and pellets from
another. Nothing in the folder said so, and every reader (exports, the data
browser, the MouseReach Video Processing Progress tab, the bench check, analysis scripts) read
the mix as if it were one snapshot.

HOW IT WORKS NOW
----------------
1. The refresh writes every table into a staging folder beside the snapshot
   (``_snapshot_staging``) first. Nothing a reader sees changes while the
   database is being read, however long that takes.
2. When all tables are written, it writes the GENERATION STAMP
   (``SNAPSHOT_GENERATION.json``) saying ``"state": "swapping"``, moves every
   staged table onto its final name (renames on the same disk: seconds), and
   then rewrites the stamp saying ``"state": "complete"``, with the generation
   id and, for each table, its row count, size, modification time and SHA-256.
3. A reader checks the stamp before and after it reads (``read_consistent``).
   Same generation, ``complete``, and every table file still matching its
   recorded size and time: everything it read came from that one generation.
   Otherwise it reads again, or -- when the snapshot really is mixed -- says
   so instead of returning mixed data.

STATES (``state()``)
--------------------
``complete``  one generation, every listed table matches the stamp.
``swapping``  a refresh is moving tables into place right now (seconds); wait.
``broken``    a refresh stopped while moving tables into place: the folder
              holds two generations. Whole again after the next successful
              refresh.
``mixed``     the stamp says complete, but a table file no longer matches it
              (it was replaced outside a refresh, e.g. by an older version of
              this tool). Treated like ``broken``.
``legacy``    no stamp: a snapshot written before generations existed. It
              cannot be checked; readers say so and carry on.

FOR A SCRIPT THAT READS THE SNAPSHOT
------------------------------------
    from mousedb.snapshot_generation import read_consistent
    (rd, ps), gen = read_consistent(snap, lambda: (pd.read_parquet(snap / "reach_data.parquet"),
                                                   pd.read_parquet(snap / "pellet_scores.parquet")))

``gen`` is the generation id (record it with your results); ``read_consistent``
raises ``SnapshotNotConsistent`` rather than hand back a mix. A script that
reads the files any other way can do the same check by hand: read
``SNAPSHOT_GENERATION.json`` before and after, and compare.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Tuple, TypeVar

T = TypeVar("T")

STAMP_NAME = "SNAPSHOT_GENERATION.json"
STAGING_NAME = "_snapshot_staging"

# How long a reader waits for a swap in progress. The swap itself takes
# seconds; it can take longer only while another program holds a table file
# open (the swap waits up to two minutes per file for it to let go).
SWAP_WAIT_SECONDS = 15 * 60


class SnapshotNotConsistent(RuntimeError):
    """The snapshot folder holds more than one generation, or kept changing
    while it was read. The message says what to do."""


# ------------------------------------------------------------------- stamp

def stamp_path(snapshot_dir) -> Path:
    return Path(snapshot_dir) / STAMP_NAME


def read_stamp(snapshot_dir) -> Optional[dict]:
    """The generation stamp, or None when there is none (or it is unreadable
    -- treated the same as none, never as "complete")."""
    try:
        return json.loads(stamp_path(snapshot_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_stamp(snapshot_dir, stamp: dict, wait_seconds: float = 120) -> None:
    """Replace the stamp whole (write beside, then rename), waiting while a
    reader has the old one open -- Windows refuses to replace an open file."""
    final = stamp_path(snapshot_dir)
    tmp = final.with_name(final.name + ".tmp")
    tmp.write_text(json.dumps(stamp, indent=1, sort_keys=True), encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            os.replace(tmp, final)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def file_facts(path: Path) -> Dict[str, int]:
    st = Path(path).stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def new_generation_id() -> str:
    """Sortable by time, unique even for two refreshes in the same second."""
    return "%s-%s" % (datetime.now().strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:8])


def _writer() -> dict:
    return {"pid": os.getpid(), "host": socket.gethostname().strip().lower(),
            "started": datetime.now().isoformat(timespec="seconds")}


def _writer_alive(writer: Optional[dict]) -> bool:
    """Is the process that wrote a "swapping" stamp still running? A writer on
    another computer cannot be checked and is assumed alive (its swap takes
    seconds; a reader then waits, which is the safe direction)."""
    if not writer:
        return False
    if str(writer.get("host", "")).lower() != socket.gethostname().strip().lower():
        return True
    try:
        import psutil
        pid = int(writer.get("pid"))
        if not psutil.pid_exists(pid):
            return False
        started = datetime.fromisoformat(str(writer.get("started"))).timestamp()
        return psutil.Process(pid).create_time() <= started + 2
    except Exception:
        return True


# ------------------------------------------------------------------- state

def state(snapshot_dir) -> Tuple[str, Optional[dict], str]:
    """(state, stamp, explanation) -- see the module docstring for the states."""
    stamp = read_stamp(snapshot_dir)
    if stamp is None:
        return "legacy", None, ("the snapshot has no generation stamp (%s); it was "
                                "written before generations were recorded, so whether "
                                "all its tables come from one refresh cannot be checked"
                                % STAMP_NAME)
    gen = stamp.get("generation", "?")
    if stamp.get("state") == "swapping":
        if _writer_alive(stamp.get("writer")):
            return "swapping", stamp, "generation %s is being moved into place now" % gen
        return "broken", stamp, (
            "the refresh that was moving generation %s into place stopped part way, so "
            "the snapshot holds tables from two refreshes. It is whole again after the "
            "next successful snapshot refresh" % gen)
    if stamp.get("state") != "complete":
        return "broken", stamp, "the generation stamp is in an unknown state %r" % stamp.get("state")
    for table, facts in (stamp.get("tables") or {}).items():
        f = Path(snapshot_dir) / ("%s.parquet" % table)
        try:
            now = file_facts(f)
        except OSError:
            return "mixed", stamp, ("%s.parquet of generation %s is missing" % (table, gen))
        if now["size"] != facts.get("size") or now["mtime_ns"] != facts.get("mtime_ns"):
            return "mixed", stamp, (
                "%s.parquet was replaced after generation %s was written (not by a "
                "snapshot refresh), so the snapshot no longer holds one generation. It "
                "is whole again after the next successful snapshot refresh" % (table, gen))
    return "complete", stamp, "generation %s" % gen


def wait_while_swapping(snapshot_dir, wait_seconds: float = SWAP_WAIT_SECONDS,
                        poll: float = 1.0, progress: Optional[Callable[[str], None]] = None):
    """Wait out a swap in progress; returns the state that followed it."""
    deadline = time.monotonic() + wait_seconds
    said = False
    while True:
        st = state(snapshot_dir)
        if st[0] != "swapping":
            return st
        if time.monotonic() >= deadline:
            raise SnapshotNotConsistent(
                "the snapshot has been in the middle of being replaced for over %d "
                "minutes (%s). Try again later." % (wait_seconds // 60, st[2]))
        if not said and progress:
            progress("The snapshot is being replaced right now; waiting a moment...")
            said = True
        time.sleep(poll)


def read_consistent(snapshot_dir, read: Callable[[], T], attempts: int = 3,
                    wait_seconds: float = SWAP_WAIT_SECONDS,
                    allow_legacy: bool = True) -> Tuple[T, Optional[str]]:
    """Run ``read()`` so that everything it reads comes from ONE generation.

    Returns (what read returned, generation id -- None for a legacy snapshot).
    Raises SnapshotNotConsistent when the snapshot is broken or mixed, or kept
    changing under the reader ``attempts`` times running.
    """
    last = ""
    for _ in range(max(1, attempts)):
        before, stamp, why = wait_while_swapping(snapshot_dir, wait_seconds)
        if before in ("broken", "mixed"):
            raise SnapshotNotConsistent("the snapshot is not consistent: %s." % why)
        if before == "legacy":
            if not allow_legacy:
                raise SnapshotNotConsistent("the snapshot is not consistent: %s." % why)
            return read(), None
        result = read()
        after, stamp2, why2 = state(snapshot_dir)
        if after == "complete" and stamp2.get("generation") == stamp.get("generation"):
            return result, stamp.get("generation")
        last = why2
    raise SnapshotNotConsistent(
        "the snapshot kept changing while it was read (%s); try again once the "
        "refresh has finished." % last)


# ------------------------------------------------------------------- writer

def publish(snapshot_dir, staging_dir, rows: Dict[str, int],
            swap: Callable[[Path, Path], None],
            read_started: Optional[str] = None,
            log: Callable[[str], None] = lambda m: print(m, flush=True)) -> dict:
    """Move every staged table into the snapshot as ONE new generation.

    ``rows``: {table: row count} of the tables in ``staging_dir`` (as
    ``<table>.parquet``). ``swap(src, dst)`` moves one file into place (the
    refresh passes its reader-tolerant ``swap_in``). Hashes are taken BEFORE
    the swap starts, so the window in which the folder is between generations
    is only the renames. Returns the complete stamp.
    """
    snapshot_dir, staging_dir = Path(snapshot_dir), Path(staging_dir)
    tables = list(rows)
    hashes = {t: sha256_of(staging_dir / ("%s.parquet" % t)) for t in tables}
    previous = read_stamp(snapshot_dir) or {}
    gen = new_generation_id()
    writer = _writer()
    write_stamp(snapshot_dir, {"generation": gen, "state": "swapping", "writer": writer,
                               "previous_generation": previous.get("generation"),
                               "tables": sorted(tables)})
    log("  moving generation %s into place (%d tables) ..." % (gen, len(tables)))
    for t in tables:
        swap(staging_dir / ("%s.parquet" % t), snapshot_dir / ("%s.parquet" % t))
    info = {}
    for t in tables:
        info[t] = dict(file_facts(snapshot_dir / ("%s.parquet" % t)),
                       rows=int(rows[t]), sha256=hashes[t])
    stamp = {"generation": gen, "state": "complete", "writer": writer,
             "previous_generation": previous.get("generation"),
             "read_started": read_started,
             "published": datetime.now().isoformat(timespec="seconds"),
             "tables": info}
    write_stamp(snapshot_dir, stamp)
    log("  snapshot generation %s complete" % gen)
    return stamp


def table_hash(snapshot_dir, table: str) -> Optional[str]:
    """SHA-256 of one table's file: from the stamp when the file still matches
    it (no need to read gigabytes again), else computed. None when absent."""
    f = Path(snapshot_dir) / ("%s.parquet" % table)
    if not f.exists():
        return None
    stamp = read_stamp(snapshot_dir) or {}
    rec = (stamp.get("tables") or {}).get(table)
    if stamp.get("state") == "complete" and rec and rec.get("sha256"):
        try:
            now = file_facts(f)
            if now["size"] == rec.get("size") and now["mtime_ns"] == rec.get("mtime_ns"):
                return rec["sha256"]
        except OSError:
            pass
    return sha256_of(f)


def generation_of(snapshot_dir) -> Optional[str]:
    st = read_stamp(snapshot_dir)
    return st.get("generation") if st else None


def tables_in(stamp: Optional[dict]) -> Iterable[str]:
    return list(((stamp or {}).get("tables") or {}))
