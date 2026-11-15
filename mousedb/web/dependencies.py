"""FastAPI dependency injection for database sessions and services."""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy.orm import Session

from ..database import get_db, Database
from ..watcher_bridge import WatcherBridge, find_watcher_db


# Singleton instances
_watcher_bridge: WatcherBridge = None


def get_database() -> Database:
    """Get the global Database instance."""
    return get_db()


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a database session with auto-commit/rollback."""
    db = get_db()
    session = db.SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_watcher() -> WatcherBridge:
    """A WatcherBridge resolved afresh for this request.

    WHY not a singleton: the bridge decides at creation which watcher
    database to read (find_watcher_db) and when it was last written. A
    server left running kept the answer from its start: a database that
    appeared or moved later was never seen. The page's scope line (which
    file, last written when) is re-fetched every 30 s from /dashboard/scope,
    which calls this, so it follows the file. Resolving costs one small
    config-file read.
    """
    global _watcher_bridge
    _watcher_bridge = WatcherBridge()
    return _watcher_bridge
