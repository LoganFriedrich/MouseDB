"""
One-shot schema migrations that add derived/computed columns to existing tables.

Each migration is idempotent: it checks whether the column already exists before
running ``ALTER TABLE``. Generated columns do not need backfilling because
SQLite computes their values on the fly from source columns.

Run via::

    python -m mousedb.migrations add-contact-group
    python -m mousedb.migrations contact-columns       # bring them to the current definition

or programmatically::

    from mousedb.migrations import add_contact_group_columns
    added = add_contact_group_columns()

The contact columns' definitions live in ``mousedb.schema`` (one place, so a new
database and an old one end up identical). ``bring_contact_columns_current``
replaces an older definition in an existing database; the nightly reach import
calls it too, so the database corrects itself on its next run.
"""

from __future__ import annotations

import sqlite3
from typing import List, Sequence

from sqlalchemy import text

from .database import Database, get_db
from .schema import PELLET_CONTACT_EXPR, REACH_CONTACT_EXPR, REACH_SEGMENT_CONTACT_EXPR


# --- contact_group expressions (defined in schema.py, which says why) ---------

CONTACT_COLUMNS = (
    ("reach_data", "contact_group", REACH_CONTACT_EXPR),
    ("reach_data", "segment_contact_group", REACH_SEGMENT_CONTACT_EXPR),
    ("pellet_scores", "contact_group", PELLET_CONTACT_EXPR),
)
# The columns each definition reads. A table without them (a partial test or
# scratch table) is left alone rather than given a column it cannot compute.
_SOURCES = {
    REACH_CONTACT_EXPR: ("tray_type", "interaction_frame"),
    REACH_SEGMENT_CONTACT_EXPR: ("tray_type", "segment_outcome"),
    PELLET_CONTACT_EXPR: ("score",),
}


def bring_contact_columns_current(conn: sqlite3.Connection) -> List[str]:
    """Give each contact column its current definition; the columns changed.

    A column whose table already carries the current expression is left alone,
    so this is safe to run on every import. A VIRTUAL generated column stores
    nothing, so SQLite drops and re-adds it by rewriting the schema alone --
    instant even on the 5 GB reach table (timed 2026-10-26: 0.01 s each on a
    559 MB table of a million rows). Call it inside a transaction, holding the
    cross-task lock (task_mutex), like any other write."""
    changed: List[str] = []
    for table, column, expr in CONTACT_COLUMNS:
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                           (table,)).fetchone()
        if not row or expr in (row[0] or ""):
            continue
        # table_xinfo, not table_info: it is the one that lists generated columns.
        have = {r[1] for r in conn.execute("PRAGMA table_xinfo(%s)" % table)}
        if not set(_SOURCES[expr]) <= have:
            continue
        if column in have:
            conn.execute("ALTER TABLE %s DROP COLUMN %s" % (table, column))
        conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT GENERATED ALWAYS AS (%s) VIRTUAL"
                     % (table, column, expr))
        changed.append("%s.%s" % (table, column))
    return changed


# --- helpers ---------------------------------------------------------------

def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _add_virtual_generated(
    conn, table: str, column: str, expr: str, col_type: str = "TEXT"
) -> bool:
    """Add a VIRTUAL generated column if absent. SQLite's ALTER TABLE supports
    only VIRTUAL (not STORED) generated columns on existing tables."""
    if _column_exists(conn, table, column):
        return False
    conn.execute(
        text(
            f"ALTER TABLE {table} ADD COLUMN {column} {col_type} "
            f"GENERATED ALWAYS AS ({expr}) VIRTUAL"
        )
    )
    return True


# --- migrations ------------------------------------------------------------

def add_contact_group_columns(db: Database | None = None) -> List[str]:
    """
    Add contact_group (and, on reach_data, segment_contact_group) as VIRTUAL
    generated columns. Returns the list of fully-qualified columns actually
    added (empty if all were already present).
    """
    db = db or get_db()
    added: List[str] = []
    with db.engine.begin() as conn:
        if _add_virtual_generated(
            conn, "reach_data", "contact_group", REACH_CONTACT_EXPR
        ):
            added.append("reach_data.contact_group")
        if _add_virtual_generated(
            conn, "reach_data", "segment_contact_group", REACH_SEGMENT_CONTACT_EXPR
        ):
            added.append("reach_data.segment_contact_group")
        if _add_virtual_generated(
            conn, "pellet_scores", "contact_group", PELLET_CONTACT_EXPR
        ):
            added.append("pellet_scores.contact_group")
    return added


# --- CLI -------------------------------------------------------------------

def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Run mousedb schema migrations (idempotent)."
    )
    sub = parser.add_subparsers(dest="migration", required=True)
    sub.add_parser(
        "add-contact-group",
        help="Add contact_group / segment_contact_group virtual columns.",
    )
    sub.add_parser(
        "contact-columns",
        help="Give the contact columns their current definition (easy/flat empty; "
             "a pellet nobody touched is 'untouched').",
    )
    args = parser.parse_args(argv)

    if args.migration == "contact-columns":
        from .task_mutex import hold
        db = get_db()
        with hold(waiting_for="another database task"):
            conn = sqlite3.connect(str(db.db_path), timeout=600)
            try:
                with conn:
                    changed = bring_contact_columns_current(conn)
            finally:
                conn.close()
        print("Redefined: " + ", ".join(changed) if changed
              else "Contact columns already current; nothing to do.")
        return 0

    if args.migration == "add-contact-group":
        added = add_contact_group_columns()
        if added:
            print("Added columns:")
            for col in added:
                print(f"  {col}")
        else:
            print("Columns already present; nothing to do.")
        return 0

    parser.error(f"Unknown migration: {args.migration}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
