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

MECHANICS: an O_CREAT|O_EXCL lockfile under <mousedb_root>/logs with the
holder's identity inside. Release deletes the file; the context manager
releases on any exit.

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
lockfile whose holder cannot be identified (no psutil, unreadable contents,
or a holder on another computer, whose process this machine cannot see).

BREAKING IS ONE STEP, AND ONLY ONE WAITER CAN DO IT. Judging a lock dead and
deleting it are two moments; between them another waiter may have deleted the
same dead lock and taken a NEW one, which a careless second delete would then
remove -- two jobs on the database at once, the outage this module exists to
prevent. So a waiter breaks a lock only while holding a short-lived break
token (``<lock>.breaking``, an exclusive create, so exactly one process has
it at a time), and only if the lockfile still holds, byte for byte, the
contents it judged dead. Every break is written to the job's log and to a
permanent record beside the lock (``<lock>.breaks.log``), so a broken lock is
never a silent event.

RELEASE REMOVES ONLY ITS OWN LOCK. Each lock carries its holder's identity
(pid, time, computer, and a random token). ``release()`` deletes the lockfile
only if it still carries that identity: a holder whose lock was broken while
it ran must not, on its way out, delete the lock of the job that took over.
"""
from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Tuple

# A break token older than this belongs to a process that died in the middle of
# breaking or releasing (the token is normally held for milliseconds), and may
# be cleared.
BREAK_TOKEN_STALE_SECONDS = 60


def _this_host() -> str:
    return socket.gethostname().strip().lower() or "unknown-host"


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
        self._identity: Optional[str] = None

    # -- the lockfile's contents ---------------------------------------------

    @staticmethod
    def _new_identity() -> str:
        """``<pid> <iso time> <computer> <token>``. The first two fields are the
        format every earlier version wrote and read; the computer tells a lock
        held elsewhere from a dead local one; the token makes this holder's
        lock distinguishable from any other, even one with the same pid."""
        return "%d %s %s %s" % (os.getpid(), datetime.now().isoformat(),
                                _this_host(), uuid.uuid4().hex)

    def _read(self) -> Optional[str]:
        """The lockfile's text; None when there is no lockfile."""
        try:
            return self.path.read_text()
        except FileNotFoundError:
            return None
        except OSError:
            return ""           # there, but cannot be read right now

    def _classify(self, text: Optional[str]) -> Tuple[str, str]:
        if text is None:
            return "absent", "no lockfile"
        try:
            fields = text.split()
            pid = int(fields[0])
            taken_text = fields[1]
            taken = datetime.fromisoformat(taken_text).timestamp()
        except Exception:
            return "unknown", "lockfile %s is unreadable" % self.path.name
        host = fields[2] if len(fields) > 2 else None
        if host is not None and host != _this_host():
            # A process on another computer cannot be looked up from here. Its
            # pid means nothing in this machine's process table: judging it
            # "dead" because no local process has that pid would break the
            # lock of a job that is running perfectly well elsewhere.
            return "unknown", ("pid %d on another computer (%s), holding since %s"
                               % (pid, host, taken_text[:16]))
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

    # -- who holds it -------------------------------------------------------

    def holder(self) -> Tuple[str, str]:
        """(state, description) for the lockfile's holder.

        state is ``"alive"`` (a process that took this lock is still running),
        ``"dead"`` (that process is gone, or its pid was reused by a process
        started after the lock was written), ``"unknown"`` (the lockfile
        cannot be read, or the pid cannot be checked from this machine -- for
        example because it belongs to another computer) or ``"absent"``.
        """
        return self._classify(self._read())

    # -- breaking a dead lock: one process, one step, always recorded -------

    @property
    def _token_path(self) -> Path:
        return self.path.with_name(self.path.name + ".breaking")

    @property
    def breaks_log(self) -> Path:
        """Permanent record of every lock this mutex broke."""
        return self.path.with_name(self.path.name + ".breaks.log")

    def _take_token(self) -> bool:
        token = self._token_path
        try:
            fd = os.open(str(token), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            # Someone is breaking or releasing right now -- or died doing it.
            # The token lives for milliseconds, so an old one is an orphan.
            try:
                if time.time() - token.stat().st_mtime > BREAK_TOKEN_STALE_SECONDS:
                    token.unlink()
            except OSError:
                pass
            return False
        try:
            os.write(fd, ("%d %s" % (os.getpid(), _this_host())).encode())
        finally:
            os.close(fd)
        return True

    def _drop_token(self) -> None:
        try:
            self._token_path.unlink()
        except OSError:
            pass

    def _record_break(self, seen: str, why: str, log: Optional[Callable]) -> None:
        line = ("%s  broke the lock %r (%s) -- by pid %d on %s"
                % (datetime.now().isoformat(timespec="seconds"), seen.strip(), why,
                   os.getpid(), _this_host()))
        try:
            with open(self.breaks_log, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
        if log is not None:
            try:
                log("  [!] database-task lock: " + line)
            except Exception:
                pass

    def _break(self, seen: str, why: str, log: Optional[Callable] = None) -> bool:
        """Delete the lockfile if it still holds exactly ``seen``. True if this
        call broke it. Only the process holding the break token may do it, and
        the contents are compared under the token, so a lock that changed hands
        since it was judged dead is left alone."""
        if not self._take_token():
            return False
        try:
            if self._read() != seen:
                return False            # broken by someone else, or retaken
            try:
                self.path.unlink()
            except OSError:
                return False
            self._record_break(seen, why, log)
            return True
        finally:
            self._drop_token()

    # -- taking and giving back --------------------------------------------

    def _create(self) -> bool:
        identity = self._new_identity()
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, identity.encode())
        finally:
            os.close(fd)
        self._identity = identity
        self._held = True
        return True

    def try_acquire(self, log: Optional[Callable] = None) -> bool:
        if self._create():
            return True
        seen = self._read()
        if seen is None:                # released a moment ago
            return self._create()
        state, why = self._classify(seen)
        if state == "alive":
            return False                # never overrule a running holder
        if state == "dead":
            return self._break(seen, why, log) and self._create()
        # Holder unknown: age is the only evidence left.
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        if age > self.stale_seconds:
            why = "%s; untouched for %d minutes" % (why, age // 60)
            return self._break(seen, why, log) and self._create()
        return False

    def acquire(self, wait_seconds: int = 1500, log=print,
                waiting_for: str = "another database task") -> "TaskMutex":
        t0 = time.time()
        said = False
        while not self.try_acquire(log=log):
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

    def release(self, log: Optional[Callable] = None) -> None:
        """Give the lock back -- only if the lockfile is still this holder's.

        Done under the break token, so no waiter can be halfway through
        breaking it at the same moment. If the token cannot be had within a
        few seconds (its holder is stuck), the lock is still removed if it is
        ours: leaving our own lock behind would block every later job."""
        if not self._held:
            return
        self._held = False
        deadline = time.monotonic() + 10
        have_token = self._take_token()
        while not have_token and time.monotonic() < deadline:
            time.sleep(0.05)
            have_token = self._take_token()
        try:
            now = self._read()
            if now == self._identity:
                try:
                    self.path.unlink()
                except OSError:
                    pass
            elif now is not None and log is not None:
                log("  [!] database-task lock: this job's lock was broken while it "
                    "ran and another job holds it now; that job's lock is left alone")
        finally:
            if have_token:
                self._drop_token()

    def __enter__(self) -> "TaskMutex":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def hold(name: str = "central-db", wait_seconds: int = 1500, log=print,
         waiting_for: str = "another database task") -> TaskMutex:
    """Acquire (waiting if needed) and return the held mutex, for ``with``."""
    return TaskMutex(name).acquire(wait_seconds=wait_seconds, log=log,
                                   waiting_for=waiting_for)
