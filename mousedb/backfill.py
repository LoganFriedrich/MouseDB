"""
One-shot migration and backfill for test_phase / phase_group columns.

Run once after deploying the phases-detection feature. Idempotent: safe to
re-run. Always overwrites existing values so legacy inconsistent labels
(`Rehab_1`, `Post-Injury Test 4`, etc.) are normalized to the canonical
vocabulary from :mod:`mousedb.phases`.

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


def _apply_assignments(
    conn, cohort_id: str, assignments, *, dry_run: bool
) -> Tuple[int, int]:
    """
    Update pellet_scores and reach_data for this cohort with derived labels.

    Strategy:
      1. Clear test_phase/phase_group for all cohort rows in both tables so
         legacy/stale values don't persist for dates no longer in the schedule.
      2. For each (session_date -> phase) assignment, UPDATE matching rows.
      3. Sweep any remaining NULL rows (dates in reach_data with no pellet
         scoring, e.g. video captures during injury+recovery) to 'Unscheduled'.

    Returns (pellet_rows_updated, reach_rows_updated) -- including the clear
    and sweep passes (so totals reflect all rows touched).
    """
    if not assignments:
        return 0, 0

    cohort_subjects_sql = "SELECT subject_id FROM subjects WHERE cohort_id = :cohort"
    params_base = {"cohort": cohort_id}

    pellet_count = 0
    reach_count = 0

    if dry_run:
        p_rows = conn.execute(
            text(
                f"""
                SELECT COUNT(*) FROM pellet_scores
                WHERE subject_id IN ({cohort_subjects_sql})
                """
            ),
            params_base,
        ).scalar() or 0
        r_rows = conn.execute(
            text(
                f"""
                SELECT COUNT(*) FROM reach_data
                WHERE subject_id IN ({cohort_subjects_sql})
                """
            ),
            params_base,
        ).scalar() or 0
        return p_rows, r_rows

    # Clear reach_data only -- its columns are nullable. pellet_scores.test_phase
    # has a legacy NOT NULL constraint we can't easily drop in SQLite, but every
    # pellet_scores row has a session_date that IS in `assignments` (since the
    # assignments are derived from pellet_scores), so the subsequent per-date
    # UPDATEs overwrite every row anyway.
    conn.execute(
        text(
            f"""
            UPDATE reach_data SET test_phase = NULL, phase_group = NULL
            WHERE subject_id IN ({cohort_subjects_sql})
            """
        ),
        params_base,
    )

    for a in assignments:
        params = {
            **params_base,
            "sd": a.session_date,
            "sd_str": a.session_date.isoformat(),
            "tp": a.test_phase,
            "pg": a.phase_group,
        }
        p_result = conn.execute(
            text(
                f"""
                UPDATE pellet_scores
                SET test_phase = :tp, phase_group = :pg
                WHERE session_date = :sd
                  AND subject_id IN ({cohort_subjects_sql})
                """
            ),
            params,
        )
        r_result = conn.execute(
            text(
                f"""
                UPDATE reach_data
                SET test_phase = :tp, phase_group = :pg
                WHERE session_date = :sd_str
                  AND subject_id IN ({cohort_subjects_sql})
                """
            ),
            params,
        )
        pellet_count += p_result.rowcount or 0
        reach_count += r_result.rowcount or 0

    # Sweep remaining NULL reach_data rows (dates with no pellet scoring, e.g.
    # video captures during the injury+recovery window) to Unscheduled.
    r_sweep = conn.execute(
        text(
            f"""
            UPDATE reach_data
            SET test_phase = 'Unscheduled', phase_group = 'Unscheduled'
            WHERE test_phase IS NULL
              AND subject_id IN ({cohort_subjects_sql})
            """
        ),
        params_base,
    )
    reach_count += r_sweep.rowcount or 0

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

    stats = backfill_phases(dry_run=args.dry_run)
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
