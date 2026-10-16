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
timestamp inside. Release deletes the file; the context manager releases on
any exit.

WHEN A LOCK IS BROKEN -- and when it never is. A crashed holder must not wedge
every later job (the one-way-door lesson), so the lockfile carries the
holder's pid: if that process is gone, or the pid now belongs to a process
started after the lock was taken, the lock is dead and is broken at once.
A holder that is STILL RUNNING is never overruled, however long it has held
the lock. Until 2026-10-15 a lock older than ``stale_minutes`` was broken
regardless; the nightly reach import legitimately held it for hours while
rewriting phase labels over the share, the brain import broke in at 22:30,
and both then fought over the database -- "database is locked" for 45
minutes, nightly. The age rule survives only as the last resort for a
lockfile whose holder cannot be identified (no psutil, unreadable contents).
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


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

    # -- who holds it -------------------------------------------------------

    def holder(self) -> Tuple[str, str]:
        """(state, description) for the lockfile's holder.

        state is ``"alive"`` (a process that took this lock is still running),
        ``"dead"`` (that process is gone, or its pid was reused by a process
        started after the lock was written) or ``"unknown"`` (the lockfile
        cannot be read or the pid cannot be checked on this machine).
        """
        try:
            pid_text, taken_text = self.path.read_text().split()[:2]
            pid = int(pid_text)
            taken = datetime.fromisoformat(taken_text).timestamp()
        except Exception:
            return "unknown", "lockfile %s is unreadable" % self.path.name
        try:
            import psutil
            if not psutil.pid_exists(pid):
                return "dead", "pid %d is no longer running" % pid
            proc = psutil.Process(pid)
            # Two seconds of slack: the stamp is written moments after start.
            if proc.create_time() > taken + 2:
                return "dead", "pid %d was reused by a newer process" % pid
            try:
                cmd = " ".join(proc.cmdline())
            except Exception:
                cmd = proc.name()
            return "alive", "pid %d, holding since %s: %s" % (pid, taken_text[:16], cmd)
        except Exception:
            return "unknown", "pid %d cannot be checked on this machine" % pid

    def _break(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def try_acquire(self) -> bool:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, ("%d %s" % (os.getpid(),
                                     datetime.now().isoformat())).encode())
            os.close(fd)
            self._held = True
            return True
        except FileExistsError:
            pass
        state, _ = self.holder()
        if state == "alive":
            return False            # never overrule a running holder
        if state == "dead":
            self._break()
            return self.try_acquire()
        # Holder unknown: age is the only evidence left.
        try:
            if time.time() - self.path.stat().st_mtime > self.stale_seconds:
                self._break()
                return self.try_acquire()
        except OSError:
            pass
        return False

    def acquire(self, wait_seconds: int = 1500, log=print,
                waiting_for: str = "another database task") -> "TaskMutex":
        t0 = time.time()
        said = False
        while not self.try_acquire():
            if not said:
                _, who = self.holder()
                log("  waiting for %s to finish (%s)" % (waiting_for, who))
                said = True
            if time.time() - t0 > wait_seconds:
                _, who = self.holder()
                raise RuntimeError(
                    "could not acquire the database-task lock after %ds; "
                    "%s still holds it (%s). Nothing was changed; this job "
                    "runs again on its next schedule."
                    % (wait_seconds, waiting_for, who))
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
