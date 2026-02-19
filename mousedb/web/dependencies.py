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
    """Get the WatcherBridge singleton (lazy-initialized)."""
    global _watcher_bridge
    if _watcher_bridge is None:
        _watcher_bridge = WatcherBridge()
    return _watcher_bridge
