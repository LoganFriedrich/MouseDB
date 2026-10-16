"""
One-shot migration and backfill for test_phase / phase_group columns.

Run once after deploying the phases-detection feature; the nightly reach
import also runs it for every cohort a new video touches. Idempotent: safe to
re-run. Every row ends up with the canonical label from :mod:`mousedb.phases`
(legacy spellings such as `Rehab_1` or `Post-Injury Test 4` are replaced), but
a row whose label is already right is NOT rewritten -- see
:func:`_apply_assignments` for why that matters on a network share.

Steps:
  1. ALTER TABLE pellet_scores ADD COLUMN phase_group TEXT (if missing).
  2. ALTER TABLE reach_data ADD COLUMN test_phase TEXT (if missing).
  3. ALTER TABLE reach_data ADD COLUMN phase_group TEXT (if missing).
  4. For each cohort with a start_date:
       - collect the union of (session_date, tray_type) pairs from BOTH
         pellet_scores and reach_data;
       - run :func:`mousedb.phases.assign_phases_for_cohort`;
       - update both tables with the derived (test_phase, phase_group).

Invoke via CLI::

    mousedb backfill-phases [--dry-run]
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Sequence, Tuple

from sqlalchemy import text

from .database import Database, get_db
from .phases import assign_phases_for_cohort


@dataclass
class BackfillStats:
    columns_added: List[str]
    cohorts_processed: int
    pellet_rows_updated: int
    reach_rows_updated: int
    unknown_subjects: int
    skipped_cohorts: List[str]


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(r[1] == column for r in rows)


def _ensure_column(conn, table: str, column: str, col_type: str = "TEXT") -> bool:
    if _column_exists(conn, table, column):
        return False
    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
    return True


def _to_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _collect_cohort_dates(conn, cohort_id: str) -> List[Tuple[date, str]]:
    """
    Return (session_date, tray_type) pairs from pellet_scores for a cohort.

    pellet_scores is the authoritative record of protocol testing days.
    reach_data may contain extra dates (e.g., video captures during the
    injury+recovery window that have no pellet scoring) that would distort
    gap-structure detection, so it is intentionally excluded here. reach_data
    rows pick up phase labels by joining to pellet_scores' (cohort, session_date)
    in :func:`_apply_assignments`.
    """
    pellet_rows = conn.execute(
        text(
            """
            SELECT DISTINCT ps.session_date, ps.tray_type
            FROM pellet_scores ps
            JOIN subjects s ON s.subject_id = ps.subject_id
            WHERE s.cohort_id = :cohort AND ps.tray_type IS NOT NULL
            """
        ),
        {"cohort": cohort_id},
    ).fetchall()
    return [(_to_date(d), (t or "").upper()) for d, t in pellet_rows]


UNSCHEDULED = "Unscheduled"


def _apply_assignments(
    conn, cohort_id: str, assignments, *, dry_run: bool
) -> Tuple[int, int]:
    """
    Bring this cohort's pellet_scores and reach_data rows to the derived
    labels, writing ONLY the rows whose label is not already right.

    End state (the same since the first version of this function): every
    reach_data row of the cohort carries its session date's (test_phase,
    phase_group) when that date is a scored test day, and 'Unscheduled' when
    it is not (a video captured during injury and recovery, for example).
    pellet_scores rows carry their date's label; every one of their dates is a
    scored day by definition, since the schedule is derived from them.

    WHY ONLY THE ROWS THAT CHANGE: the first version cleared every row of the
    cohort and rewrote it in three passes. Correct, but the database lives on
    a network share that takes writes at a fraction of a megabyte per second,
    so that was gigabytes of page writes to say what the rows already said.
    The nightly reach import runs this for every cohort a new video touches;
    on 2026-10-15 five reprocessed videos of the largest cohort made it rewrite
    every reach of that cohort, it ran for hours past its scheduler limit, and
    it held the database through the brain import and the export refresh,
    which failed night after night. With the guard below, a night on which the
    labels did not change writes nothing.

    AND ONLY ONE PASS OVER EACH TABLE: reach_data has no index on session_date
    or subject_id (only on video and on the labels), so an UPDATE per test date
    read the whole multi-gigabyte table once per date -- 42 GB of reads in 40
    minutes on 2026-10-16, for one cohort. The date -> label map goes into a
    temporary table and each row looks its own date up there, so a cohort costs
    one read of the table however many test dates it has.

    Returns (pellet_rows_changed, reach_rows_changed): rows actually written.
    """
    if not assignments:
        return 0, 0

    # The map every row consults. TEMP tables are private to this connection
    # and vanish with it; dropped explicitly so a long session can call this
    # for many cohorts.
    conn.execute(text("DROP TABLE IF EXISTS temp.phase_map"))
    conn.execute(text("CREATE TEMP TABLE phase_map (d TEXT PRIMARY KEY, tp TEXT NOT NULL, pg TEXT NOT NULL)"))
    conn.execute(
        text("INSERT OR REPLACE INTO phase_map (d, tp, pg) VALUES (:d, :tp, :pg)"),
        [{"d": a.session_date.isoformat(), "tp": a.test_phase, "pg": a.phase_group} for a in assignments],
    )

    cohort_subjects = "SELECT subject_id FROM subjects WHERE cohort_id = :cohort"
    params = {"cohort": cohort_id, "unscheduled": UNSCHEDULED}

    # reach_data: a row on a scored day takes that day's label; any other day
    # (a recording during injury and recovery, an unparseable date) is
    # 'Unscheduled'. SQLite's IS NOT compares NULL-safely, so a row whose label
    # is still NULL counts as different and gets written.
    tp_of = "(SELECT m.tp FROM phase_map m WHERE m.d = reach_data.session_date)"
    pg_of = "(SELECT m.pg FROM phase_map m WHERE m.d = reach_data.session_date)"
    reach_where = (
        f"subject_id IN ({cohort_subjects}) AND "
        f"(test_phase IS NOT COALESCE({tp_of}, :unscheduled) OR phase_group IS NOT COALESCE({pg_of}, :unscheduled))"
    )
    reach_set = f"test_phase = COALESCE({tp_of}, :unscheduled), phase_group = COALESCE({pg_of}, :unscheduled)"

    # pellet_scores: every scored day is in the map by construction; a row on
    # a day that is not (no tray type recorded) is left alone, as it always was.
    ptp_of = "(SELECT m.tp FROM phase_map m WHERE m.d = pellet_scores.session_date)"
    ppg_of = "(SELECT m.pg FROM phase_map m WHERE m.d = pellet_scores.session_date)"
    pellet_where = (
        f"subject_id IN ({cohort_subjects}) AND session_date IN (SELECT d FROM phase_map) AND "
        f"(test_phase IS NOT {ptp_of} OR phase_group IS NOT {ppg_of})"
    )
    pellet_set = f"test_phase = {ptp_of}, phase_group = {ppg_of}"

    try:
        if dry_run:
            pellet_count = conn.execute(text(f"SELECT COUNT(*) FROM pellet_scores WHERE {pellet_where}"),
                                        params).scalar() or 0
            reach_count = conn.execute(text(f"SELECT COUNT(*) FROM reach_data WHERE {reach_where}"),
                                       params).scalar() or 0
        else:
            pellet_count = conn.execute(text(f"UPDATE pellet_scores SET {pellet_set} WHERE {pellet_where}"),
                                        params).rowcount or 0
            reach_count = conn.execute(text(f"UPDATE reach_data SET {reach_set} WHERE {reach_where}"),
                                       params).rowcount or 0
    finally:
        conn.execute(text("DROP TABLE IF EXISTS temp.phase_map"))

    return pellet_count, reach_count


def backfill_phases(
    db: Database | None = None,
    *,
    dry_run: bool = False,
    cohort_id: str | None = None,
) -> BackfillStats:
    """Add columns and assign phases across the whole database.

    Args:
        cohort_id: If provided, only re-assign phases for this one cohort.
            Used by the GUI post-commit hook to keep per-edit latency low.
    """
    db = db or get_db()
    stats = BackfillStats(
        columns_added=[],
        cohorts_processed=0,
        pellet_rows_updated=0,
        reach_rows_updated=0,
        unknown_subjects=0,
        skipped_cohorts=[],
    )

    with db.engine.begin() as conn:
        for table, col in [
            ("pellet_scores", "phase_group"),
            ("reach_data", "test_phase"),
            ("reach_data", "phase_group"),
        ]:
            if _ensure_column(conn, table, col):
                stats.columns_added.append(f"{table}.{col}")

        if cohort_id is not None:
            cohort_rows = conn.execute(
                text(
                    """
                    SELECT cohort_id, start_date FROM cohorts
                    WHERE start_date IS NOT NULL AND cohort_id = :cid
                    """
                ),
                {"cid": cohort_id},
            ).fetchall()
        else:
            cohort_rows = conn.execute(
                text(
                    """
                    SELECT cohort_id, start_date FROM cohorts
                    WHERE start_date IS NOT NULL
                    ORDER BY cohort_id
                    """
                )
            ).fetchall()

        for cohort_id, start_date in cohort_rows:
            pairs = _collect_cohort_dates(conn, cohort_id)
            if not pairs:
                stats.skipped_cohorts.append(cohort_id)
                continue

            assignments = assign_phases_for_cohort(pairs)
            p_updated, r_updated = _apply_assignments(
                conn, cohort_id, assignments, dry_run=dry_run
            )
            stats.pellet_rows_updated += p_updated
            stats.reach_rows_updated += r_updated
            stats.cohorts_processed += 1

        stats.unknown_subjects = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM pellet_scores ps
                LEFT JOIN subjects s ON s.subject_id = ps.subject_id
                WHERE s.cohort_id IS NULL
                """
            )
        ).scalar() or 0

    return stats


def print_stats(stats: BackfillStats) -> None:
    print("=" * 60)
    print("Phase backfill complete")
    print("=" * 60)
    if stats.columns_added:
        print(f"Columns added: {', '.join(stats.columns_added)}")
    else:
        print("Columns added: (all already present)")
    print(f"Cohorts processed:      {stats.cohorts_processed}")
    print(f"Cohorts skipped (no test dates): {len(stats.skipped_cohorts)}")
    if stats.skipped_cohorts:
        print(f"  -> {', '.join(stats.skipped_cohorts)}")
    print(f"pellet_scores rows updated: {stats.pellet_rows_updated}")
    print(f"reach_data rows updated:    {stats.reach_rows_updated}")
    if stats.unknown_subjects:
        print(f"[!] pellet_scores rows with unknown subject_id: {stats.unknown_subjects}")


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill test_phase and phase_group across pellet_scores and reach_data."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Count rows that would be updated without modifying the database.",
    )
    args = parser.parse_args(argv)

    # One sequential read of the file first, held open across every cohort,
    # so the per-cohort table scans run from memory (see mousedb.prefetch).
    from .database import get_db
    from .prefetch import warmed
    db = get_db()
    with warmed(db.db_path, log=print):
        stats = backfill_phases(db, dry_run=args.dry_run)
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
