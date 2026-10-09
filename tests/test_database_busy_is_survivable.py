"""A scheduled job must survive a lock held by a program we do not control.

The database is one SQLite file, usually on a shared network location. The
cross-task mutex makes this tool's own jobs take turns, but anything else that
can open the file -- someone's notebook, a window of this tool on another
computer -- can hold a write lock, and while it does everyone else is refused.

Before this, a job that met one locked moment died with a stack trace and did
not run again until its next scheduled time. For a daily job that is a lost
day, and three were lost in a row without anybody noticing, because a stack
trace in an unread log is not a report.
"""
import sqlite3

import pytest

from mousedb import db_contention as dc


class FakeClock:
    """Time that only moves when something sleeps, so tests do not wait."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def locked_error():
    return sqlite3.OperationalError("database is locked")


def wrapped_locked_error():
    """What the ORM raises: its own error with the driver's as the cause."""
    outer = RuntimeError("(sqlite3.OperationalError) database is locked")
    outer.__cause__ = locked_error()
    return outer


# --------------------------------------------------------------- recognising it
def test_a_plain_driver_lock_error_is_recognised():
    assert dc.is_locked_error(locked_error())


def test_a_lock_error_wrapped_by_the_orm_is_recognised():
    """The ORM wraps it, so matching on the outermost type alone would miss it."""
    assert dc.is_locked_error(wrapped_locked_error())


@pytest.mark.parametrize("exc", [
    sqlite3.OperationalError("no such table: subjects"),
    sqlite3.DatabaseError("database disk image is malformed"),
    ValueError("something else entirely"),
    OSError("disk full"),
])
def test_other_faults_are_not_mistaken_for_a_lock(exc):
    """Retrying a real fault just delays it by the length of the window.

    A missing table, a corrupt file or a full disk are all things a person must
    be told about now, with the traceback that says where.
    """
    assert not dc.is_locked_error(exc)


# -------------------------------------------------------------------- retrying
def test_work_that_succeeds_first_time_is_not_delayed():
    clock = FakeClock()
    calls = []
    result = dc.retry_while_locked(
        lambda: (calls.append(1), "done")[1], what="The job",
        sleep=clock.sleep, clock=clock)
    assert result == "done"
    assert len(calls) == 1
    assert clock.slept == [], "nothing should wait when nothing is locked"


def test_a_lock_that_clears_lets_the_job_finish():
    """The case this exists for: a neighbour finishes writing and we carry on."""
    clock = FakeClock()
    attempts = []

    def work():
        attempts.append(1)
        if len(attempts) < 3:
            raise locked_error()
        return "snapshot taken"

    assert dc.retry_while_locked(work, what="The job", gap_minutes=5,
                                 window_minutes=45,
                                 sleep=clock.sleep, clock=clock) == "snapshot taken"
    assert len(attempts) == 3
    assert clock.slept == [300, 300]


def test_a_lock_that_never_clears_ends_with_a_sentence_a_person_can_act_on():
    clock = FakeClock()

    def work():
        raise locked_error()

    with pytest.raises(dc.DatabaseBusy) as caught:
        dc.retry_while_locked(work, what="The analysis snapshot refresh",
                              gap_minutes=10, window_minutes=30,
                              sleep=clock.sleep, clock=clock)

    message = str(caught.value)
    assert "The analysis snapshot refresh did not run" in message
    assert "another program" in message, "must not read as a fault in this tool"
    assert "Nothing was changed" in message
    assert "last successful run" in message, "must say what people are seeing instead"
    assert "Error" not in message and "Traceback" not in message


def test_it_gives_up_inside_its_window_rather_than_running_over():
    """A job still running when the next one starts is the problem it prevents."""
    clock = FakeClock()

    with pytest.raises(dc.DatabaseBusy):
        dc.retry_while_locked(lambda: (_ for _ in ()).throw(locked_error()),
                              what="The job", gap_minutes=7, window_minutes=20,
                              sleep=clock.sleep, clock=clock)

    assert clock.now - 1000.0 <= 20 * 60


def test_a_real_fault_keeps_its_own_traceback_and_is_not_retried():
    clock = FakeClock()
    attempts = []

    def work():
        attempts.append(1)
        raise sqlite3.OperationalError("no such table: subjects")

    with pytest.raises(sqlite3.OperationalError):
        dc.retry_while_locked(work, what="The job", sleep=clock.sleep, clock=clock)
    assert len(attempts) == 1
    assert clock.slept == []


def test_waiting_is_announced_so_a_slow_job_is_not_mistaken_for_a_hung_one():
    clock = FakeClock()
    said = []
    attempts = []

    def work():
        attempts.append(1)
        if len(attempts) < 2:
            raise locked_error()
        return None

    dc.retry_while_locked(work, what="The job", gap_minutes=5, window_minutes=45,
                          announce=said.append, sleep=clock.sleep, clock=clock)
    assert said, "a wait must be visible in the log"
    assert "locked by another program" in said[0]


def test_giving_up_raises_rather_than_returning_quietly():
    """The caller has to be able to set a failing exit code.

    If giving up looked like success, the scheduler would record a clean run,
    and a job that has not worked for days would look perfectly healthy.
    """
    clock = FakeClock()
    with pytest.raises(dc.DatabaseBusy):
        dc.retry_while_locked(lambda: (_ for _ in ()).throw(locked_error()),
                              what="The job", gap_minutes=1, window_minutes=2,
                              sleep=clock.sleep, clock=clock)


def test_a_zero_window_tries_once_and_does_not_wait():
    """How a long job opts out: retrying it is more expensive than skipping it.

    A job that takes an hour has to restart from the beginning on every retry,
    so for some jobs the right window is none at all.
    """
    clock = FakeClock()
    attempts = []

    def work():
        attempts.append(1)
        raise locked_error()

    with pytest.raises(dc.DatabaseBusy):
        dc.retry_while_locked(work, what="The job", window_minutes=0,
                              sleep=clock.sleep, clock=clock)
    assert len(attempts) == 1
    assert clock.slept == []


def test_the_original_error_is_kept_as_the_cause_for_whoever_debugs_it():
    """A person gets the sentence; a developer can still reach the real error."""
    clock = FakeClock()
    with pytest.raises(dc.DatabaseBusy) as caught:
        dc.retry_while_locked(lambda: (_ for _ in ()).throw(locked_error()),
                              what="The job", gap_minutes=1, window_minutes=2,
                              sleep=clock.sleep, clock=clock)
    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
