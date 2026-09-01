"""mousedb update -- ONE action that brings the database current.

WHY THIS EXISTS
---------------
Five scheduled jobs each pulled one kind of data into connectome.db on their
own clocks (sheets, reaches, analyses, brains, then the snapshot). The
database is a single SQLite file on a network share, so two of them touching
it at once meant one failed with "database is locked" -- and the failures
were quiet: rows landed without phase labels, the snapshot went stale, and
Task Scheduler stayed green (2026-09-01). Logan's correction: a person
presses ONE button, "Update the database", and the pulls run in a fixed order,
one after another, under a lock, and the result is spelled out.

The scheduled hourly run can call this same command, so on-demand and
scheduled updates are the same code path and can never collide.

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
STEPS: List[Tuple[str, str, List[str]]] = [
    ("sheets", "Tracking sheets -> database",
     [sys.executable, "-m", "mousedb.cli", "import", "--all"]),
    ("reaches", "MouseReach results -> reach_data",
     [sys.executable, "-m", "mousedb.cli", "import-reaches"]),
    ("analyses", "MouseBrain analysis registry -> exports",
     [sys.executable, "-m", "mousedb.cli", "import-analyses"]),
    ("brains", "Brain region counts -> database",
     [sys.executable, "-m", "mousedb.cli", "import-brains"]),
    ("snapshot", "Snapshot + current exports rewritten",
     [sys.executable, "-m", "mousedb.exporters.refresh_snapshot"]),
]
STEP_KEYS = [k for k, _, _ in STEPS]

LOCK_NAME = "update.lock"
LAST_NAME = "last_update.json"
LEDGER_NAME = "updates.jsonl"
STALE_LOCK_SECONDS = 2 * 3600   # an update older than this is presumed dead
STEP_TIMEOUT_SECONDS = 3600     # no single step may run longer than an hour


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


def _default_runner(cmd: Sequence[str]) -> Tuple[int, str]:
    """Run one step in a child interpreter; return (returncode, combined output).
    A child process keeps one step's crash from taking the others down, and
    keeps the GUI's own database connection out of the writers' way."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console pop-ups from the GUI
    try:
        p = subprocess.run(list(cmd), capture_output=True, text=True,
                           timeout=STEP_TIMEOUT_SECONDS, creationflags=flags)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "step exceeded %d s and was stopped" % STEP_TIMEOUT_SECONDS


def _acquire_lock(logs: Path, triggered_by: str) -> Optional[str]:
    """Take the update lock. Returns None on success, else a sentence saying
    who holds it. A lock older than STALE_LOCK_SECONDS is taken over -- an
    update that old is a dead process, not a running one."""
    lock = logs / LOCK_NAME
    if lock.exists():
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        age = time.time() - lock.stat().st_mtime
        if age < STALE_LOCK_SECONDS:
            return ("another update (started %s by %s) is still running -- "
                    "wait for it to finish" % (info.get("started", "?"), info.get("triggered_by", "?")))
    lock.write_text(json.dumps({"pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds"),
                                "triggered_by": triggered_by}), encoding="utf-8")
    return None


def _release_lock(logs: Path) -> None:
    try:
        (logs / LOCK_NAME).unlink()
    except FileNotFoundError:
        pass


def _last_line(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    return lines[-1] if lines else "(no output)"


def run_update(triggered_by: str = "cli", skip: Sequence[str] = (),
               runner: Callable[[Sequence[str]], Tuple[int, str]] = None,
               log: Callable[[str], None] = print) -> UpdateResult:
    """Run every step in order under the update lock; record and return the result."""
    runner = runner or _default_runner
    logs = _logs_dir()
    res = UpdateResult(started=datetime.now().isoformat(timespec="seconds"), triggered_by=triggered_by)
    held = _acquire_lock(logs, triggered_by)
    if held:
        res.skipped = True
        res.message = held
        res.finished = res.started
        log("update skipped: " + held)
        return res
    try:
        for key, label, cmd in STEPS:
            if key in skip:
                continue
            log("[%s] %s ..." % (key, label))
            t0 = time.time()
            rc, out = runner(cmd)
            step = StepResult(key=key, label=label, ok=(rc == 0), seconds=round(time.time() - t0, 1),
                              summary=_last_line(out), returncode=rc,
                              output_tail=[ln for ln in out.splitlines() if ln.strip()][-15:])
            res.steps.append(step)
            log("   %s (%.0f s): %s" % ("OK  " if step.ok else "FAIL", step.seconds, step.summary))
        res.finished = datetime.now().isoformat(timespec="seconds")
        failed = [s.label for s in res.steps if not s.ok]
        res.message = ("Everything landed." if not failed
                       else "%d step(s) failed: %s" % (len(failed), "; ".join(failed)))
    finally:
        _release_lock(logs)
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
        lines.append("Each failed step's last line is above; its full output is the newest entry in "
                     "%s." % (_logs_dir() / LEDGER_NAME))
    return "\n".join(lines)
