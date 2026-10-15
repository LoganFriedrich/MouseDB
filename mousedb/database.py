"""
Database connection and session management for Connectome Data Entry.
"""

import os
import json
import threading
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from . import DEFAULT_DB_PATH, DEFAULT_LOG_PATH
from .schema import Base, create_default_projects, create_default_tray_types, AuditLog


def _begin_before_savepoint(conn, name):
    """Open a real transaction before a SAVEPOINT if the driver has not.

    WHY: Python's sqlite3 driver only starts a transaction (BEGIN) in front
    of an INSERT/UPDATE/DELETE. When a SAVEPOINT is the first statement of a
    transaction -- session.begin_nested() before anything was written --
    SQLite treats the SAVEPOINT itself as the transaction, and releasing it
    COMMITS. The surrounding rollback then undoes nothing, while the ORM (and
    the audit hooks below) believe it undid everything: data that was meant
    to be discarded stays in the database. Issuing BEGIN first makes the
    SAVEPOINT nest inside the real transaction, as the ORM assumes.

    Only transactions that use SAVEPOINTs are touched; every other
    transaction, and its locking, is exactly as before.
    """
    dbapi_conn = conn.connection.dbapi_connection
    if not dbapi_conn.in_transaction:
        dbapi_conn.execute("BEGIN")


class Database:
    """Database connection manager with audit logging."""

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to the configured
                     database (mousedb config --set mousedb_root <folder>).
        """
        from .config import require
        self.db_path = Path(db_path) if db_path else require("db_path")
        self.log_path = DEFAULT_LOG_PATH or (self.db_path.parent / "logs")

        # Ensure directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)

        # Create engine and session factory.
        #
        # Busy timeout: the database lives on a network share in rollback-journal
        # mode, where the hourly snapshot's reads take minutes and a bulk
        # phase re-derivation commits for minutes. SQLite's default wait is 5 s,
        # so any overlap made one job fail with "database is locked" -- the
        # snapshot went stale and phase assignment silently skipped
        # (2026-09-01). Batch jobs now wait long enough to serialize; the GUI
        # sets MOUSEDB_DB_TIMEOUT low so a window never freezes for minutes.
        import os
        timeout = int(os.environ.get("MOUSEDB_DB_TIMEOUT", "600"))
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False,  # For multi-threaded GUI
                          "timeout": timeout}
        )
        # Without this, a begin_nested() that opens a transaction commits on
        # release and the outer rollback undoes nothing (see the function).
        event.listen(self.engine, 'savepoint', _begin_before_savepoint)
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Audit file lines wait on the session until its transaction commits
        # (see _register_audit_hooks). The lock keeps two threads' committed
        # batches from interleaving inside one day file (the GUI is
        # multi-threaded). unwritten_audit_lines holds any committed line the
        # file append refused, so a caller can report it instead of losing it.
        self._audit_file_lock = threading.Lock()
        self.unwritten_audit_lines: list = []
        # Registered BEFORE the phase hooks: both run on after_commit, and the
        # record of a committed change should be on disk before a phase
        # refresh that can take minutes (or die with the process).
        self._register_audit_hooks()

        # Auto-refresh phase_group / test_phase after any GUI/ORM write that
        # could invalidate them (cohort start_date, subject cohort_id,
        # pellet_scores session_date / tray_type).
        self._register_phase_hooks()

        # Current user for audit logging
        self._current_user = os.environ.get('USERNAME', os.environ.get('USER', 'unknown'))

    def _register_phase_hooks(self):
        """Install SQLAlchemy session events that auto-refresh phase columns.

        before_flush collects cohort IDs whose phase assignments could be
        invalidated by the pending changes. after_commit runs a scoped
        backfill for those cohorts so the GUI (and any other ORM writer)
        never has to remember to call backfill_phases manually.

        Raw-SQL writers that bypass the ORM (e.g. mousereach-sync) are not
        covered here -- they have their own post-write hook.
        """
        from sqlalchemy import event, inspect as sa_inspect
        from .schema import Cohort, Subject, PelletScore

        def _collect_dirty_cohorts(session, flush_context, instances):
            dirty = session.info.setdefault('_phases_dirty_cohorts', set())

            for obj in list(session.new) + list(session.dirty) + list(session.deleted):
                if isinstance(obj, Cohort):
                    if obj in session.new or obj in session.deleted:
                        if obj.cohort_id:
                            dirty.add(obj.cohort_id)
                    else:
                        hist = sa_inspect(obj).attrs.start_date.history
                        if hist.has_changes() and obj.cohort_id:
                            dirty.add(obj.cohort_id)

                elif isinstance(obj, Subject):
                    if obj in session.new or obj in session.deleted:
                        if obj.cohort_id:
                            dirty.add(obj.cohort_id)
                    else:
                        hist = sa_inspect(obj).attrs.cohort_id.history
                        if hist.has_changes():
                            # Both old and new cohorts need re-assignment.
                            for v in hist.deleted or ():
                                if v:
                                    dirty.add(v)
                            if obj.cohort_id:
                                dirty.add(obj.cohort_id)

                elif isinstance(obj, PelletScore):
                    insp = sa_inspect(obj)
                    relevant_changed = (
                        obj in session.new
                        or obj in session.deleted
                        or insp.attrs.session_date.history.has_changes()
                        or insp.attrs.tray_type.history.has_changes()
                        or insp.attrs.subject_id.history.has_changes()
                    )
                    if relevant_changed and obj.subject_id:
                        # Resolve subject -> cohort via the in-flight session.
                        subj = session.query(Subject).filter_by(
                            subject_id=obj.subject_id
                        ).first()
                        if subj and subj.cohort_id:
                            dirty.add(subj.cohort_id)

        def _run_scoped_backfill(session):
            # SQLAlchemy also fires after_commit when a SAVEPOINT is released.
            # That change is not committed yet, and the outer transaction
            # still holds the SQLite write lock, so a backfill here (it writes
            # on its own connection) would wait out the whole busy timeout and
            # then fail. Keep the dirty set for the real commit instead.
            if session.in_nested_transaction():
                return
            dirty = session.info.pop('_phases_dirty_cohorts', None)
            if not dirty:
                return
            # Local import to avoid circular-import at module load.
            from .backfill import backfill_phases
            for cid in dirty:
                try:
                    backfill_phases(db=self, cohort_id=cid)
                except Exception as exc:
                    # Don't raise -- the user's commit already succeeded.
                    # Print so the GUI shell surfaces it.
                    print(
                        f"[!] Phase auto-refresh failed for cohort {cid}: {exc}"
                    )

        event.listen(self.SessionLocal, 'before_flush', _collect_dirty_cohorts)
        event.listen(self.SessionLocal, 'after_commit', _run_scoped_backfill)

    # Key in session.info where log_change parks file lines until a commit.
    # Each entry is (transaction, file_name, line): the transaction says which
    # rollback would undo the change, the file name is fixed when the change
    # is logged so a line lands in the file of the day it was made.
    _AUDIT_PENDING_KEY = '_audit_pending_lines'

    def _register_audit_hooks(self):
        """Install session events that write audit file lines only after a commit.

        WHY: log_change records a change twice -- an AuditLog row in the
        database and a line in logs/<date>_changes.jsonl. The row lives and
        dies with its transaction; a file append does not. Appending at call
        time meant a rolled-back transaction left a line claiming a change the
        database never received: a false audit trail. So log_change parks the
        line on the session, and these hooks decide its fate:

        - after_commit: append every parked line once the change is durable,
          one append per day file for the whole transaction rather than one
          file open per change (an import logs thousands of changes, and the
          log folder is usually on a network share). SQLAlchemy also fires
          after_commit when a SAVEPOINT is released; that is NOT durable --
          the outer transaction can still roll back -- so it is ignored and
          the lines stay parked.
        - after_soft_rollback: drop the lines whose changes this rollback
          undid -- everything, or only those logged inside the SAVEPOINT that
          was rolled back (and any SAVEPOINT nested within it).
        - after_transaction_end of the outermost transaction: drop whatever is
          still parked. A commit has written its lines by then, so anything
          left belongs to a transaction that ended without committing (for
          example session.close() with no commit) and must never reach the
          file, nor leak into a later commit on the same session.
        """
        key = self._AUDIT_PENDING_KEY

        def _write_after_commit(session):
            if session.in_nested_transaction():
                return  # SAVEPOINT release: not durable yet (see docstring)
            pending = session.info.pop(key, None)
            if pending:
                self._append_audit_lines(pending)

        def _drop_rolled_back(session, previous_transaction):
            pending = session.info.get(key)
            if not pending:
                return
            # The database undoes the nearest SAVEPOINT or the outermost
            # transaction at or above the one rollback was called on; a
            # flush's internal sub-transaction is only a marker inside one of
            # those, so climb to the boundary that actually rolled back.
            boundary = previous_transaction
            while not boundary.nested and boundary.parent is not None:
                boundary = boundary.parent
            kept = [entry for entry in pending
                    if not _is_inside(entry[0], boundary)]
            if kept:
                session.info[key] = kept
            else:
                session.info.pop(key, None)

        def _drop_uncommitted(session, transaction):
            # Only the outermost transaction's end means "this session's
            # changes are settled"; a SAVEPOINT ending is handled above.
            if transaction.parent is None:
                session.info.pop(key, None)

        def _is_inside(tagged, boundary):
            # True when the transaction a line was logged in is the rolled
            # back one or nested somewhere within it.
            t = tagged
            while t is not None:
                if t is boundary:
                    return True
                t = t.parent
            return False

        event.listen(self.SessionLocal, 'after_commit', _write_after_commit)
        event.listen(self.SessionLocal, 'after_soft_rollback', _drop_rolled_back)
        event.listen(self.SessionLocal, 'after_transaction_end', _drop_uncommitted)

    def _append_audit_lines(self, pending):
        """Append committed audit lines: one open and one write per day file.

        Runs inside after_commit, when the database change is ALREADY
        committed. It must not raise: an exception here would surface from
        session.commit() and the caller would report a failed save for data
        that was in fact saved. A refused append is printed and kept in
        self.unwritten_audit_lines instead; the AuditLog rows in the database
        are unaffected, so only the file copy is missing.
        """
        by_file = {}
        for _txn, file_name, line in pending:
            by_file.setdefault(file_name, []).append(line)
        for file_name, lines in by_file.items():
            log_file = self.log_path / file_name
            try:
                with self._audit_file_lock:
                    # The folder is created at start-up, but it lives on a
                    # share someone can tidy away while a long job runs.
                    log_file.parent.mkdir(parents=True, exist_ok=True)
                    # One write call so a transaction's lines stay together
                    # even if another process appends to the same day file.
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(''.join(lines))
            except Exception as exc:
                self.unwritten_audit_lines.extend(lines)
                message = (
                    f"[!] Audit log: {len(lines)} committed change(s) could not "
                    f"be appended to {log_file}: {exc}. The changes are saved "
                    f"and recorded in the audit_log table; only the file copy "
                    f"is missing."
                )
                # The folder path and the system's error text can hold
                # characters a legacy code page cannot print (a user name, a
                # localised message). print() would then raise from inside
                # the commit -- exactly what this function must not do -- so
                # escape to plain ASCII.
                message = message.encode('ascii', 'backslashreplace').decode('ascii')
                try:
                    print(message)
                except Exception:
                    # A closed or broken stdout must not fail a saved change
                    # either; the lines are still in unwritten_audit_lines.
                    pass

    def init_db(self):
        """Create all tables, run migrations, and seed default data."""
        Base.metadata.create_all(self.engine)

        # Run migrations for any missing columns
        self._run_migrations()

        with self.session() as session:
            create_default_projects(session)
            create_default_tray_types(session)
        print(f"Database initialized at: {self.db_path}")

    def _run_migrations(self):
        """Check for and add any missing columns to existing tables."""
        from sqlalchemy import text, inspect

        # Define migrations: (table_name, column_name, column_definition)
        migrations = [
            ('surgeries', 'pre_surgery_weight_grams', 'REAL'),
            ('surgeries', 'dwell_time_s', 'REAL'),
            ('surgeries', 'anesthesia', 'VARCHAR(100)'),
            ('surgeries', 'survived', 'INTEGER DEFAULT 1'),
            # Protocol System columns
            ('cohorts', 'protocol_id', 'INTEGER REFERENCES protocols(id)'),
            ('cohorts', 'protocol_version', 'INTEGER'),
            ('cohorts', 'is_archived', 'INTEGER DEFAULT 0'),
            ('cohorts', 'archived_at', 'TIMESTAMP'),
            ('cohorts', 'archived_reason', 'TEXT'),
        ]

        inspector = inspect(self.engine)

        for table_name, column_name, column_def in migrations:
            # Check if table exists
            if table_name not in inspector.get_table_names():
                continue

            # Get existing columns
            existing_columns = [col['name'] for col in inspector.get_columns(table_name)]

            # Add column if missing
            if column_name not in existing_columns:
                try:
                    with self.engine.connect() as conn:
                        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"))
                        conn.commit()
                    print(f"  Migration: Added {table_name}.{column_name}")
                except Exception as e:
                    # Column might already exist or other issue - log but don't fail
                    print(f"  Migration warning: {e}")

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager for database sessions with automatic commit/rollback.

        Usage:
            with db.session() as session:
                session.add(...)
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            raise
        finally:
            session.close()

    def set_user(self, username: str):
        """Set the current user for audit logging."""
        self._current_user = username

    @property
    def current_user(self) -> str:
        """Get the current user for audit logging."""
        return self._current_user

    def log_change(self, session: Session, action: str, table_name: str,
                   record_id: str, old_values: dict = None, new_values: dict = None):
        """
        Log a data change for audit trail.

        Records the change twice: an AuditLog row added to ``session``, and a
        line in ``<log_path>/<date>_changes.jsonl``. The row commits or rolls
        back with the change it describes. The file line is NOT written here:
        it is held on the session and appended only after that session's
        transaction commits (see _register_audit_hooks). If the transaction
        rolls back, or the session is closed without committing, neither
        record survives -- the file can never claim a change the database does
        not hold. Every line from one commit is appended in a single write.

        Args:
            session: Active database session. It must come from this Database
                (``db.session()`` or ``db.SessionLocal()``): only those carry
                the commit hooks that write the file line.
            action: INSERT, UPDATE, or DELETE
            table_name: Name of the affected table
            record_id: Primary key of the affected record
            old_values: Previous values (for UPDATE/DELETE); JSON-serialisable
            new_values: New values (for INSERT/UPDATE); JSON-serialisable

        Raises:
            TypeError: ``session`` was not made by this Database. Its file line
                would never be written (or would be written by another
                Database into its own log folder), so refuse rather than drop
                it silently.
        """
        if not isinstance(session, self.SessionLocal.class_):
            raise TypeError(
                "log_change needs a session from this Database "
                "(db.session() or db.SessionLocal()); any other session has "
                "no commit hook to write the audit file line."
            )

        # One timestamp for both records so the table row and the file line
        # can be matched exactly (the row's own default would be the later
        # flush time).
        now = datetime.now()
        # Serialise before touching the session: a value JSON cannot encode
        # fails here, in the caller's hands, and leaves no half-logged change
        # -- rather than inside the commit, after the data is saved.
        old_json = json.dumps(old_values) if old_values else None
        new_json = json.dumps(new_values) if new_values else None
        line = json.dumps({
            'timestamp': now.isoformat(),
            'user': self.current_user,
            'action': action,
            'table': table_name,
            'record_id': str(record_id),
            'old': old_values,
            'new': new_values,
        }) + '\n'

        log_entry = AuditLog(
            timestamp=now,
            user=self.current_user,
            action=action,
            table_name=table_name,
            record_id=str(record_id),
            old_values=old_json,
            new_values=new_json,
        )
        # add() begins the session's transaction if none is open yet, so the
        # transaction lookup below always finds the one this row belongs to.
        session.add(log_entry)

        # Tag the line with the innermost transaction that can be rolled back
        # on its own (a SAVEPOINT if one is open, else the whole transaction),
        # so a rollback drops exactly the lines whose changes it undid.
        txn = session.get_nested_transaction() or session.get_transaction()
        file_name = f"{now.strftime('%Y-%m-%d')}_changes.jsonl"
        session.info.setdefault(self._AUDIT_PENDING_KEY, []).append(
            (txn, file_name, line))

    def get_stats(self) -> dict:
        """Get database statistics."""
        from .schema import (Project, Cohort, Subject, Weight, PelletScore, Surgery,
                             RampEntry, LadderEntry, VirusPrep, ArchivedSummary)
        with self.session() as session:
            return {
                'projects': session.query(Project).count(),
                'cohorts': session.query(Cohort).count(),
                'subjects': session.query(Subject).count(),
                'weights': session.query(Weight).count(),
                'pellet_scores': session.query(PelletScore).count(),
                'surgeries': session.query(Surgery).count(),
                'ramp_entries': session.query(RampEntry).count(),
                'ladder_entries': session.query(LadderEntry).count(),
                'virus_preps': session.query(VirusPrep).count(),
                'archived_summaries': session.query(ArchivedSummary).count(),
                'db_path': str(self.db_path),
                'db_size_mb': self.db_path.stat().st_size / (1024 * 1024) if self.db_path.exists() else 0,
            }

    def backup(self, backup_path: Optional[Path] = None) -> Path:
        """
        Create a backup of the database.

        Args:
            backup_path: Destination path. Defaults to timestamped backup in same directory.

        Returns:
            Path to the backup file.
        """
        import shutil
        if backup_path is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = self.db_path.parent / f"connectome_backup_{timestamp}.db"

        shutil.copy2(self.db_path, backup_path)
        print(f"Backup created: {backup_path}")
        return backup_path


# Global database instance
_db: Optional[Database] = None


def get_db(db_path: Optional[Path] = None) -> Database:
    """Get or create the global database instance."""
    global _db
    if _db is None or (db_path is not None and _db.db_path != Path(db_path)):
        _db = Database(db_path)
    return _db


def init_database(db_path: Optional[Path] = None) -> Database:
    """Initialize the database and return the instance."""
    db = get_db(db_path)
    db.init_db()
    return db
