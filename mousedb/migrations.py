"""
One-shot schema migrations that add derived/computed columns to existing tables.

Each migration is idempotent: it checks whether the column already exists before
running ``ALTER TABLE``. Generated columns do not need backfilling because
SQLite computes their values on the fly from source columns.

Run via::

    python -m mousedb.migrations add-contact-group

or programmatically::

    from mousedb.migrations import add_contact_group_columns
    added = add_contact_group_columns()
"""

from __future__ import annotations

from typing import List, Sequence

from sqlalchemy import text

from .database import Database, get_db


# --- contact_group expressions --------------------------------------------

REACH_CONTACT_EXPR = (
    "CASE WHEN interaction_frame IS NOT NULL THEN 'contacted' ELSE 'missed' END"
)

REACH_SEGMENT_CONTACT_EXPR = (
    "CASE WHEN segment_outcome IN ('retrieved', 'displaced_sa', 'displaced_outside') "
    "THEN 'contacted' ELSE 'missed' END"
)

PELLET_CONTACT_EXPR = "CASE WHEN score = 0 THEN 'missed' ELSE 'contacted' END"


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
    args = parser.parse_args(argv)

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
