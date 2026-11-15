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

MECHANICS: an O_CREAT|O_EXCL lockfile under <mousedb_root>/logs (or, with
only ``db_path`` configured, the ``logs`` folder beside the database; never
the working directory -- see ``default_lock_dir``) with the holder's identity
inside. Release deletes the file; the context manager
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

HOW LONG A JOB WAITS. A job waits while the holder is running and making
progress (its CPU time and read/write count move; for a holder this machine
cannot look up, its lockfile's heartbeat), up to the job's WAIT BUDGET -- a
bound that must fit inside the job's own time limit with room for its own
work. Then it fails loudly: ``LockWaitTimeout``, holder named, nothing
changed. It also fails early when a live holder shows no progress for
``STALL_MINUTES``: it may be hung, and a live holder's lock is never broken.
WHY wait so long: the operating system's "restart on failure" re-runs a task
only when it could not be STARTED, never one that started and then exited
with an error. A job that gave up after a fixed short wait while the job ahead
of it was still busy was therefore simply lost until its next scheduled run.
Waiting turns a slow job into a delay for the next one instead of a loss.

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

# -- how long a job waits for the lock ----------------------------------------
#
# A scheduled job waits while the holder is running and getting somewhere, up
# to its WAIT BUDGET. The budget must fit inside the job's own time limit in
# the operating system's scheduler, with room left for the job's own work:
#     budget = time limit - (how long a successful run of this job takes)
# DEFAULT_WAIT_MINUTES is the budget of a job that does not set one: sized so a
# short job (seconds to minutes of its own work) still fits inside a one-hour
# time limit. A job that runs after a long one and has a longer limit sets its
# own with ``set_wait_minutes`` (the snapshot refresh: ``--lock-wait-minutes``),
# and the environment variable WAIT_ENV overrides both for one task without
# editing anything.
DEFAULT_WAIT_MINUTES = 40
WAIT_ENV = "MOUSEDB_LOCK_WAIT_MINUTES"
# For a window a person is sitting at: the wait blocks the window, and a
# person will not sit through two hours of it.
INTERACTIVE_WAIT_MINUTES = 25
# A holder that has shown no progress (CPU time, read/write count) for this
# long may be hung. Waiting longer will not help, and its lock is never broken
# while it runs, so the waiter gives up loudly instead.
STALL_MINUTES = 30
# A long wait says so in the log every this-many minutes: a silent job that
# waits for two hours looks exactly like a hung one, and gets killed.
REPORT_EVERY_MINUTES = 10
POLL_SECONDS = 5
# How often a holder touches its lockfile to show it is alive (see _heartbeat).
HEARTBEAT_SECONDS = 60

_process_wait_minutes: Optional[float] = None
# Lockfile contents this process already gave up waiting on -> when (monotonic).
_gave_up_on: dict = {}


class LockWaitTimeout(RuntimeError):
    """The lock did not come free within this job's wait budget, or its holder
    stopped making progress. Nothing was changed; the message says what to do."""


def set_wait_minutes(minutes: Optional[float]) -> None:
    """Set this process's wait budget (None: back to the default)."""
    global _process_wait_minutes
    _process_wait_minutes = None if minutes is None else float(minutes)


def default_wait_seconds() -> float:
    """The wait budget, in seconds: WAIT_ENV, else ``set_wait_minutes``, else
    DEFAULT_WAIT_MINUTES."""
    env = os.environ.get(WAIT_ENV, "").strip()
    if env:
        try:
            return float(env) * 60.0
        except ValueError:
            pass
    if _process_wait_minutes is not None:
        return _process_wait_minutes * 60.0
    return DEFAULT_WAIT_MINUTES * 60.0


def _say(message: str) -> None:
    # flush: a scheduled job's output goes to a log file, where Python buffers
    # it until the process ends -- a waiting job would look silent.
    print(message, flush=True)


def _this_host() -> str:
    return socket.gethostname().strip().lower() or "unknown-host"


def default_lock_dir() -> Path:
    """The folder every process puts the lockfile in.

    ``<mousedb_root>/logs`` when ``mousedb_root`` is set; else the ``logs``
    folder beside the database (``db_path``), which is the same folder on a
    standard layout; else ``ConfigError`` naming the command to run.

    WHY never the working directory: the lock only excludes processes that
    look for it in the SAME place. A machine with ``db_path`` set but not
    ``mousedb_root`` used to put the lockfile wherever each process happened
    to be started, so two jobs started from different folders each found the
    lock free and ran against the database together -- the very overlap this
    module exists to prevent.
    """
    from .config import db_path, log_path, require
    logs = log_path()
    if logs is not None:
        return logs
    db = db_path()
    if db is not None:
        # Beside the database, as given: a relative db_path names a different
        # database from each working directory, and its lock goes with it.
        return Path(db).parent / "logs"
    return require("mousedb_root") / "logs"     # raises ConfigError: says what to set


class TaskMutex:
    def __init__(self, name: str = "central-db", stale_minutes: int = 45,
                 base_dir: Optional[Path] = None):
        if base_dir is None:
            base_dir = default_lock_dir()
        base_dir = Path(base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        self.path = base_dir / (".%s.task.lock" % name)
        self.stale_seconds = stale_minutes * 60
        self._held = False
        self._identity: Optional[str] = None
        self._heartbeat_stop = None
        # Injected so the waiting rules can be tested without waiting.
        self._clock = time.monotonic
        self._sleep = time.sleep

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

    def try_acquire(self, log: Optional[Callable] = None, heartbeat: bool = False) -> bool:
        """Take the lock if it is free now (or its holder is gone); never wait.

        ``heartbeat`` True: on success, also start touching the lockfile while
        it is held, as ``acquire`` does -- for a caller that holds the lock for
        longer than ``stale_minutes`` and may be judged from another computer
        (see ``_start_heartbeat``)."""
        taken = self._try_acquire(log)
        if taken and heartbeat:
            self._start_heartbeat()
        return taken

    def _try_acquire(self, log: Optional[Callable]) -> bool:
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

    # -- waiting for the holder ---------------------------------------------

    def _activity(self, text: Optional[str], state: str):
        """Something that changes while the holder is getting somewhere.

        A holder on THIS machine: its CPU time and its count of read/write
        operations -- a job rewriting tables over a slow share moves both,
        a job hung on a dead connection moves neither. A holder that cannot be
        looked up (another computer, no psutil): the lockfile's modification
        time, which a holder running this version touches every
        ``HEARTBEAT_SECONDS`` (see ``_heartbeat``). A lock that changed hands
        is progress too: the queue is moving.
        """
        sig = [text]
        if state == "alive":
            try:
                import psutil
                proc = psutil.Process(int(text.split()[0]))
                cpu = proc.cpu_times()
                sig.append(round(cpu.user + cpu.system, 1))
                try:
                    io = proc.io_counters()
                    sig.append((io.read_count, io.write_count))
                except Exception:
                    pass
            except Exception:
                pass
        else:
            try:
                sig.append(self.path.stat().st_mtime)
            except OSError:
                pass
        return tuple(sig)

    def acquire(self, wait_seconds: Optional[float] = None, log=_say,
                waiting_for: str = "another database task",
                stall_seconds: Optional[float] = None,
                lock_label: str = "the database-task lock") -> "TaskMutex":
        """Wait for the lock, then take it.

        ``lock_label`` names this lock in the messages: a job waiting on a
        different lock (the current-exports lock) must not say it waited for
        the database-task lock, or the person looks at the wrong jobs.

        Waits as long as the holder is running AND getting somewhere, up to
        ``wait_seconds`` (default: ``default_wait_seconds()``). WHY so long:
        a job that gives up while the job before it is still working is a
        night's work lost -- the scheduler's "restart on failure" does not
        re-run a job that started and then exited with an error, so nothing
        brings it back until its next scheduled run. Waiting means a slow
        import DELAYS the next job instead of cancelling it. The bound must fit
        inside the waiting job's own time limit with room for its own work.

        Gives up early, loudly, when the holder has made no progress for
        ``stall_seconds`` (default ``STALL_MINUTES``): a hung holder will not
        finish however long we wait, and its lock is never broken while it is
        alive -- a person has to look at it. Either way raises
        ``LockWaitTimeout`` with the holder named and nothing changed.

        A job that already gave up on THIS holder (same lockfile contents)
        within its budget does not wait for it again: a job that takes the lock
        once per item (the sheet import: once per cohort) would otherwise sit
        through the whole wait once per item behind the same hung holder.
        """
        budget = default_wait_seconds() if wait_seconds is None else float(wait_seconds)
        stall = STALL_MINUTES * 60.0 if stall_seconds is None else float(stall_seconds)
        clock, sleep = self._clock, self._sleep
        t0 = clock()
        last_change = t0
        last_report = t0
        last_sig = None
        said = False
        while not self.try_acquire(log=log):
            now = clock()
            text = self._read()
            state, who = self._classify(text)
            gave_up = _gave_up_on.get(text)
            if text and gave_up is not None and now - gave_up < budget:
                raise LockWaitTimeout(
                    "could not acquire %s: this job already waited "
                    "for %s (%s) and gave up on it; it still holds the lock, so this "
                    "step is not waited for again. Nothing was changed by this step."
                    % (lock_label, waiting_for, who))
            sig = self._activity(text, state)
            if sig != last_sig:
                last_sig, last_change = sig, now
            waited = (now - t0) / 60.0
            if not said:
                log("  waiting for %s to finish (%s); will wait up to %d min "
                    "while it is working" % (waiting_for, who, budget // 60))
                said = True
            elif now - last_report >= REPORT_EVERY_MINUTES * 60:
                log("  still waiting after %d min for %s (%s; last sign of progress "
                    "%d min ago)" % (waited, waiting_for, who, (now - last_change) // 60))
                last_report = now
            if now - t0 >= budget:
                _gave_up_on[text] = now
                raise LockWaitTimeout(
                    "could not acquire %s: waited the full %d minutes "
                    "this job allows and %s still holds it (%s). It was left running. "
                    "Nothing was changed by this job, and it does NOT run again by "
                    "itself -- run it by hand once the holder has finished, or it "
                    "runs at its next scheduled time. If this keeps happening, give "
                    "this job a longer wait (%s, or --lock-wait-minutes where the job "
                    "has it) that still fits inside its time limit."
                    % (lock_label, budget // 60, waiting_for, who, WAIT_ENV))
            if state == "alive" and now - last_change >= stall:
                _gave_up_on[text] = now
                raise LockWaitTimeout(
                    "could not acquire %s: %s holds it (%s) and has "
                    "shown no sign of progress for %d minutes, so it may be hung. "
                    "Its lock is not broken while it is still running. Nothing was "
                    "changed by this job, and it does NOT run again by itself: look "
                    "at the holder (its log, or the operating system's scheduler), end "
                    "it if it is "
                    "stuck, then run this job by hand."
                    % (lock_label, waiting_for, who, (now - last_change) // 60))
            sleep(POLL_SECONDS)
        self._start_heartbeat()
        return self

    # -- heartbeat ------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Touch the lockfile every HEARTBEAT_SECONDS while held.

        WHY: a waiter that cannot look the holder up (it runs on another
        computer, or psutil is missing) judges it only by the lockfile's age.
        Without a heartbeat, a live holder on another computer looked "stale"
        after ``stale_minutes`` and its lock was broken under it. With one, the
        age means "time since the holder last showed it was alive"."""
        import threading
        stop = threading.Event()
        identity = self._identity

        def beat():
            while not stop.wait(HEARTBEAT_SECONDS):
                if self._read() != identity:
                    return
                try:
                    os.utime(self.path, None)
                except OSError:
                    pass

        t = threading.Thread(target=beat, name="mousedb-task-lock-heartbeat", daemon=True)
        t.start()
        self._heartbeat_stop = stop

    def release(self, log: Optional[Callable] = None) -> None:
        """Give the lock back -- only if the lockfile is still this holder's.

        Done under the break token, so no waiter can be halfway through
        breaking it at the same moment. If the token cannot be had within a
        few seconds (its holder is stuck), the lock is still removed if it is
        ours: leaving our own lock behind would block every later job."""
        if self._heartbeat_stop is not None:
            self._heartbeat_stop.set()
            self._heartbeat_stop = None
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


def hold(name: str = "central-db", wait_seconds: Optional[float] = None, log=_say,
         waiting_for: str = "another database task") -> TaskMutex:
    """Acquire (waiting if needed) and return the held mutex, for ``with``.

    ``wait_seconds`` None: this process's wait budget (``default_wait_seconds``)."""
    return TaskMutex(name).acquire(wait_seconds=wait_seconds, log=log,
                                   waiting_for=waiting_for)
