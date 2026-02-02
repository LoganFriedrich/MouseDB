"""
Database connection and session management for Connectome Data Entry.
"""

import os
import json
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from . import DEFAULT_DB_PATH, DEFAULT_LOG_PATH
from .schema import Base, create_default_projects, create_default_tray_types, AuditLog


class Database:
    """Database connection manager with audit logging."""

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Defaults to Y:/2_Connectome/MouseDB/connectome.db
        """
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.log_path = DEFAULT_LOG_PATH

        # Ensure directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.mkdir(parents=True, exist_ok=True)

        # Create engine and session factory
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            echo=False,
            connect_args={"check_same_thread": False}  # For multi-threaded GUI
        )
        self.SessionLocal = sessionmaker(bind=self.engine)

        # Current user for audit logging
        self._current_user = os.environ.get('USERNAME', os.environ.get('USER', 'unknown'))

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

        Args:
            session: Active database session
            action: INSERT, UPDATE, or DELETE
            table_name: Name of the affected table
            record_id: Primary key of the affected record
            old_values: Previous values (for UPDATE/DELETE)
            new_values: New values (for INSERT/UPDATE)
        """
        log_entry = AuditLog(
            user=self.current_user,
            action=action,
            table_name=table_name,
            record_id=str(record_id),
            old_values=json.dumps(old_values) if old_values else None,
            new_values=json.dumps(new_values) if new_values else None,
        )
        session.add(log_entry)

        # Also write to daily log file
        log_file = self.log_path / f"{datetime.now().strftime('%Y-%m-%d')}_changes.jsonl"
        with open(log_file, 'a') as f:
            f.write(json.dumps({
                'timestamp': datetime.now().isoformat(),
                'user': self.current_user,
                'action': action,
                'table': table_name,
                'record_id': str(record_id),
                'old': old_values,
                'new': new_values,
            }) + '\n')

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
