"""
Validation helpers for Connectome Data Entry.

These validators are used by both the database schema and the GUI
to provide consistent, user-friendly error messages.
"""

import re
from datetime import date
from typing import Dict, Tuple, Optional, List


class ValidationError(Exception):
    """Custom exception for validation errors with user-friendly messages."""

    def __init__(self, field: str, value, message: str):
        self.field = field
        self.value = value
        self.message = message
        super().__init__(f"{field}: {message}")


# =============================================================================
# ID VALIDATORS
# =============================================================================

SUBJECT_ID_PATTERN = re.compile(r'^[A-Z]+_\d{2}_\d{2}$')
COHORT_ID_PATTERN = re.compile(r'^[A-Z]+_\d{2}$')
PROJECT_CODE_PATTERN = re.compile(r'^[A-Z]+$')


def validate_subject_id(value: str) -> Tuple[bool, str]:
    """
    Validate subject ID format.

    Args:
        value: Subject ID to validate

    Returns:
        (is_valid, error_message)
    """
    if not value:
        return False, "Subject ID is required"
    value = value.upper().strip()
    if not SUBJECT_ID_PATTERN.match(value):
        return False, f"Subject ID must be format XXX_NN_NN (e.g., CNT_05_01), got: {value}"
    return True, ""


def validate_cohort_id(value: str) -> Tuple[bool, str]:
    """Validate cohort ID format."""
    if not value:
        return False, "Cohort ID is required"
    value = value.upper().strip()
    if not COHORT_ID_PATTERN.match(value):
        return False, f"Cohort ID must be format XXX_NN (e.g., CNT_05), got: {value}"
    return True, ""


def validate_project_code(value: str) -> Tuple[bool, str]:
    """Validate project code format."""
    if not value:
        return False, "Project code is required"
    value = value.upper().strip()
    if not PROJECT_CODE_PATTERN.match(value):
        return False, f"Project code must be uppercase letters only (e.g., CNT), got: {value}"
    return True, ""


# Compact animal ID format: letters followed by 4+ digits (e.g., CNT0115)
_COMPACT_ID_PATTERN = re.compile(r'^([A-Za-z]+)(\d{2})(\d{2})$')


def compact_id_to_subject_id(animal_id: str) -> Optional[str]:
    """
    Convert compact animal ID (CNT0115) to database format (CNT_01_15).

    Returns None if the ID cannot be parsed.
    """
    if not animal_id:
        return None
    animal_id = animal_id.strip()

    # Already in database format?
    if SUBJECT_ID_PATTERN.match(animal_id.upper()):
        return animal_id.upper()

    match = _COMPACT_ID_PATTERN.match(animal_id)
    if match:
        return f"{match.group(1).upper()}_{match.group(2)}_{match.group(3)}"

    return None


def validate_animal_ids(animal_ids: List[str],
                        db_path=None) -> Dict[str, dict]:
    """
    Check which animal IDs exist in the subjects table.

    Called by mousereach.watcher during video validation (Hook 2).
    Gracefully handles database unavailability.

    Args:
        animal_ids: List of IDs in any format (CNT0115 or CNT_01_15)
        db_path: Optional database path override

    Returns:
        Dict mapping each input ID to:
        {'exists': bool, 'subject_id': str or None, 'message': str}
    """
    results = {}

    # Convert all IDs to database format first
    for raw_id in animal_ids:
        subject_id = compact_id_to_subject_id(raw_id)
        if subject_id is None:
            results[raw_id] = {
                'exists': False,
                'subject_id': None,
                'message': f"Cannot parse animal ID: {raw_id}",
            }
        else:
            valid, msg = validate_subject_id(subject_id)
            if not valid:
                results[raw_id] = {
                    'exists': False,
                    'subject_id': subject_id,
                    'message': msg,
                }
            else:
                results[raw_id] = {
                    'exists': False,  # Will be updated below
                    'subject_id': subject_id,
                    'message': '',
                }

    # Query database for existence
    ids_to_check = [
        r['subject_id'] for r in results.values()
        if r['subject_id'] is not None and r['message'] == ''
    ]

    if not ids_to_check:
        return results

    try:
        from .database import init_database
        from .schema import Subject

        db = init_database(db_path)
        with db.session() as session:
            existing = set(
                row.subject_id for row in
                session.query(Subject.subject_id)
                .filter(Subject.subject_id.in_(ids_to_check))
                .all()
            )

        for raw_id, info in results.items():
            sid = info['subject_id']
            if sid and sid in existing:
                info['exists'] = True
                info['message'] = f"Subject {sid} found"
            elif sid and info['message'] == '':
                info['message'] = f"Subject {sid} not found in database"

    except Exception as e:
        for raw_id, info in results.items():
            if info['subject_id'] and info['message'] == '':
                info['message'] = f"Database check failed: {e}"

    return results


# =============================================================================
# DATA VALIDATORS
# =============================================================================

def validate_weight(value: float) -> Tuple[bool, str]:
    """
    Validate weight measurement.

    Args:
        value: Weight in grams

    Returns:
        (is_valid, error_message)
    """
    if value is None:
        return False, "Weight is required"
    if value <= 0:
        return False, f"Weight must be positive, got: {value}"
    if value >= 100:
        return False, f"Weight must be less than 100g (check units), got: {value}"
    if value < 10:
        return False, f"Weight seems too low (check units), got: {value}g"
    if value > 50:
        return False, f"Weight seems too high for a mouse, got: {value}g"
    return True, ""


def validate_pellet_score(value: int) -> Tuple[bool, str]:
    """
    Validate pellet score.

    Args:
        value: Score (0=miss, 1=displaced, 2=retrieved)

    Returns:
        (is_valid, error_message)
    """
    if value is None:
        return False, "Pellet score is required"
    if value not in (0, 1, 2):
        return False, f"Score must be 0 (miss), 1 (displaced), or 2 (retrieved), got: {value}"
    return True, ""


def validate_tray_type(value: str) -> Tuple[bool, str]:
    """Validate tray type."""
    if not value:
        return False, "Tray type is required"
    value = value.upper().strip()
    if value not in ('E', 'F', 'P'):
        return False, f"Tray type must be E (easy), F (flat), or P (pillar), got: {value}"
    return True, ""


def validate_tray_number(value: int) -> Tuple[bool, str]:
    """Validate tray number."""
    if value is None:
        return False, "Tray number is required"
    if not 1 <= value <= 4:
        return False, f"Tray number must be 1-4, got: {value}"
    return True, ""


def validate_pellet_number(value: int) -> Tuple[bool, str]:
    """Validate pellet number within a tray."""
    if value is None:
        return False, "Pellet number is required"
    if not 1 <= value <= 20:
        return False, f"Pellet number must be 1-20, got: {value}"
    return True, ""


def validate_sex(value: str) -> Tuple[bool, str]:
    """Validate sex field."""
    if not value:
        return True, ""  # Optional field
    value = value.upper().strip()
    if value not in ('M', 'F'):
        return False, f"Sex must be M or F, got: {value}"
    return True, ""


def validate_surgery_type(value: str) -> Tuple[bool, str]:
    """Validate surgery type."""
    if not value:
        return False, "Surgery type is required"
    value = value.lower().strip()
    valid_types = ('contusion', 'tracing', 'perfusion')
    if value not in valid_types:
        return False, f"Surgery type must be one of {valid_types}, got: {value}"
    return True, ""


# =============================================================================
# SESSION VALIDATORS
# =============================================================================

def validate_session_date(value: date, cohort_start_date: date,
                          valid_phases: List[str]) -> Tuple[bool, str, Optional[str]]:
    """
    Validate session date against cohort timeline.

    Args:
        value: Session date to validate
        cohort_start_date: Cohort's food deprivation start date
        valid_phases: List of valid test phase names

    Returns:
        (is_valid, error_message, phase_name)
    """
    from .schema import TIMELINE

    if value is None:
        return False, "Session date is required", None

    if value < cohort_start_date:
        return False, f"Session date cannot be before cohort start date ({cohort_start_date})", None

    day_offset = (value - cohort_start_date).days

    # Find matching phase
    for offset, phase, _, _ in TIMELINE:
        if offset == day_offset:
            return True, "", phase

    return False, f"Day {day_offset} is not a valid testing day (recovery period?)", None


# =============================================================================
# BATCH VALIDATORS
# =============================================================================

def validate_pellet_grid(scores: List[List[int]], expected_trays: int = 4,
                         pellets_per_tray: int = 20) -> Tuple[bool, List[str]]:
    """
    Validate a complete pellet score grid.

    Args:
        scores: 2D list of scores [tray][pellet]
        expected_trays: Expected number of trays
        pellets_per_tray: Expected pellets per tray

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    if len(scores) != expected_trays:
        errors.append(f"Expected {expected_trays} trays, got {len(scores)}")

    for tray_idx, tray_scores in enumerate(scores):
        if len(tray_scores) != pellets_per_tray:
            errors.append(f"Tray {tray_idx + 1}: Expected {pellets_per_tray} pellets, got {len(tray_scores)}")

        for pellet_idx, score in enumerate(tray_scores):
            if score is not None:
                valid, msg = validate_pellet_score(score)
                if not valid:
                    errors.append(f"Tray {tray_idx + 1}, Pellet {pellet_idx + 1}: {msg}")

    return len(errors) == 0, errors


def validate_import_row(row: dict, required_fields: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate a row during Excel import.

    Args:
        row: Dictionary of field values
        required_fields: List of required field names

    Returns:
        (is_valid, list_of_errors)
    """
    errors = []

    for field in required_fields:
        if field not in row or row[field] is None or str(row[field]).strip() == '':
            errors.append(f"Missing required field: {field}")

    return len(errors) == 0, errors


# =============================================================================
# COMPUTE + COMPARE VALIDATION FUNCTIONS
# =============================================================================

def compute_manual_summary(session, cohort_id: str) -> list:
    """
    Compute retrieved/contacted percentages per animal per date from PelletScore records.

    This replicates what the 3c_Manual_Summary Excel sheet calculated.

    For each (subject_id, session_date):
    - retrieved_pct = (count of score==2) / (total scores) * 100
    - contacted_pct = (count of score > 0) / (total scores) * 100

    Args:
        session: Database session
        cohort_id: Cohort to compute summaries for

    Returns:
        List of dicts: [{subject_id, date, retrieved_pct, contacted_pct, total_pellets}, ...]
    """
    from .schema import PelletScore, Subject
    from sqlalchemy import func, case, Integer

    # Get all subjects in this cohort
    subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
    subject_ids = [s.subject_id for s in subjects]

    if not subject_ids:
        return []

    results = []

    # Query pellet scores grouped by subject and date
    # Use CASE WHEN instead of func.cast for SQLite compatibility
    retrieved_count = func.sum(case((PelletScore.score == 2, 1), else_=0))
    contacted_count = func.sum(case((PelletScore.score > 0, 1), else_=0))

    rows = (
        session.query(
            PelletScore.subject_id,
            PelletScore.session_date,
            func.count(PelletScore.id).label('total'),
            retrieved_count.label('retrieved'),
            contacted_count.label('contacted'),
        )
        .filter(PelletScore.subject_id.in_(subject_ids))
        .group_by(PelletScore.subject_id, PelletScore.session_date)
        .all()
    )

    for row in rows:
        total = row.total or 0
        if total == 0:
            continue
        results.append({
            'subject_id': row.subject_id,
            'date': row.session_date,
            'retrieved_pct': (row.retrieved or 0) / total * 100,
            'contacted_pct': (row.contacted or 0) / total * 100,
            'total_pellets': total,
        })

    return results


def compute_phase_stats(session, cohort_id: str) -> list:
    """
    Compute mean retrieved/contacted percentages per animal per phase group.

    This replicates what the 7_Stats Excel sheet calculated.

    Phase groups map individual phases to summary categories:
    - Training_Flat_* → "Flat Training"
    - Training_Pillar_* → "Pillar Training"
    - Pre-Injury_Test_* → "Last 3" (the pre-injury tests)
    - Post-Injury_Test_1 → "Post injury 1"
    - Post-Injury_Test_2, Post-Injury_Test_3, Post-Injury_Test_4 → "Post Injury 2-4"
    - Rehab_Easy_* → "Rehab Easy"
    - Rehab_Flat_* → "Rehab Flat"
    - Rehab_Pillar_* → "Rehab Pillar"

    Args:
        session: Database session
        cohort_id: Cohort to compute stats for

    Returns:
        List of dicts: [{subject_id, phase, retrieved_pct, contacted_pct}, ...]
    """
    from .schema import PelletScore, Subject
    from collections import defaultdict

    # Maps test_phase prefixes to summary category names
    # Handles both underscore format (CNT_01) and space format (CNT_02)
    PHASE_MAP = {
        # Underscore format (CNT_01 style)
        'Training_Flat': 'Flat Training',
        'Training_Pillar': 'Pillar Training',
        'Pre-Injury_Test': 'Last 3',
        'Post-Injury_Test_1': 'Post injury 1',
        'Post-Injury_Test_2': 'Post Injury 2-4',
        'Post-Injury_Test_3': 'Post Injury 2-4',
        'Post-Injury_Test_4': 'Post Injury 2-4',
        'Rehab_Easy': 'Rehab Easy',
        'Rehab_Flat': 'Rehab Flat',
        'Rehab_Pillar': 'Rehab Pillar',
        # Space format (CNT_02 style)
        'Flat Training': 'Flat Training',
        'Pillar Training': 'Pillar Training',
        'Pre-Injury Test': 'Last 3',
        'Post-Injury Test 1': 'Post injury 1',
        'Post-Injury Test 2': 'Post Injury 2-4',
        'Post-Injury Test 3': 'Post Injury 2-4',
        'Post-Injury Test 4': 'Post Injury 2-4',
        'Rehab Easy': 'Rehab Easy',
        'Rehab Flat': 'Rehab Flat',
        'Rehab Pillar': 'Rehab Pillar',
        # Generic rehab (CNT_01 style where rehab isn't split by tray type)
        'Rehab_': 'Rehab',
    }

    def get_phase_group(test_phase: str) -> str:
        """Map individual test phase to summary phase group."""
        if not test_phase:
            return 'Unknown'
        # Try exact prefix match (longer prefixes first for specificity)
        for prefix in sorted(PHASE_MAP.keys(), key=len, reverse=True):
            if test_phase.startswith(prefix):
                return PHASE_MAP[prefix]
        return 'Unknown'

    # First compute per-session summaries
    session_summaries = compute_manual_summary(session, cohort_id)

    # Need to get test_phase for each (subject_id, date) pair
    # Query distinct (subject_id, session_date, test_phase) from pellet_scores
    subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
    subject_ids = [s.subject_id for s in subjects]

    if not subject_ids:
        return []

    # Get phase for each session
    from .schema import PelletScore
    phase_rows = (
        session.query(
            PelletScore.subject_id,
            PelletScore.session_date,
            PelletScore.test_phase,
        )
        .filter(PelletScore.subject_id.in_(subject_ids))
        .distinct()
        .all()
    )

    # Build lookup: (subject_id, date) -> test_phase
    phase_lookup = {}
    for pr in phase_rows:
        key = (pr.subject_id, pr.session_date)
        phase_lookup[key] = pr.test_phase

    # Group session summaries by (subject_id, phase_group)
    grouped = defaultdict(lambda: {'retrieved_pcts': [], 'contacted_pcts': []})

    for summary in session_summaries:
        key = (summary['subject_id'], summary['date'])
        test_phase = phase_lookup.get(key, '')
        phase_group = get_phase_group(test_phase)

        group_key = (summary['subject_id'], phase_group)
        grouped[group_key]['retrieved_pcts'].append(summary['retrieved_pct'])
        grouped[group_key]['contacted_pcts'].append(summary['contacted_pct'])

    # Compute means
    results = []
    for (subject_id, phase_group), data in grouped.items():
        if phase_group == 'Unknown':
            continue
        results.append({
            'subject_id': subject_id,
            'phase': phase_group,
            'retrieved_pct': sum(data['retrieved_pcts']) / len(data['retrieved_pcts']) if data['retrieved_pcts'] else 0,
            'contacted_pct': sum(data['contacted_pcts']) / len(data['contacted_pcts']) if data['contacted_pcts'] else 0,
        })

    return results


def validate_against_archive(session, cohort_id: str, tolerance: float = 0.5) -> dict:
    """
    Compare computed metrics against archived Excel values.

    Args:
        session: Database session
        cohort_id: Cohort to validate
        tolerance: Maximum acceptable difference in percentage points

    Returns:
        Dict with validation report:
        {
            'cohort_id': str,
            'summary_validation': {
                'total_compared': int,
                'matches': int,
                'discrepancies': [{'subject_id', 'date', 'metric', 'excel_val', 'computed_val', 'diff'}]
            },
            'stats_validation': {
                'total_compared': int,
                'matches': int,
                'discrepancies': [{'subject_id', 'phase', 'metric', 'excel_val', 'computed_val', 'diff'}]
            }
        }
    """
    from .schema import ArchivedSummary

    report = {
        'cohort_id': cohort_id,
        'summary_validation': {'total_compared': 0, 'matches': 0, 'discrepancies': []},
        'stats_validation': {'total_compared': 0, 'matches': 0, 'discrepancies': []},
    }

    # --- Validate 3c_Manual_Summary ---
    computed_summaries = compute_manual_summary(session, cohort_id)

    # Build lookup: (subject_id, date, metric) -> computed_value
    computed_lookup = {}
    for s in computed_summaries:
        computed_lookup[(s['subject_id'], s['date'], 'retrieved_pct')] = s['retrieved_pct']
        computed_lookup[(s['subject_id'], s['date'], 'contacted_pct')] = s['contacted_pct']

    # Get archived values for 3c sheets
    archived_3c = (
        session.query(ArchivedSummary)
        .filter(
            ArchivedSummary.cohort_id == cohort_id,
            ArchivedSummary.sheet_name.in_(['3c_Manual_Summary', '3d_Manual_Summary - survivors']),
        )
        .all()
    )

    for arch in archived_3c:
        if arch.metric_value is None or arch.date is None or arch.subject_id is None:
            continue

        key = (arch.subject_id, arch.date, arch.metric_name)
        computed_val = computed_lookup.get(key)

        if computed_val is None:
            # No computed value - data might not be imported yet
            continue

        report['summary_validation']['total_compared'] += 1
        diff = abs(arch.metric_value - computed_val)

        if diff <= tolerance:
            report['summary_validation']['matches'] += 1
        else:
            report['summary_validation']['discrepancies'].append({
                'subject_id': arch.subject_id,
                'date': str(arch.date),
                'metric': arch.metric_name,
                'excel_val': round(arch.metric_value, 2),
                'computed_val': round(computed_val, 2),
                'diff': round(diff, 2),
            })

    # --- Validate 7_Stats ---
    computed_stats = compute_phase_stats(session, cohort_id)

    # Build lookup: (subject_id, phase, metric) -> computed_value
    stats_lookup = {}
    for s in computed_stats:
        stats_lookup[(s['subject_id'], s['phase'], 'retrieved_pct')] = s['retrieved_pct']
        stats_lookup[(s['subject_id'], s['phase'], 'contacted_pct')] = s['contacted_pct']

    archived_stats = (
        session.query(ArchivedSummary)
        .filter(
            ArchivedSummary.cohort_id == cohort_id,
            ArchivedSummary.sheet_name == '7_Stats',
        )
        .all()
    )

    for arch in archived_stats:
        if arch.metric_value is None or arch.phase is None or arch.subject_id is None:
            continue

        key = (arch.subject_id, arch.phase, arch.metric_name)
        computed_val = stats_lookup.get(key)

        if computed_val is None:
            continue

        report['stats_validation']['total_compared'] += 1
        diff = abs(arch.metric_value - computed_val)

        if diff <= tolerance:
            report['stats_validation']['matches'] += 1
        else:
            report['stats_validation']['discrepancies'].append({
                'subject_id': arch.subject_id,
                'phase': arch.phase,
                'metric': arch.metric_name,
                'excel_val': round(arch.metric_value, 2),
                'computed_val': round(computed_val, 2),
                'diff': round(diff, 2),
            })

    return report


def print_validation_report(report: dict):
    """Print a human-readable validation report."""
    print(f"\nValidation Report for {report['cohort_id']}")
    print("=" * 60)

    # Summary validation
    sv = report['summary_validation']
    print(f"\n3c_Manual_Summary:")
    print(f"  Values compared: {sv['total_compared']}")
    print(f"  Matches: {sv['matches']}")
    if sv['discrepancies']:
        print(f"  Discrepancies ({len(sv['discrepancies'])}):")
        for d in sv['discrepancies'][:10]:  # Show first 10
            print(f"    {d['subject_id']} / {d['date']} / {d['metric']}: "
                  f"Excel={d['excel_val']}%, Computed={d['computed_val']}% (diff={d['diff']})")
        if len(sv['discrepancies']) > 10:
            print(f"    ... and {len(sv['discrepancies']) - 10} more")
    else:
        print("  All values match!")

    # Stats validation
    stv = report['stats_validation']
    print(f"\n7_Stats:")
    print(f"  Values compared: {stv['total_compared']}")
    print(f"  Matches: {stv['matches']}")
    if stv['discrepancies']:
        print(f"  Discrepancies ({len(stv['discrepancies'])}):")
        for d in stv['discrepancies'][:10]:
            print(f"    {d['subject_id']} / {d['phase']} / {d['metric']}: "
                  f"Excel={d['excel_val']}%, Computed={d['computed_val']}% (diff={d['diff']})")
        if len(stv['discrepancies']) > 10:
            print(f"    ... and {len(stv['discrepancies']) - 10} more")
    else:
        print("  All values match!")
