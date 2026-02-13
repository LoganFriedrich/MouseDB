"""
Export functions for Connectome Data Entry.

Supports:
- Excel export (for legacy tools)
- Parquet export (for unified database)
- ODC-compatible export (203-column format)
"""

import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, date

from .database import Database, get_db
from .schema import Project, Cohort, Subject, Weight, PelletScore, Surgery
from .stats import (
    calculate_daily_stats, calculate_subject_summary, calculate_cohort_summary,
    get_cohort_overview, DailyStats
)


def export_cohort_to_excel(db: Database, cohort_id: str,
                           output_path: Optional[Path] = None) -> Path:
    """
    Export a cohort to Excel format matching the legacy tracking sheet structure.

    Args:
        db: Database instance
        cohort_id: Cohort to export
        output_path: Output path. Defaults to exports directory.

    Returns:
        Path to the exported file
    """
    from . import DEFAULT_EXPORT_PATH

    if output_path is None:
        DEFAULT_EXPORT_PATH.mkdir(parents=True, exist_ok=True)
        output_path = DEFAULT_EXPORT_PATH / f"{cohort_id}_tracking.xlsx"

    with db.session() as session:
        # Get cohort
        cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        if not cohort:
            raise ValueError(f"Cohort not found: {cohort_id}")

        # Get subjects
        subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
        subject_ids = [s.subject_id for s in subjects]

        # Create Excel writer
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # 0a_Metadata sheet
            metadata_data = [{
                'SubjectID': s.subject_id,
                'Date_of_Birth': s.date_of_birth,
                'Date_of_Death': s.date_of_death,
                'Sex': s.sex,
                'Cohort': s.cohort_id,
                'Notes': s.notes,
            } for s in subjects]
            pd.DataFrame(metadata_data).to_excel(writer, sheet_name='0a_Metadata', index=False)

            # 1_Weight sheet
            weights = session.query(Weight).filter(
                Weight.subject_id.in_(subject_ids)
            ).order_by(Weight.date, Weight.subject_id).all()

            weight_data = [{
                'Date': w.date,
                'Animal': w.subject_id,
                'Weight': w.weight_grams,
                'Weight %': w.weight_percent,
                'Notes': w.notes,
            } for w in weights]
            pd.DataFrame(weight_data).to_excel(writer, sheet_name='1_Weight', index=False)

            # 3b_Manual_Tray sheet
            pellets = session.query(PelletScore).filter(
                PelletScore.subject_id.in_(subject_ids)
            ).order_by(PelletScore.session_date, PelletScore.subject_id,
                      PelletScore.tray_number).all()

            # Group by session/animal/tray to create rows
            tray_rows = {}
            for p in pellets:
                key = (p.session_date, p.subject_id, p.tray_type, p.tray_number, p.test_phase)
                if key not in tray_rows:
                    tray_rows[key] = {
                        'Date': p.session_date,
                        'Animal': p.subject_id,
                        'Test_Phase': p.test_phase,
                        'Tray Type/Number': f"{p.tray_type}{p.tray_number}",
                    }
                    for i in range(1, 21):
                        tray_rows[key][str(i)] = None
                tray_rows[key][str(p.pellet_number)] = p.score

            tray_data = list(tray_rows.values())
            if tray_data:
                df = pd.DataFrame(tray_data)
                # Reorder columns
                cols = ['Date', 'Animal', 'Test_Phase', 'Tray Type/Number'] + [str(i) for i in range(1, 21)]
                df = df[[c for c in cols if c in df.columns]]
                df.to_excel(writer, sheet_name='3b_Manual_Tray', index=False)

            # 4_Contusion_Injury_Details sheet
            contusions = session.query(Surgery).filter(
                Surgery.subject_id.in_(subject_ids),
                Surgery.surgery_type == 'contusion'
            ).all()

            if contusions:
                contusion_data = [{
                    'Date': s.surgery_date,
                    'Animal': s.subject_id,
                    'Force_kDyn': s.force_kdyn,
                    'Displacement_um': s.displacement_um,
                    'Velocity_mm_s': s.velocity_mm_s,
                    'Surgeon': s.surgeon,
                    'Notes': s.notes,
                } for s in contusions]
                pd.DataFrame(contusion_data).to_excel(
                    writer, sheet_name='4_Contusion_Injury_Details', index=False)

    print(f"Exported {cohort_id} to: {output_path}")
    return output_path


def export_odc_format(db: Database, cohort_id: str,
                      output_path: Optional[Path] = None) -> Path:
    """
    Export a cohort to ODC-compatible Excel format (203 columns).

    This is the format used by legacy analysis tools, with one row per animal-session
    and extensive calculated columns.

    Args:
        db: Database instance
        cohort_id: Cohort to export
        output_path: Output path. Defaults to exports directory.

    Returns:
        Path to the exported file
    """
    from . import DEFAULT_EXPORT_PATH

    if output_path is None:
        DEFAULT_EXPORT_PATH.mkdir(parents=True, exist_ok=True)
        output_path = DEFAULT_EXPORT_PATH / f"{cohort_id}_ODC.xlsx"

    with db.session() as session:
        cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        if not cohort:
            raise ValueError(f"Cohort not found: {cohort_id}")

        summaries = calculate_cohort_summary(session, cohort_id)

        rows = []
        for subject_id, summary in sorted(summaries.items()):
            for sess in summary.sessions:
                row = _build_odc_row(summary, sess)
                rows.append(row)

        if rows:
            df = pd.DataFrame(rows)
            df.to_excel(output_path, sheet_name='2_ODC_Animal_Tracking', index=False)
            print(f"Exported ODC format to: {output_path}")
            print(f"  Rows: {len(rows)}, Columns: {len(df.columns)}")
        else:
            print(f"No data to export for {cohort_id}")

    return output_path


def _build_odc_row(summary, sess: DailyStats) -> Dict[str, Any]:
    """Build a single ODC row with all calculated columns."""
    row = {
        # Basic info
        'Animal': summary.subject_id,
        'Cohort': summary.cohort_id,
        'Sex': summary.sex,
        'Date': sess.session_date,
        'Test_Phase': sess.test_phase,
        'Days_Post_Injury': sess.days_post_injury,

        # Weight
        'Weight_g': sess.weight_grams,
        'Weight_Pct': sess.weight_pct,

        # Injury info
        'Injury_Date': summary.injury_date,
        'Injury_Force_kDyn': summary.injury_force_kdyn,
        'Injury_Displacement_um': summary.injury_displacement_um,
    }

    # Per-tray stats (Tray 1-4)
    for i, tray in enumerate(sess.trays, 1):
        prefix = f'Tray{i}'
        row[f'{prefix}_Type'] = tray.tray_type
        row[f'{prefix}_Presented'] = tray.presented
        row[f'{prefix}_Missed'] = tray.miss
        row[f'{prefix}_Displaced'] = tray.displaced
        row[f'{prefix}_Retrieved'] = tray.retrieved
        row[f'{prefix}_Contacted'] = tray.contacted
        row[f'{prefix}_Miss_Pct'] = tray.miss_pct
        row[f'{prefix}_Displaced_Pct'] = tray.displaced_pct
        row[f'{prefix}_Retrieved_Pct'] = tray.retrieved_pct
        row[f'{prefix}_Contacted_Pct'] = tray.contacted_pct

        # Individual pellet scores
        # Note: This would need access to raw pellet data, which we have in the session
        # For now, we'll skip individual pellet columns

    # Fill missing trays (if less than 4)
    for i in range(len(sess.trays) + 1, 5):
        prefix = f'Tray{i}'
        row[f'{prefix}_Type'] = None
        row[f'{prefix}_Presented'] = 0
        row[f'{prefix}_Missed'] = 0
        row[f'{prefix}_Displaced'] = 0
        row[f'{prefix}_Retrieved'] = 0
        row[f'{prefix}_Contacted'] = 0
        row[f'{prefix}_Miss_Pct'] = 0.0
        row[f'{prefix}_Displaced_Pct'] = 0.0
        row[f'{prefix}_Retrieved_Pct'] = 0.0
        row[f'{prefix}_Contacted_Pct'] = 0.0

    # Daily totals
    row['Daily_Presented'] = sess.total_presented
    row['Daily_Missed'] = sess.total_miss
    row['Daily_Displaced'] = sess.total_displaced
    row['Daily_Retrieved'] = sess.total_retrieved
    row['Daily_Contacted'] = sess.total_contacted

    # Daily percentages
    row['Daily_Miss_Pct'] = sess.miss_pct
    row['Daily_Displaced_Pct'] = sess.displaced_pct
    row['Daily_Retrieved_Pct'] = sess.retrieved_pct
    row['Daily_Contacted_Pct'] = sess.contacted_pct

    # Daily averages across trays
    row['Avg_Miss_Pct'] = sess.avg_miss_pct
    row['Avg_Displaced_Pct'] = sess.avg_displaced_pct
    row['Avg_Retrieved_Pct'] = sess.avg_retrieved_pct
    row['Avg_Contacted_Pct'] = sess.avg_contacted_pct

    return row


def export_unified_to_parquet(db: Database, output_path: Optional[Path] = None) -> Path:
    """
    Export unified reaches dataset to Parquet format.

    This creates the final analysis dataset with one row per reach,
    including all metadata, kinematics, and BrainGlobe data joined.

    Args:
        db: Database instance
        output_path: Output path. Defaults to MouseDB directory.

    Returns:
        Path to the exported file
    """
    from . import DEFAULT_EXPORT_PATH

    if output_path is None:
        output_path = DEFAULT_EXPORT_PATH / "unified_reaches.parquet"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # For now, export a summary of all data
    # The full unified export will integrate MouseReach pipeline data

    with db.session() as session:
        # Get all subjects with their metadata
        subjects = session.query(Subject).all()

        # Build subject metadata
        subject_data = []
        for s in subjects:
            cohort = session.query(Cohort).filter_by(cohort_id=s.cohort_id).first()

            # Get contusion surgery details
            contusion = session.query(Surgery).filter_by(
                subject_id=s.subject_id, surgery_type='contusion'
            ).first()

            subject_data.append({
                'subject_id': s.subject_id,
                'cohort_id': s.cohort_id,
                'project_code': cohort.project_code if cohort else None,
                'cohort_start_date': cohort.start_date if cohort else None,
                'sex': s.sex,
                'date_of_birth': s.date_of_birth,
                'date_of_death': s.date_of_death,
                'injury_date': contusion.surgery_date if contusion else None,
                'injury_force_kdyn': contusion.force_kdyn if contusion else None,
                'injury_displacement_um': contusion.displacement_um if contusion else None,
            })

        df = pd.DataFrame(subject_data)

        # Calculate session summaries per subject
        session_summaries = []
        for s in subjects:
            # Get pellet score summaries by session
            pellets = session.query(PelletScore).filter_by(subject_id=s.subject_id).all()

            # Group by session date
            sessions = {}
            for p in pellets:
                if p.session_date not in sessions:
                    sessions[p.session_date] = {
                        'subject_id': s.subject_id,
                        'session_date': p.session_date,
                        'test_phase': p.test_phase,
                        'total_pellets': 0,
                        'miss_count': 0,
                        'displaced_count': 0,
                        'retrieved_count': 0,
                    }
                sessions[p.session_date]['total_pellets'] += 1
                if p.score == 0:
                    sessions[p.session_date]['miss_count'] += 1
                elif p.score == 1:
                    sessions[p.session_date]['displaced_count'] += 1
                elif p.score == 2:
                    sessions[p.session_date]['retrieved_count'] += 1

            for sess in sessions.values():
                total = sess['total_pellets']
                if total > 0:
                    sess['success_rate'] = sess['retrieved_count'] / total
                    sess['skill_rate'] = (sess['displaced_count'] + sess['retrieved_count']) / total
                session_summaries.append(sess)

        sessions_df = pd.DataFrame(session_summaries)

        # Merge subject metadata with sessions
        if not sessions_df.empty and not df.empty:
            unified_df = sessions_df.merge(df, on='subject_id', how='left')
        else:
            unified_df = df

        # Save to parquet
        unified_df.to_parquet(output_path, index=False)
        print(f"Exported unified data to: {output_path}")
        print(f"  Rows: {len(unified_df)}")

    return output_path


def export_all_formats(db: Database, cohort_id: str,
                       output_dir: Optional[Path] = None) -> Dict[str, Path]:
    """
    Export a cohort in all available formats.

    Args:
        db: Database instance
        cohort_id: Cohort to export
        output_dir: Output directory. Defaults to exports directory.

    Returns:
        Dictionary mapping format name to output path
    """
    from . import DEFAULT_EXPORT_PATH

    if output_dir is None:
        output_dir = DEFAULT_EXPORT_PATH

    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}

    # Legacy Excel format
    results['excel'] = export_cohort_to_excel(
        db, cohort_id,
        output_dir / f"{cohort_id}_tracking.xlsx"
    )

    # ODC format
    results['odc'] = export_odc_format(
        db, cohort_id,
        output_dir / f"{cohort_id}_ODC.xlsx"
    )

    print(f"\nExported {cohort_id} to all formats:")
    for fmt, path in results.items():
        print(f"  {fmt}: {path}")

    return results


# =============================================================================
# FLEXIBLE QUERY EXPORTER
# =============================================================================

class QueryExporter:
    """
    Fluent API for flexible data queries and exports.

    Build custom queries by chaining filter methods, then export to various formats.

    Example:
        QueryExporter(db) \\
            .cohorts(["CNT_01", "CNT_02"]) \\
            .phases(["Post-Injury_1", "Post-Injury_2"]) \\
            .score_equals(2) \\
            .include_weights() \\
            .include_injury_data() \\
            .to_csv("my_analysis.csv")

    Advanced filtering:
        QueryExporter(db) \\
            .where_subject("CAST(SUBSTR(subject_id, -2) AS INTEGER) % 2 = 0") \\
            .where_cohort("CAST(SUBSTR(cohort_id, -2) AS INTEGER) % 2 = 0") \\
            .phases_containing("Post-Injury") \\
            .to_dataframe()
    """

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self._reset()

    def _reset(self):
        """Reset all filters."""
        self._cohort_ids: Optional[List[str]] = None
        self._subject_ids: Optional[List[str]] = None
        self._phases: Optional[List[str]] = None
        self._phases_containing: Optional[List[str]] = None
        self._scores: Optional[List[int]] = None
        self._date_from: Optional[date] = None
        self._date_to: Optional[date] = None
        self._custom_where: List[str] = []
        self._subject_where: List[str] = []
        self._cohort_where: List[str] = []

        # Include flags
        self._include_weights = False
        self._include_injury = False
        self._include_brainglobe = False
        self._include_subject_metadata = True

        # Aggregation
        self._aggregate_by: Optional[str] = None  # 'session', 'subject', 'cohort'

    # === COHORT/SUBJECT FILTERS ===

    def cohorts(self, cohort_ids: List[str]) -> 'QueryExporter':
        """Filter to specific cohorts."""
        self._cohort_ids = cohort_ids
        return self

    def cohort(self, cohort_id: str) -> 'QueryExporter':
        """Filter to a single cohort."""
        self._cohort_ids = [cohort_id]
        return self

    def subjects(self, subject_ids: List[str]) -> 'QueryExporter':
        """Filter to specific subjects."""
        self._subject_ids = subject_ids
        return self

    def subject(self, subject_id: str) -> 'QueryExporter':
        """Filter to a single subject."""
        self._subject_ids = [subject_id]
        return self

    def where_subject(self, sql_condition: str) -> 'QueryExporter':
        """
        Add custom SQL condition on subject.

        Example: where_subject("CAST(SUBSTR(subject_id, -2) AS INTEGER) % 2 = 0")
        """
        self._subject_where.append(sql_condition)
        return self

    def where_cohort(self, sql_condition: str) -> 'QueryExporter':
        """
        Add custom SQL condition on cohort.

        Example: where_cohort("cohort_id LIKE 'CNT%'")
        """
        self._cohort_where.append(sql_condition)
        return self

    def even_subjects(self) -> 'QueryExporter':
        """Filter to even-numbered subjects only."""
        self._subject_where.append("CAST(SUBSTR(subject_id, -2) AS INTEGER) % 2 = 0")
        return self

    def odd_subjects(self) -> 'QueryExporter':
        """Filter to odd-numbered subjects only."""
        self._subject_where.append("CAST(SUBSTR(subject_id, -2) AS INTEGER) % 2 = 1")
        return self

    def even_cohorts(self) -> 'QueryExporter':
        """Filter to even-numbered cohorts only."""
        self._cohort_where.append("CAST(SUBSTR(cohort_id, -2) AS INTEGER) % 2 = 0")
        return self

    def odd_cohorts(self) -> 'QueryExporter':
        """Filter to odd-numbered cohorts only."""
        self._cohort_where.append("CAST(SUBSTR(cohort_id, -2) AS INTEGER) % 2 = 1")
        return self

    # === PHASE FILTERS ===

    def phases(self, phase_names: List[str]) -> 'QueryExporter':
        """Filter to specific phases (exact match)."""
        self._phases = phase_names
        return self

    def phase(self, phase_name: str) -> 'QueryExporter':
        """Filter to a single phase."""
        self._phases = [phase_name]
        return self

    def phases_containing(self, substring: str) -> 'QueryExporter':
        """Filter to phases containing a substring (e.g., 'Post-Injury')."""
        if self._phases_containing is None:
            self._phases_containing = []
        self._phases_containing.append(substring)
        return self

    def pre_injury(self) -> 'QueryExporter':
        """Filter to pre-injury phases only."""
        return self.phases_containing('Pre-Injury')

    def post_injury(self) -> 'QueryExporter':
        """Filter to post-injury phases only."""
        return self.phases_containing('Post-Injury')

    def training(self) -> 'QueryExporter':
        """Filter to training phases only."""
        return self.phases_containing('Training')

    # === SCORE FILTERS ===

    def scores(self, score_values: List[int]) -> 'QueryExporter':
        """Filter to specific score values."""
        self._scores = score_values
        return self

    def score_equals(self, score: int) -> 'QueryExporter':
        """Filter to a specific score value."""
        self._scores = [score]
        return self

    def retrieved_only(self) -> 'QueryExporter':
        """Filter to retrieved pellets only (score=2)."""
        return self.score_equals(2)

    def contacted_only(self) -> 'QueryExporter':
        """Filter to contacted pellets (score=1 or 2)."""
        return self.scores([1, 2])

    def missed_only(self) -> 'QueryExporter':
        """Filter to missed pellets only (score=0)."""
        return self.score_equals(0)

    # === DATE FILTERS ===

    def date_range(self, from_date: date, to_date: date) -> 'QueryExporter':
        """Filter to a date range."""
        self._date_from = from_date
        self._date_to = to_date
        return self

    def after_date(self, from_date: date) -> 'QueryExporter':
        """Filter to sessions after a date."""
        self._date_from = from_date
        return self

    def before_date(self, to_date: date) -> 'QueryExporter':
        """Filter to sessions before a date."""
        self._date_to = to_date
        return self

    # === CUSTOM WHERE ===

    def where(self, sql_condition: str) -> 'QueryExporter':
        """
        Add custom SQL WHERE condition on pellet_scores table.

        Example: where("tray_number = 1")
        """
        self._custom_where.append(sql_condition)
        return self

    # === INCLUDE RELATED DATA ===

    def include_weights(self) -> 'QueryExporter':
        """Include weight data for each session."""
        self._include_weights = True
        return self

    def include_injury_data(self) -> 'QueryExporter':
        """Include injury (contusion) data for each subject."""
        self._include_injury = True
        return self

    def include_brainglobe(self) -> 'QueryExporter':
        """Include BrainGlobe cell counts for each subject."""
        self._include_brainglobe = True
        return self

    def include_all(self) -> 'QueryExporter':
        """Include all related data."""
        self._include_weights = True
        self._include_injury = True
        self._include_brainglobe = True
        return self

    def exclude_subject_metadata(self) -> 'QueryExporter':
        """Exclude subject metadata (sex, DOB, etc.)."""
        self._include_subject_metadata = False
        return self

    # === AGGREGATION ===

    def aggregate_by_session(self) -> 'QueryExporter':
        """Aggregate pellet scores to session level."""
        self._aggregate_by = 'session'
        return self

    def aggregate_by_subject(self) -> 'QueryExporter':
        """Aggregate all data to subject level."""
        self._aggregate_by = 'subject'
        return self

    def aggregate_by_cohort(self) -> 'QueryExporter':
        """Aggregate all data to cohort level."""
        self._aggregate_by = 'cohort'
        return self

    # === BUILD QUERY ===

    def _build_query(self) -> str:
        """Build the SQL query from filters."""
        # Base query - pellet scores with subject info
        select_cols = [
            "ps.id as pellet_id",
            "ps.subject_id",
            "ps.session_date",
            "ps.test_phase",
            "ps.tray_type",
            "ps.tray_number",
            "ps.pellet_number",
            "ps.score",
        ]

        joins = ["FROM pellet_scores ps"]
        where_clauses = []

        # Add subject metadata
        if self._include_subject_metadata:
            select_cols.extend([
                "s.cohort_id",
                "s.sex",
                "s.date_of_birth",
                "s.date_of_death",
                "s.is_active",
            ])
            joins.append("JOIN subjects s ON ps.subject_id = s.subject_id")

        # Cohort filters
        if self._cohort_ids:
            placeholders = ", ".join(f"'{c}'" for c in self._cohort_ids)
            where_clauses.append(f"s.cohort_id IN ({placeholders})")

        for cond in self._cohort_where:
            where_clauses.append(f"({cond})")

        # Subject filters
        if self._subject_ids:
            placeholders = ", ".join(f"'{s}'" for s in self._subject_ids)
            where_clauses.append(f"ps.subject_id IN ({placeholders})")

        for cond in self._subject_where:
            where_clauses.append(f"({cond})")

        # Phase filters
        if self._phases:
            placeholders = ", ".join(f"'{p}'" for p in self._phases)
            where_clauses.append(f"ps.test_phase IN ({placeholders})")

        if self._phases_containing:
            phase_conds = [f"ps.test_phase LIKE '%{p}%'" for p in self._phases_containing]
            where_clauses.append(f"({' OR '.join(phase_conds)})")

        # Score filters
        if self._scores is not None:
            placeholders = ", ".join(str(s) for s in self._scores)
            where_clauses.append(f"ps.score IN ({placeholders})")

        # Date filters
        if self._date_from:
            where_clauses.append(f"ps.session_date >= '{self._date_from}'")
        if self._date_to:
            where_clauses.append(f"ps.session_date <= '{self._date_to}'")

        # Custom WHERE
        for cond in self._custom_where:
            where_clauses.append(f"({cond})")

        # Build query
        query = f"SELECT {', '.join(select_cols)}\n"
        query += "\n".join(joins)

        if where_clauses:
            query += f"\nWHERE {' AND '.join(where_clauses)}"

        query += "\nORDER BY ps.subject_id, ps.session_date, ps.tray_number, ps.pellet_number"

        return query

    def _execute_query(self) -> pd.DataFrame:
        """Execute the query and return a DataFrame."""
        query = self._build_query()

        with self.db.session() as session:
            df = pd.read_sql(query, session.bind)

            # Add weights if requested
            if self._include_weights and not df.empty:
                weight_df = pd.read_sql("""
                    SELECT subject_id, date as session_date, weight_grams, weight_percent
                    FROM weights
                """, session.bind)
                if not weight_df.empty:
                    df = df.merge(weight_df, on=['subject_id', 'session_date'], how='left')

            # Add injury data if requested
            if self._include_injury and not df.empty:
                injury_df = pd.read_sql("""
                    SELECT subject_id,
                           surgery_date as injury_date,
                           force_kdyn as injury_force_kdyn,
                           displacement_um as injury_displacement_um,
                           velocity_mm_s as injury_velocity_mm_s,
                           survived as injury_survived
                    FROM surgeries
                    WHERE surgery_type = 'contusion'
                """, session.bind)
                if not injury_df.empty:
                    df = df.merge(injury_df, on='subject_id', how='left')

            # Add BrainGlobe data if requested
            if self._include_brainglobe and not df.empty:
                try:
                    bg_df = pd.read_sql("""
                        SELECT
                            bs.subject_id,
                            COUNT(DISTINCT rc.region_id) as brain_regions_with_cells,
                            SUM(rc.cell_count) as brain_total_cells
                        FROM brain_samples bs
                        LEFT JOIN region_counts rc ON bs.id = rc.brain_sample_id
                        WHERE rc.is_final = 1
                        GROUP BY bs.subject_id
                    """, session.bind)
                    if not bg_df.empty:
                        df = df.merge(bg_df, on='subject_id', how='left')
                except Exception:
                    pass  # BrainGlobe tables may not exist yet

            # Apply aggregation if requested
            if self._aggregate_by:
                df = self._apply_aggregation(df)

        return df

    def _apply_aggregation(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply aggregation to the DataFrame."""
        if df.empty:
            return df

        if self._aggregate_by == 'session':
            # Aggregate to session level
            group_cols = ['subject_id', 'session_date', 'test_phase']
            if 'cohort_id' in df.columns:
                group_cols.insert(1, 'cohort_id')

            agg_dict = {
                'score': ['count', 'sum', 'mean'],
                'pellet_id': 'count',
            }

            # Add optional columns to groupby if present
            for col in ['sex', 'injury_force_kdyn', 'injury_displacement_um',
                        'weight_grams', 'weight_percent']:
                if col in df.columns:
                    agg_dict[col] = 'first'

            result = df.groupby(group_cols).agg(agg_dict).reset_index()
            result.columns = ['_'.join(col).strip('_') if isinstance(col, tuple) else col
                             for col in result.columns]

            # Calculate percentages
            if 'score_count' in result.columns and 'score_sum' in result.columns:
                result['retrieved_count'] = df[df['score'] == 2].groupby(group_cols).size().reindex(
                    result.set_index(group_cols).index, fill_value=0).values
                result['contacted_count'] = df[df['score'].isin([1, 2])].groupby(group_cols).size().reindex(
                    result.set_index(group_cols).index, fill_value=0).values
                result['total_pellets'] = result['score_count']
                result['retrieved_pct'] = (result['retrieved_count'] / result['total_pellets'] * 100).round(2)
                result['contacted_pct'] = (result['contacted_count'] / result['total_pellets'] * 100).round(2)

            return result

        elif self._aggregate_by == 'subject':
            # Aggregate to subject level
            group_cols = ['subject_id']
            if 'cohort_id' in df.columns:
                group_cols.append('cohort_id')

            agg_dict = {
                'session_date': 'nunique',
                'score': ['count', 'mean'],
            }

            for col in ['sex', 'injury_force_kdyn', 'injury_displacement_um']:
                if col in df.columns:
                    agg_dict[col] = 'first'

            result = df.groupby(group_cols).agg(agg_dict).reset_index()
            result.columns = ['_'.join(col).strip('_') if isinstance(col, tuple) else col
                             for col in result.columns]

            return result

        elif self._aggregate_by == 'cohort':
            # Aggregate to cohort level
            if 'cohort_id' not in df.columns:
                raise ValueError("Cannot aggregate by cohort without cohort_id column")

            agg_dict = {
                'subject_id': 'nunique',
                'session_date': 'count',
                'score': ['count', 'mean'],
            }

            result = df.groupby('cohort_id').agg(agg_dict).reset_index()
            result.columns = ['_'.join(col).strip('_') if isinstance(col, tuple) else col
                             for col in result.columns]

            return result

        return df

    # === OUTPUT METHODS ===

    def to_dataframe(self) -> pd.DataFrame:
        """Execute query and return as pandas DataFrame."""
        return self._execute_query()

    def to_csv(self, output_path: str, **kwargs) -> Path:
        """Execute query and save to CSV."""
        df = self._execute_query()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False, **kwargs)
        print(f"Exported {len(df)} rows to: {path}")
        return path

    def to_excel(self, output_path: str, sheet_name: str = 'Data', **kwargs) -> Path:
        """Execute query and save to Excel."""
        df = self._execute_query()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(path, sheet_name=sheet_name, index=False, **kwargs)
        print(f"Exported {len(df)} rows to: {path}")
        return path

    def to_parquet(self, output_path: str, **kwargs) -> Path:
        """Execute query and save to Parquet."""
        df = self._execute_query()
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False, **kwargs)
        print(f"Exported {len(df)} rows to: {path}")
        return path

    def preview(self, n: int = 10) -> pd.DataFrame:
        """Preview the first n rows of the result."""
        df = self._execute_query()
        print(f"Query returns {len(df)} rows, {len(df.columns)} columns")
        print(f"Columns: {list(df.columns)}")
        return df.head(n)

    def count(self) -> int:
        """Return the count of rows that would be returned."""
        df = self._execute_query()
        return len(df)

    def describe(self) -> pd.DataFrame:
        """Return descriptive statistics of the result."""
        df = self._execute_query()
        return df.describe()

    def show_sql(self) -> str:
        """Show the SQL query that will be executed (for debugging)."""
        query = self._build_query()
        print(query)
        return query
