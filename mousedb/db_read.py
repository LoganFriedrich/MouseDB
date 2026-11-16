"""Open mousedb's own database for reading, even right after a writer was stopped.

WHY
---
The database is in rollback-journal mode. When a writer is stopped part-way
through a save (a scheduled job ended at its time limit, a window closed, a
test killed), its unfinished save is left beside the database as a journal
file. The next connection has to undo that save before anything can be read.
A read-only connection (``mode=ro``) is not allowed to do that, so it fails
with "attempt to write a readonly database" -- and every read-only job then
fails until some ordinary connection happens to come along and undo it. On one
night that left the hand-scores file a day old although the sheet import
itself had landed.

So a read here is tried read-only first, and on exactly that refusal it is
opened again as an ordinary connection, which undoes the unfinished save the
same way any writer's next open would, and is then limited to reads
(``PRAGMA query_only``) so the code using it still cannot change a row.

Only for mousedb's OWN database. Other tools' files (MouseReach's watcher
database, its version index) are opened strictly read-only elsewhere and are
never written by mousedb, not even to undo an unfinished save: that is the
owning tool's job.
"""

import sqlite3
from pathlib import Path


def connect_for_reading(db_file, timeout: float = 600) -> sqlite3.Connection:
    """A connection to ``db_file`` that can only read, and that works even when
    a stopped writer left an unfinished save beside the file. The caller closes it."""
    path = Path(db_file)
    con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=timeout)
    try:
        # Reading the schema is the first thing that touches the file, so this
        # is where an unfinished save is refused.
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return con
    except sqlite3.OperationalError as e:
        con.close()
        if "readonly" not in str(e).lower():
            raise
    con = sqlite3.connect(str(path), timeout=timeout)
    con.execute("PRAGMA query_only = ON")
    return con
