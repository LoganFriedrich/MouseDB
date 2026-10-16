"""Read a database file once, sequentially, and keep it open while a job scans it.

WHY THIS EXISTS
---------------
SQLite reads a table page by page, 4 KB per request, each one a round trip.
When the database lives on a network share and the file is not in the
operating system's cache, that is slow in a way nothing else in the job is:
measured on the lab's share, 0.4 MB/s cold against 14-18 MB/s once the file
had been read once. A full scan of a 5 GB table -- what the snapshot refresh
does for every table, and what re-labelling test phases does for every
cohort -- is three hours cold and six minutes warm.

A plain sequential read in large chunks fills that cache at the share's real
speed (tens of MB/s), so the scan that follows runs from memory. Two details
make it work, both learned the hard way on 2026-10-16:

* The cache is dropped when the LAST handle on the file closes (the network
  redirector purges on final close). Warming the file in one process and
  scanning it in the next gained nothing: the refresh read cold again. So
  this is a context manager: it reads, then HOLDS the handle open for as long
  as the job inside the ``with`` runs.
* SQLite locks a 512-byte region at offset 1 GiB; a read overlapping it may be
  refused while a writer holds a lock. That chunk is skipped, not fatal.

Reading the file takes no SQLite lock and does not interfere with a writer.
On a local disk it costs seconds and changes nothing; set
``MOUSEDB_PREFETCH=0`` to turn it off anyway.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

CHUNK = 8 * 1024 * 1024


def enabled() -> bool:
    return os.environ.get("MOUSEDB_PREFETCH", "1").strip() not in ("0", "false", "no", "off")


@contextmanager
def warmed(path, log: Optional[Callable[[str], None]] = None, chunk: int = CHUNK):
    """``with warmed(db_path, log=print): ...scan the database...``

    Reads the whole file once, says so through ``log`` (if given) with the
    speed it managed, and keeps the handle open until the block ends. A
    missing file, a disabled prefetch, or any failure to open just yields
    without doing anything: the scan then runs as it would have anyway.
    """
    path = Path(path)
    if not enabled() or not path.is_file():
        yield
        return
    try:
        f = open(path, "rb", buffering=0)
    except OSError:
        yield
        return
    try:
        t0 = time.time()
        done = 0
        skipped = 0
        while True:
            try:
                b = f.read(chunk)
            except OSError:
                skipped += 1
                done += chunk
                try:
                    f.seek(done)
                except OSError:
                    break
                continue
            if not b:
                break
            done += len(b)
        if log:
            el = time.time() - t0
            log("  read %s once (%.0f MB in %.0f s, %.1f MB/s) so the table scans run from memory%s"
                % (path.name, done / 1e6, el, done / 1e6 / max(el, 0.001),
                   "; %d locked chunk(s) skipped" % skipped if skipped else ""))
        yield
    finally:
        f.close()
