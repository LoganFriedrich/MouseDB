"""mousedb update -- ONE action that brings the database current.

WHY THIS EXISTS
---------------
Five scheduled jobs each pulled one kind of data into connectome.db on their
own clocks (sheets, reaches, analyses, brains, then the snapshot). The
database is a single SQLite file that every one of them opens, so two of them
touching it at once meant one failed with "database is locked" -- and the
failures were quiet: rows landed without phase labels, the snapshot went
stale, and the scheduler still showed every job as succeeded.
The fix: a person presses ONE button, "Update the database", and the pulls
run in a fixed order, one after another, under a lock, and the result is
spelled out.

A scheduled run, at whatever cadence an installation picks (INSTALL.md,
"Scheduled jobs"), can call this same command, so on-demand and scheduled
updates are the same code path and can never collide.

ORDER (fixed, and why): hand data first (tracking sheets -- the sheet is
authoritative but late), then machine data (MouseReach reaches, MouseBrain
analyses, brain counts), then the human-facing copy (snapshot + current
exports) so what people see reflects everything that just landed.

A failed step does not stop the later steps: sheets failing must not keep a
day's reaches out of the database. Every step's outcome is reported, and the
whole update fails loudly (nonzero exit, red dialog) if any step failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .config import require

# (key, label a person reads, command)
#
# Each step runs the SAME command its scheduled job runs, so pressing the
# button and waiting for the schedule can never do different things. Two
# steps did not, until 2026-10:
#   sheets -- ran "mousedb.cli import --all", which discarded every cohort's
#     errors and warnings, wrote nothing to the import ledger, did not wait for
#     the database lock, and exited 0 even when cohorts failed, so this step
#     showed OK over a failed import. sheet_sync is the path that records each
#     cohort in the ledger (the Tracking Sheets tab reads it), takes the lock,
#     and exits 1 when any cohort fails; its last line is the summary this
#     dialog shows.
#   brains -- ran "import-brains" with no target, which only prints "Specify
#     --all, --csv, or --calibration" and exits 1, so this step failed on
#     every press and never imported a brain. --all is the whole-folder brain
#     import (INSTALL.md), the same command a scheduled brain import runs.
STEPS: List[Tuple[str, str, List[str]]] = [
    ("sheets", "Tracking sheets -> database",
     [sys.executable, "-m", "mousedb.sheet_sync", "import", "--triggered-by", "update"]),
    ("reaches", "MouseReach results -> reach_data",
     [sys.executable, "-m", "mousedb.cli", "import-reaches"]),
    ("analyses", "MouseBrain analysis registry -> exports",
     [sys.executable, "-m", "mousedb.cli", "import-analyses"]),
    ("brains", "Brain region counts -> database",
     [sys.executable, "-m", "mousedb.cli", "import-brains", "--all"]),
    ("snapshot", "Snapshot + current exports rewritten",
     [sys.executable, "-m", "mousedb.exporters.refresh_snapshot"]),
]
STEP_KEYS = [k for k, _, _ in STEPS]

# The update lock is a task_mutex lock named "update": logs/.update.task.lock.
# WHY not a hand-made file: the earlier one was checked with exists() and then
# written, so two presses at the same moment could both take it; and it was
# judged by AGE (taken over after 2 hours), so a long update was overruled
# while still running and then, on its way out, deleted the second update's
# lock. TaskMutex creates the file exclusively (exactly one press wins), judges
# a holder by whether its process is still running (a crashed update's lock is
# taken over at once, a live one never), and gives back only its own lock.
LOCK_MUTEX_NAME = "update"
# The marker earlier versions used. An update started by an earlier version
# (a window opened before the new version was installed) knows only this file,
# so while an update runs this version writes it too, and refuses to start
# while a running earlier update holds it. Without this, an old window and a
# new one would not see each other and both would run.
#
# An earlier version judges this marker by AGE alone: one less than two hours
# old is a running update, an older one is taken over. Updates of this version
# can run longer than that (the snapshot step's limit is 12 hours), so the
# marker is touched every task_mutex.HEARTBEAT_SECONDS for as long as the
# update holds the lock (_start_legacy_heartbeat). Written once and left, it
# would let an old window start a second update two hours in.
LEGACY_LOCK_NAME = "update.lock"
# The earlier versions' age rule, used here for an earlier-version marker whose
# process this computer cannot find (see _legacy_holder).
LEGACY_MARKER_HOURS = 2
# Only for a holder this computer cannot look up (an update started on another
# computer that shares the logs folder): its lockfile is touched every minute
# while it runs, so one untouched this long belongs to an update that died.
UNKNOWN_HOLDER_STALE_MINUTES = 45
LAST_NAME = "last_update.json"
LEDGER_NAME = "updates.jsonl"
# How long each step may run before it is stopped (None: no limit of its own).
#
# The imports: an hour each. They commit as they go and a stopped one carries
# on from where it stopped on the next run, so a limit only costs a delay.
#
# The snapshot step: 12 hours. WHY: a full refresh of a large database over a
# network share can take more than an hour by itself, and the one-hour limit
# every step would otherwise share stopped it every time -- part way through
# rewriting the current exports, so the button could never finish and left
# the exports half rewritten. The refresh bounds its own waiting (the
# database-task lock wait, the retry window), so what is left is real work.
# The cost: a refresh that hangs keeps the update -- and its lock -- busy for
# up to 12 hours, unless its window is closed or its process ended.
#
# Every step must be listed: run_update looks its limit up by key and fails on
# a missing one, so a new long step cannot silently get the import limit.
IMPORT_STEP_TIMEOUT_SECONDS = 3600
# The snapshot's limit is only a backstop against a refresh that has hung (an
# unreachable share): far above any working refresh, so one is never cut off,
# but finite, so a hung one cannot hold the update lock for days.
SNAPSHOT_STEP_TIMEOUT_SECONDS = 12 * 3600
STEP_TIMEOUT_SECONDS = {
    "sheets": IMPORT_STEP_TIMEOUT_SECONDS,
    "reaches": IMPORT_STEP_TIMEOUT_SECONDS,
    "analyses": IMPORT_STEP_TIMEOUT_SECONDS,
    "brains": IMPORT_STEP_TIMEOUT_SECONDS,
    "snapshot": SNAPSHOT_STEP_TIMEOUT_SECONDS,
}
OUTPUT_TAIL_LINES = 15          # how much of each step's output the ledger keeps


@dataclass
class StepResult:
    key: str
    label: str
    ok: bool
    seconds: float
    summary: str            # the step's own last line -- what it said it did
    returncode: Optional[int] = None
    output_tail: List[str] = field(default_factory=list)


@dataclass
class UpdateResult:
    started: str
    triggered_by: str
    finished: str = ""
    steps: List[StepResult] = field(default_factory=list)
    skipped: bool = False   # True when another update held the lock
    message: str = ""

    @property
    def ok(self) -> bool:
        return (not self.skipped) and all(s.ok for s in self.steps)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def _logs_dir() -> Path:
    d = Path(require("mousedb_root")) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_runner(cmd: Sequence[str],
                    timeout_seconds: Optional[float] = IMPORT_STEP_TIMEOUT_SECONDS) -> Tuple[int, str]:
    """Run one step in a child interpreter; return (returncode, combined output).
    ``timeout_seconds`` None: no limit (see STEP_TIMEOUT_SECONDS).
    A child process keeps one step's crash from taking the others down, and
    keeps the GUI's own database connection out of the writers' way."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console pop-ups from the GUI
    # A step waits for the cross-task lock for the interactive budget, not a
    # scheduled job's hours: a person is waiting (task_mutex.INTERACTIVE_WAIT_MINUTES).
    from . import task_mutex
    env = dict(os.environ)
    env.setdefault(task_mutex.WAIT_ENV, str(task_mutex.INTERACTIVE_WAIT_MINUTES))
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True,
                           timeout=timeout_seconds, creationflags=flags, env=env)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "step exceeded its limit of %d s and was stopped" % timeout_seconds


def _acquire_lock(logs: Path, log: Callable[[str], None]):
    """Take the update lock without waiting (see LOCK_MUTEX_NAME).

    Returns (mutex, None) when taken, else (None, a sentence saying who holds
    it). Never waits: a person pressed a button, and "another update is
    running" is the answer they need, not a frozen window."""
    from .task_mutex import TaskMutex
    mutex = TaskMutex(name=LOCK_MUTEX_NAME, base_dir=logs,
                      stale_minutes=UNKNOWN_HOLDER_STALE_MINUTES)
    # heartbeat: the lockfile is touched every minute while the update runs,
    # so an update started on another computer is judged alive by its age.
    # Two tries: between a refused try and reading who holds the lock, that
    # update may have finished (or turned out dead); the lock is then simply
    # taken, instead of refusing with "still running (no lockfile)".
    for _ in range(2):
        if mutex.try_acquire(log=log, heartbeat=True):
            legacy = _legacy_holder(logs)
            if legacy:
                mutex.release(log=log)
                return None, ("another update (started %s by an earlier version of this "
                              "tool) is still running -- wait for it to finish" % legacy)
            _write_legacy_marker(logs, mutex)
            return mutex, None
        state, who = mutex.holder()
        if state in ("alive", "unknown"):
            return None, "another update is still running (%s) -- wait for it to finish" % who
    # The lock kept changing hands (another press took it at the same moment):
    # say what was seen, not that a dead process is "still running".
    return None, ("could not take the update lock (%s) -- another update may have just "
                  "started; press again in a moment" % who)


def _legacy_holder(logs: Path) -> Optional[str]:
    """When an earlier version's update is running: when it started, else None.

    The earlier versions' marker names a process (pid) but not the computer it
    runs on, and the logs folder can be shared by several computers. So:

    - a process with that pid, started before the marker was written, is
      running HERE: the update is running, however old the marker is;
    - otherwise this computer cannot tell. A pid missing here may belong to an
      update running on another computer, so a missing pid is not proof that
      the update died. The earlier versions' own rule decides: a marker
      younger than two hours (LEGACY_MARKER_HOURS) is a running update, an
      older one is left over and ignored. A crashed earlier-version update
      therefore blocks presses for at most two hours from its start, which is
      what the earlier versions themselves did.

    A marker that cannot be read is ignored."""
    marker = logs / LEGACY_LOCK_NAME
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
        pid = int(info["pid"])
    except Exception:
        return None
    if info.get("token"):
        return None             # written by this version: the task lock decides
    alive = False
    try:
        import psutil
        if psutil.pid_exists(pid):
            # A pid reused by a process started after the marker was written
            # is not the update that wrote it.
            started = datetime.fromisoformat(str(info.get("started"))).timestamp()
            alive = psutil.Process(pid).create_time() <= started + 2
    except Exception:
        alive = False
    if not alive:
        # Not running on this computer (or not checkable): it may be running
        # on another one. The earlier versions wrote the marker once at the
        # start, so its age is the update's age.
        try:
            alive = time.time() - marker.stat().st_mtime < LEGACY_MARKER_HOURS * 3600
        except OSError:
            alive = False
    return "%s, pid %d" % (info.get("started", "?"), pid) if alive else None


def _write_legacy_marker(logs: Path, mutex) -> None:
    """The earlier versions' marker, so their update refuses while this runs."""
    try:
        (logs / LEGACY_LOCK_NAME).write_text(json.dumps({
            "pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds"),
            "token": mutex._identity}), encoding="utf-8")
    except OSError:
        pass


def _start_legacy_heartbeat(logs: Path, mutex):
    """Keep the earlier versions' marker young while this update runs.

    Returns the Event that stops it (set it in run_update's finally). Touches
    the marker only while it still carries this update's token, so a marker
    some other update has since written is never kept alive by this one.
    See LEGACY_LOCK_NAME for why."""
    import threading
    from . import task_mutex
    stop = threading.Event()
    marker = logs / LEGACY_LOCK_NAME
    token = mutex._identity
    interval = task_mutex.HEARTBEAT_SECONDS

    def beat():
        while not stop.wait(interval):
            try:
                if json.loads(marker.read_text(encoding="utf-8")).get("token") != token:
                    return
                os.utime(marker, None)
            except Exception:
                pass        # unreadable for a moment (being written): try again next beat

    threading.Thread(target=beat, name="mousedb-update-legacy-marker", daemon=True).start()
    return stop


def _remove_legacy_marker(logs: Path, mutex) -> None:
    """Remove the marker only if this update wrote it."""
    p = logs / LEGACY_LOCK_NAME
    try:
        if json.loads(p.read_text(encoding="utf-8")).get("token") == mutex._identity:
            p.unlink()
    except Exception:
        pass


def _last_line(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return lines[-1] if lines else "(no output)"


def run_update(triggered_by: str = "cli", skip: Sequence[str] = (),
               runner: Callable[[Sequence[str]], Tuple[int, str]] = None,
               log: Callable[[str], None] = print) -> UpdateResult:
    """Run every step in order under the update lock; record and return the result."""
    if runner is None:
        def run_step(key, cmd):
            return _default_runner(cmd, STEP_TIMEOUT_SECONDS[key])
    else:
        def run_step(key, cmd):
            return runner(cmd)
    logs = _logs_dir()
    res = UpdateResult(started=datetime.now().isoformat(timespec="seconds"), triggered_by=triggered_by)
    mutex, held = _acquire_lock(logs, log)
    if held:
        res.skipped = True
        res.message = held
        res.finished = res.started
        log("update skipped: " + held)
        return res
    legacy_beat = _start_legacy_heartbeat(logs, mutex)
    try:
        for key, label, cmd in STEPS:
            if key in skip:
                continue
            log("[%s] %s ..." % (key, label))
            t0 = time.time()
            rc, out = run_step(key, cmd)
            step = StepResult(key=key, label=label, ok=(rc == 0), seconds=round(time.time() - t0, 1),
                              summary=_last_line(out), returncode=rc,
                              output_tail=[ln for ln in out.splitlines() if ln.strip()][-OUTPUT_TAIL_LINES:])
            res.steps.append(step)
            log("   %s (%.0f s): %s" % ("OK  " if step.ok else "FAIL", step.seconds, step.summary))
        res.finished = datetime.now().isoformat(timespec="seconds")
        failed = [s.label for s in res.steps if not s.ok]
        res.message = ("Everything landed." if not failed
                       else "%d step(s) failed: %s" % (len(failed), "; ".join(failed)))
    finally:
        legacy_beat.set()
        _remove_legacy_marker(logs, mutex)
        mutex.release(log=log)
    # Record: the last result for the GUI label, and an append-only ledger.
    try:
        (logs / LAST_NAME).write_text(json.dumps(res.as_dict(), indent=1), encoding="utf-8")
        with open(logs / LEDGER_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(res.as_dict()) + "\n")
    except Exception as e:  # recording must never mask the update's own outcome
        log("could not record the update: %s" % e)
    return res


def last_update() -> Optional[dict]:
    """The most recent recorded update, or None."""
    try:
        return json.loads((_logs_dir() / LAST_NAME).read_text(encoding="utf-8"))
    except Exception:
        return None


def format_summary(res: UpdateResult) -> str:
    """The result as a person reads it: one line per step, then the verdict."""
    if res.skipped:
        return "Not run: " + res.message
    lines = []
    for s in res.steps:
        lines.append("%s  %s  (%.0f s)\n       %s" % ("[OK]  " if s.ok else "[FAIL]", s.label, s.seconds, s.summary))
    lines.append("")
    lines.append(res.message)
    if not res.ok:
        # Never raises. WHY: the GUI calls this from its "update finished" slot,
        # where an exception closes the whole application, and _logs_dir() can
        # fail right then -- an unreachable mousedb folder is a usual reason the
        # steps failed in the first place. The ledger keeps each step's last
        # OUTPUT_TAIL_LINES lines, not its whole output, so the text says so.
        try:
            where = str(_logs_dir() / LEDGER_NAME)
        except Exception:
            where = "%s in mousedb's logs folder, which cannot be reached right now" % LEDGER_NAME
        lines.append("Each failed step's last line is above; its last %d lines of output are in "
                     "the newest entry of %s." % (OUTPUT_TAIL_LINES, where))
    return "\n".join(lines)
