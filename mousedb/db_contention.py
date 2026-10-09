"""Surviving a database lock held by something this tool does not control.

WHY THIS EXISTS
---------------
The database is a single SQLite file, and it is normally kept on a shared
network location so that several computers can reach it. SQLite handles that
correctly -- it takes real file locks -- but a lock is a lock: while one
program is writing, everybody else is refused with "database is locked".

``task_mutex`` already makes this tool's own scheduled jobs take turns, so they
never do this to each other. It cannot do anything about the other openers:

* a person's analysis session (a notebook, a console, a spreadsheet driver)
  left open with an uncommitted write,
* this tool's own windows, running on somebody else's computer,
* anything else that can see the file.

None of those are faults. They are what a shared database is for. What WAS a
fault is how a scheduled job met them: it waited out a single timeout, then
died with a sixty-line stack trace and an exit code. Three consequences, all
observed:

1. The job's work did not happen that night -- and because the next run was a
   day away, a daily job could be down for days.
2. Nobody knew. A stack trace in a log file that nobody opens is not a report.
3. The trace said "OperationalError: database is locked", which reads like a
   defect in this tool. It is not; it is a neighbour holding a lock.

So: wait and try again a few times across a bounded window, and if the lock is
still there at the end, say so in one sentence a person can act on, and leave
the exit code to tell the scheduler. Nothing is half-written either way --
every caller here either completes its transaction or does not start it.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not break the other program's lock, and there is no option to. A lock
means a write is in flight; taking it away is how a database gets corrupted.
It also does not retry forever: a job that never gives up is a job that is
still running when the next one starts, which is the problem it was meant to
prevent.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

# How long to keep trying, and how long to pause between tries. The defaults
# suit a job that runs nightly with hours of clear air after it. A job with a
# neighbour an hour later should pass a smaller window.
DEFAULT_WINDOW_MINUTES = 45
DEFAULT_GAP_MINUTES = 7


class DatabaseBusy(RuntimeError):
    """The database stayed locked by another program for the whole window.

    Carries a message written for whoever reads the job's log, not for a
    developer: what happened, that nothing was changed, and what happens next.
    """


def is_locked_error(exc: BaseException) -> bool:
    """True if this exception is SQLite refusing because of a lock.

    Matches on the message rather than the type on purpose. The same condition
    arrives as ``sqlite3.OperationalError`` from a plain connection and as
    ``sqlalchemy.exc.OperationalError`` through the ORM, and the ORM wraps the
    original, so a type test has to know about every layer in use. The two
    messages SQLite produces for this are stable and few.

    Anything else -- a missing table, a corrupt file, a disk full -- is NOT a
    lock, must not be retried, and must keep its own traceback, because
    retrying it just delays a real fault by the length of the window.
    """
    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        text = str(cur).lower()
        if "database is locked" in text or "database table is locked" in text:
            return True
        # SQLAlchemy chains the driver error as __cause__; a bare driver error
        # has none and the loop ends.
        cur = cur.__cause__ or cur.__context__
    return False


def busy_message(what: str, window_minutes: int) -> str:
    """The sentence a person reads when a job gave up. Plain English, no jargon.

    Names the three things that are actually useful: that the obstacle is
    another program and not this one, that nothing was left half-done, and that
    no action is needed unless it keeps happening.
    """
    return (
        "%s did not run: another program kept the database locked for the whole "
        "%d minutes this job is willing to wait. That is usually an analysis "
        "session or a window of this tool left open somewhere -- including on "
        "another computer -- with a write in progress. Nothing was changed and "
        "nothing is half-written; the figures people see are still the ones from "
        "the last successful run. If this happens several times running, find the "
        "session holding the database and close it; the alternative, breaking "
        "someone else's lock while they are writing, is how a database gets "
        "corrupted, so this job will never do it."
        % (what, window_minutes)
    )


def retry_while_locked(
    work: Callable[[], T],
    what: str,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    gap_minutes: int = DEFAULT_GAP_MINUTES,
    announce: Optional[Callable[[str], None]] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run ``work``, retrying only while the database is locked by someone else.

    Args:
        work: the whole job, as something that can be run again from the start.
            It must be safe to re-run after a lock failure -- true of every
            caller here, because a transaction that could not start changed
            nothing.
        what: the job's name, as it should appear in a message to a person
            ("The analysis snapshot refresh").
        window_minutes: stop trying after this long, counted from the first
            attempt. A job will not run past its window even if a retry is due.
        gap_minutes: how long to wait between attempts.
        announce: where to put progress for a person -- usually print. Each
            wait is announced, because a silent job that takes 45 minutes looks
            exactly like a hung one, and someone will kill it.
        sleep, clock: injected so the behaviour can be tested without waiting.

    Returns:
        whatever ``work`` returned.

    Raises:
        DatabaseBusy: the window ran out with the database still locked.
        Anything else ``work`` raised, untouched and with its traceback: a
        fault that is not a lock is not something waiting can fix.
    """
    say = announce or (lambda _m: None)
    started = clock()
    deadline = started + window_minutes * 60
    attempt = 0

    while True:
        attempt += 1
        try:
            return work()
        except BaseException as exc:
            if not is_locked_error(exc):
                raise

            waited = int((clock() - started) / 60)
            remaining = deadline - clock()
            # No point sleeping if the nap itself would run past the window.
            if remaining <= gap_minutes * 60:
                raise DatabaseBusy(busy_message(what, window_minutes)) from exc

            say("  [!] the database is locked by another program (attempt %d, "
                "%d min so far). Waiting %d min and trying again; this job will "
                "give up after %d min."
                % (attempt, waited, gap_minutes, window_minutes))
            sleep(gap_minutes * 60)
