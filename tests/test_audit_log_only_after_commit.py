"""The audit file must only ever describe changes the database actually holds.

Database.log_change records a change twice: an AuditLog row in the database
and a line in logs/<date>_changes.jsonl. The row is part of the caller's
transaction. The file line used to be appended at once, when log_change was
called. When that transaction then rolled back -- a constraint failure, a
crash, a cancelled import -- the database had no change but the file still
claimed one: a false audit trail, in the very file a person reads to answer
"what did the import do to my data?".

log_change has no callers yet; the importer is about to call it for every
change it makes, potentially thousands per run. These tests pin the contract
before that happens:

- a committed change appears in the file, and the file agrees with the table;
- a rolled-back change -- the whole transaction or only a SAVEPOINT -- never
  does, and neither does one whose commit itself failed;
- a session closed without committing leaves nothing behind, even if reused;
- all of one commit's lines go out in one file append, not one open per change;
- a refused append never turns a saved change into a reported failure, and is
  not silent either.

The same SQLAlchemy behaviour that matters for SAVEPOINTs (after_commit also
fires when a SAVEPOINT is released) used to start the phase refresh before the
real commit, while the outer transaction still held the write lock; the last
test pins that too.
"""
import builtins
import datetime as dt
import io
import json
import sys

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mousedb import database as dbmod
from mousedb.schema import AuditLog, Base, Cohort, Project


class Boom(Exception):
    """Stands in for whatever makes a caller abandon its transaction."""


@pytest.fixture
def db(tmp_path, monkeypatch):
    # Without this the change files would go to the configured log folder --
    # the real one -- instead of under tmp_path.
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    d = dbmod.Database(tmp_path / "t.db")
    assert d.log_path == tmp_path / "logs"
    Base.metadata.create_all(d.engine)
    return d


def change_lines(db):
    """Every line in every change file, in file then line order."""
    return [json.loads(text)
            for f in sorted(db.log_path.glob("*_changes.jsonl"))
            for text in f.read_text(encoding="utf-8").splitlines()]


def logged_ids(db):
    return [line["record_id"] for line in change_lines(db)]


def table_ids(db):
    with db.session() as s:
        return [r.record_id for r in s.query(AuditLog).order_by(AuditLog.id)]


# ------------------------------------------------------------------ committed
def test_a_committed_change_is_written_only_once_the_commit_happens(db):
    with db.session() as s:
        db.log_change(s, "INSERT", "subjects", "CNT_05_01",
                      new_values={"sex": "F"})
        s.flush()  # the row is in the database's transaction already...
        assert change_lines(db) == []  # ...but nothing is committed, so no line

    lines = change_lines(db)
    assert [(l["action"], l["table"], l["record_id"], l["new"]) for l in lines] \
        == [("INSERT", "subjects", "CNT_05_01", {"sex": "F"})]
    assert table_ids(db) == ["CNT_05_01"]

    # One timestamp for both records, so a line can be matched to its row.
    with db.session() as s:
        row = s.query(AuditLog).one()
        assert row.timestamp.isoformat() == lines[0]["timestamp"]


def test_two_changes_in_one_commit_are_one_append(db, monkeypatch):
    opens, writes = [], []

    class CountingFile:
        def __init__(self, f):
            self._f = f

        def write(self, text):
            writes.append(text)
            return self._f.write(text)

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *exc):
            return self._f.__exit__(*exc)

    def counting_open(file, *args, **kwargs):
        f = builtins.open(file, *args, **kwargs)
        if str(file).endswith("_changes.jsonl"):
            opens.append(str(file))
            return CountingFile(f)
        return f

    # A module global named open shadows the builtin inside database.py only.
    monkeypatch.setattr(dbmod, "open", counting_open, raising=False)

    with db.session() as s:
        db.log_change(s, "INSERT", "subjects", "CNT_05_01", new_values={"sex": "F"})
        db.log_change(s, "UPDATE", "subjects", "CNT_05_02",
                      old_values={"sex": None}, new_values={"sex": "M"})
        assert opens == []

    assert len(opens) == 1 and len(writes) == 1
    assert logged_ids(db) == ["CNT_05_01", "CNT_05_02"]
    assert table_ids(db) == ["CNT_05_01", "CNT_05_02"]


def test_each_line_goes_to_the_file_of_the_day_it_was_logged(db, monkeypatch):
    """A transaction that spans midnight still files each change under its own day."""
    stamps = [dt.datetime(2026, 1, 1, 23, 59, 59), dt.datetime(2026, 1, 2, 0, 0, 1)]

    class FakeDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return stamps.pop(0)

    monkeypatch.setattr(dbmod, "datetime", FakeDatetime)
    with db.session() as s:
        db.log_change(s, "INSERT", "subjects", "CNT_05_01")
        db.log_change(s, "INSERT", "subjects", "CNT_05_02")

    day1 = db.log_path / "2026-01-01_changes.jsonl"
    day2 = db.log_path / "2026-01-02_changes.jsonl"
    assert [json.loads(t)["record_id"] for t in day1.read_text().splitlines()] == ["CNT_05_01"]
    assert [json.loads(t)["record_id"] for t in day2.read_text().splitlines()] == ["CNT_05_02"]


# ---------------------------------------------------------------- rolled back
def test_a_rolled_back_change_never_reaches_the_file(db):
    with pytest.raises(Boom):
        with db.session() as s:
            db.log_change(s, "INSERT", "subjects", "CNT_05_01")
            s.flush()
            raise Boom()

    assert change_lines(db) == []
    assert not list(db.log_path.glob("*_changes.jsonl"))
    assert table_ids(db) == []


def test_a_rollback_does_not_leak_into_the_next_commit_on_the_same_session(db):
    with db.session() as s:
        db.log_change(s, "INSERT", "subjects", "CNT_05_01")
        s.rollback()
        db.log_change(s, "INSERT", "subjects", "CNT_05_02")

    assert logged_ids(db) == ["CNT_05_02"]
    assert table_ids(db) == ["CNT_05_02"]


def test_a_commit_that_fails_writes_nothing(db):
    with db.session() as s:
        s.add(Project(project_code="PRJ", project_name="test project"))

    with pytest.raises(IntegrityError):
        with db.session() as s:
            # The same project again: refused at commit by the primary key.
            s.add(Project(project_code="PRJ", project_name="duplicate"))
            db.log_change(s, "INSERT", "projects", "PRJ")

    assert change_lines(db) == []
    assert table_ids(db) == []


def test_a_session_closed_without_commit_leaves_nothing_behind(db):
    s = db.SessionLocal()
    db.log_change(s, "INSERT", "subjects", "CNT_05_01")
    s.close()  # abandoned: no commit, no explicit rollback
    assert change_lines(db) == []

    # A closed session can be used again; the abandoned line must not ride
    # along with the next commit.
    db.log_change(s, "INSERT", "subjects", "CNT_05_02")
    s.commit()
    s.close()

    assert logged_ids(db) == ["CNT_05_02"]
    assert table_ids(db) == ["CNT_05_02"]


# ----------------------------------------------------------------- savepoints
def test_a_rolled_back_savepoint_drops_only_its_own_lines(db):
    with db.session() as s:
        db.log_change(s, "INSERT", "subjects", "CNT_05_01")
        try:
            with s.begin_nested():
                db.log_change(s, "INSERT", "subjects", "CNT_05_02")
                with s.begin_nested():
                    db.log_change(s, "INSERT", "subjects", "CNT_05_03")
                raise Boom()
        except Boom:
            pass
        db.log_change(s, "INSERT", "subjects", "CNT_05_04")

    assert logged_ids(db) == ["CNT_05_01", "CNT_05_04"]
    assert table_ids(db) == ["CNT_05_01", "CNT_05_04"]


def test_a_released_savepoint_is_not_a_commit(db):
    """SQLAlchemy fires after_commit on SAVEPOINT release; the outer rollback still wins.

    The SAVEPOINT here is the transaction's first statement. Python's sqlite3
    driver had not sent BEGIN yet, so SQLite treated the SAVEPOINT as the
    transaction and its release COMMITTED: the outer rollback undid nothing
    and the row stayed. The table assertion pins the fix for that too.
    """
    with pytest.raises(Boom):
        with db.session() as s:
            with s.begin_nested():
                db.log_change(s, "INSERT", "subjects", "CNT_05_01")
            assert change_lines(db) == []  # released, but nothing is durable
            raise Boom()

    assert change_lines(db) == []
    assert table_ids(db) == []


def test_a_released_savepoint_is_written_by_the_outer_commit(db):
    with db.session() as s:
        with s.begin_nested():
            db.log_change(s, "INSERT", "subjects", "CNT_05_01")
        assert change_lines(db) == []
        db.log_change(s, "INSERT", "subjects", "CNT_05_02")

    assert logged_ids(db) == ["CNT_05_01", "CNT_05_02"]
    assert table_ids(db) == ["CNT_05_01", "CNT_05_02"]


# ----------------------------------------------------- refusals and failures
def test_a_session_this_database_did_not_make_is_refused(db, tmp_path):
    # Its commits would never run this Database's hooks, so the file line
    # would be dropped without a word -- or land in another Database's folder.
    with Session(bind=db.engine) as foreign:
        with pytest.raises(TypeError):
            db.log_change(foreign, "INSERT", "subjects", "CNT_05_01")

    other = dbmod.Database(tmp_path / "other.db")
    with other.session() as s:
        with pytest.raises(TypeError):
            db.log_change(s, "INSERT", "subjects", "CNT_05_01")


def test_a_refused_append_keeps_the_save_and_says_so(db, tmp_path, capsys):
    blocker = tmp_path / "not_a_folder"
    blocker.write_text("a file where the log folder should be")
    db.log_path = blocker / "logs"

    with db.session() as s:  # must not raise: the data IS saved
        db.log_change(s, "INSERT", "subjects", "CNT_05_01")

    assert table_ids(db) == ["CNT_05_01"]
    assert [json.loads(l)["record_id"] for l in db.unwritten_audit_lines] == ["CNT_05_01"]
    out = capsys.readouterr().out
    assert "[!] Audit log: 1 committed change(s) could not be appended" in out
    assert out.isascii()  # Windows consoles crash on anything else


def test_the_refusal_message_itself_cannot_fail_the_save(db, tmp_path, monkeypatch):
    """The message names the folder and quotes the system's error text.

    Either can hold characters a legacy code page cannot print (a user name in
    the path, a localised error). A task log or console in such a code page
    made print() itself raise -- from inside the commit -- so a change that
    WAS saved came back to the caller as a failed save.
    """
    # Cyrillic Zhe: outside ASCII and outside the Western code pages.
    blocker = tmp_path / "folder_Ж"
    blocker.write_text("a file where the log folder should be")
    db.log_path = blocker / "logs"
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="ascii"))

    with db.session() as s:  # must not raise: the data IS saved
        db.log_change(s, "INSERT", "subjects", "CNT_05_01")

    sys.stdout.flush()
    out = raw.getvalue().decode("ascii")
    assert "[!] Audit log: 1 committed change(s) could not be appended" in out
    assert table_ids(db) == ["CNT_05_01"]
    assert len(db.unwritten_audit_lines) == 1


# ------------------------------------------------------------- phase refresh
def test_phase_refresh_waits_for_the_real_commit(db, monkeypatch):
    from mousedb import backfill

    refreshed = []
    monkeypatch.setattr(backfill, "backfill_phases",
                        lambda db=None, cohort_id=None, **kw: refreshed.append(cohort_id))

    with db.session() as s:
        s.add(Project(project_code="CNT", project_name="test project"))
        s.flush()
        with s.begin_nested():
            s.add(Cohort(cohort_id="CNT_05", project_code="CNT",
                         start_date=dt.date(2026, 1, 1)))
        # Released, not committed: a refresh now would write on its own
        # connection and wait on this transaction's lock.
        assert refreshed == []

    assert refreshed == ["CNT_05"]
