"""One heavy database task at a time -- across PROCESSES.

WHY THIS EXISTS
----------------
connectome.db lives on a network share in rollback-journal mode, where a
writer that wants to commit while a long read is in flight goes PENDING --
and while it is PENDING, every NEW reader is refused. On 2026-09-02 the
hourly reach import (per-video commits, individually milliseconds) overlapped
the snapshot's long SELECT * reads, the import sat PENDING behind them, and
the database answered "locked" to everyone for 22 minutes. Neither task held
a long transaction; the OVERLAP was the outage.

The scheduled tasks are staggered by minutes, which is a hope, not a
guarantee, the moment one of them runs long. This mutex is the guarantee:
whoever gets the lockfile runs; the other waits and runs immediately after.
Serialized, the same two tasks produce lock windows of seconds.

MECHANICS: an O_CREAT|O_EXCL lockfile under <mousedb_root>/logs with pid and
timestamp inside. A crashed holder must not wedge every later hour (the
one-way-door lesson), so a lock older than ``stale_minutes`` is broken with a
message. Release deletes the file; the context manager releases on any exit.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class TaskMutex:
    def __init__(self, name: str = "central-db", stale_minutes: int = 45,
                 base_dir: Optional[Path] = None):
        if base_dir is None:
            from .config import log_path
            base_dir = log_path() or Path(".")
        base_dir = Path(base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        self.path = base_dir / (".%s.task.lock" % name)
        self.stale_seconds = stale_minutes * 60
        self._held = False

    def try_acquire(self) -> bool:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("%d %s" % (os.getpid(),
                                     datetime.now().isoformat())).encode())
            os.close(fd)
            self._held = True
            return True
        except FileExistsError:
            try:
                if time.time() - self.path.stat().st_mtime > self.stale_seconds:
                    # A crashed holder; break the stale lock rather than wedge
                    # every later hour.
                    self.path.unlink()
                    return self.try_acquire()
            except OSError:
                pass
            return False

    def acquire(self, wait_seconds: int = 1500, log=print,
                waiting_for: str = "another database task") -> "TaskMutex":
        t0 = time.time()
        said = False
        while not self.try_acquire():
            if time.time() - t0 > wait_seconds:
                raise RuntimeError(
                    "could not acquire the database-task lock after %ds "
                    "(%s still holds it: %s)"
                    % (wait_seconds, waiting_for, self.path))
            if not said:
                log("  waiting for %s to finish (lock: %s)"
                    % (waiting_for, self.path.name))
                said = True
            time.sleep(5)
        return self

    def release(self) -> None:
        if self._held:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._held = False

    def __enter__(self) -> "TaskMutex":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def hold(name: str = "central-db", wait_seconds: int = 1500, log=print,
         waiting_for: str = "another database task") -> TaskMutex:
    """Acquire (waiting if needed) and return the held mutex, for ``with``."""
    return TaskMutex(name).acquire(wait_seconds=wait_seconds, log=log,
                                   waiting_for=waiting_for)
