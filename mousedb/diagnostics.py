"""
Data completeness diagnostics for Connectome Data Entry.

Checks all cohorts (or a specific cohort) for missing data and flags issues
using severity levels: CRITICAL, WARNING, INFO.

Uses sibling-based heuristics: computes the median count of data across
all subjects in a cohort, then flags outliers (0 when expected = CRITICAL,
<50% of median = WARNING).

Usage:
    from mousedb.diagnostics import check_all_cohorts, print_completeness_report

    with db.session() as session:
        report = check_all_cohorts(session)
        print_completeness_report(report)

CLI:
    mousedb check                    # All cohorts, hide INFO
    mousedb check --verbose          # Show everything
    mousedb check --cohort CNT_04    # Single cohort
    mousedb check --json             # JSON output
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Set
from datetime import datetime
from statistics import median


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class Severity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class DiagnosticFinding:
    severity: Severity
    cohort_id: str
    subject_id: Optional[str]  # None for cohort-level findings
    category: str  # 'weights', 'pellets', 'surgeries', 'ramp', 'metadata', 'template'
    message: str  # Human-readable message
    detail: Optional[str] = None  # Hint for how to fix
    actual_count: Optional[int] = None
    expected_count: Optional[int] = None


@dataclass
class CohortReport:
    cohort_id: str
    num_subjects: int
    findings: List[DiagnosticFinding] = field(default_factory=list)
    summary: Dict[str, int] = field(default_factory=lambda: {
        'CRITICAL': 0, 'WARNING': 0, 'INFO': 0
    })
    data_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class CompletenessReport:
    cohorts: List[CohortReport] = field(default_factory=list)
    generated_at: datetime = field(default_factory=datetime.now)
    db_path: str = ""


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _safe_median(values: List[int]) -> float:
    """Compute median of a list, returning 0 for empty lists."""
    if not values:
        return 0
    return median(values)


def _add_finding(report: CohortReport, finding: DiagnosticFinding):
    """Add a finding to a cohort report and update the summary counts."""
    report.findings.append(finding)
    report.summary[finding.severity.value] += 1


# =============================================================================
# CORE CHECK FUNCTIONS
# =============================================================================

def check_cohort_completeness(
    session,
    cohort_id: str,
    pilot_cohorts: Optional[Set[str]] = None,
) -> CohortReport:
    """
    Check a single cohort for data completeness issues.

    Args:
        session: Active SQLAlchemy database session
        cohort_id: Cohort to check
        pilot_cohorts: Set of cohort IDs that are pilot/exploratory
                       (get INFO-level severity only). Defaults to {'CNT_00'}.

    Returns:
        CohortReport with findings
    """
    from .schema import (
        Cohort, Subject, Weight, PelletScore, Surgery, RampEntry, LadderEntry
    )
    from sqlalchemy import func

    if pilot_cohorts is None:
        pilot_cohorts = {'CNT_00'}

    is_pilot = cohort_id in pilot_cohorts

    # Get cohort and subjects
    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if cohort is None:
        report = CohortReport(cohort_id=cohort_id, num_subjects=0)
        _add_finding(report, DiagnosticFinding(
            severity=Severity.CRITICAL,
            cohort_id=cohort_id,
            subject_id=None,
            category='cohort',
            message=f"Cohort {cohort_id} not found in database",
        ))
        return report

    subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
    subject_ids = [s.subject_id for s in subjects]

    report = CohortReport(
        cohort_id=cohort_id,
        num_subjects=len(subjects),
    )

    if not subjects:
        _add_finding(report, DiagnosticFinding(
            severity=Severity.CRITICAL,
            cohort_id=cohort_id,
            subject_id=None,
            category='cohort',
            message=f"Cohort {cohort_id} has 0 subjects",
            detail="Create subjects via GUI or CLI (unified-data new-cohort)",
        ))
        return report

    # =========================================================================
    # Gather per-subject counts
    # =========================================================================

    # Weights per subject
    weight_counts = dict(
        session.query(Weight.subject_id, func.count(Weight.id))
        .filter(Weight.subject_id.in_(subject_ids))
        .group_by(Weight.subject_id)
        .all()
    )

    # Pellet scores per subject
    pellet_counts = dict(
        session.query(PelletScore.subject_id, func.count(PelletScore.id))
        .filter(PelletScore.subject_id.in_(subject_ids))
        .group_by(PelletScore.subject_id)
        .all()
    )

    # Surgeries per subject
    surgery_counts = dict(
        session.query(Surgery.subject_id, func.count(Surgery.id))
        .filter(Surgery.subject_id.in_(subject_ids))
        .group_by(Surgery.subject_id)
        .all()
    )

    # Ramp entries per subject
    ramp_counts = dict(
        session.query(RampEntry.subject_id, func.count(RampEntry.id))
        .filter(RampEntry.subject_id.in_(subject_ids))
        .group_by(RampEntry.subject_id)
        .all()
    )

    # Ladder entries per subject
    ladder_counts = dict(
        session.query(LadderEntry.subject_id, func.count(LadderEntry.id))
        .filter(LadderEntry.subject_id.in_(subject_ids))
        .group_by(LadderEntry.subject_id)
        .all()
    )

    # Build per-subject count lists
    per_subj_weights = [weight_counts.get(sid, 0) for sid in subject_ids]
    per_subj_pellets = [pellet_counts.get(sid, 0) for sid in subject_ids]
    per_subj_surgeries = [surgery_counts.get(sid, 0) for sid in subject_ids]
    per_subj_ramp = [ramp_counts.get(sid, 0) for sid in subject_ids]
    per_subj_ladder = [ladder_counts.get(sid, 0) for sid in subject_ids]

    # Store aggregate counts
    report.data_counts = {
        'weights': sum(per_subj_weights),
        'pellets': sum(per_subj_pellets),
        'surgeries': sum(per_subj_surgeries),
        'ramp': sum(per_subj_ramp),
        'ladder': sum(per_subj_ladder),
    }

    # Compute medians for sibling-based heuristic
    med_weights = _safe_median(per_subj_weights)
    med_pellets = _safe_median(per_subj_pellets)
    med_surgeries = _safe_median(per_subj_surgeries)
    med_ramp = _safe_median(per_subj_ramp)

    # =========================================================================
    # Check 1: Empty template detection
    # =========================================================================
    # If ALL subjects have 0 behavioral data, this is an empty template
    total_behavioral = (
        report.data_counts['weights']
        + report.data_counts['pellets']
        + report.data_counts['ramp']
    )

    if total_behavioral == 0:
        # Check if there are at least surgery records with real data
        has_real_surgeries = False
        if report.data_counts['surgeries'] > 0:
            # Check if surgeries have non-zero contusion params
            contusion_with_data = (
                session.query(Surgery)
                .filter(
                    Surgery.subject_id.in_(subject_ids),
                    Surgery.surgery_type == 'contusion',
                )
                .filter(
                    (Surgery.force_kdyn != None) & (Surgery.force_kdyn > 0)  # noqa: E711
                    | (Surgery.displacement_um != None) & (Surgery.displacement_um > 0)  # noqa: E711
                )
                .count()
            )
            has_real_surgeries = contusion_with_data > 0

        if is_pilot:
            _add_finding(report, DiagnosticFinding(
                severity=Severity.INFO,
                cohort_id=cohort_id,
                subject_id=None,
                category='template',
                message=f"Pilot cohort - no behavioral data (weights/pellets/ramp)",
                detail="Pilot cohorts are expected to have sparse data",
            ))
        else:
            _add_finding(report, DiagnosticFinding(
                severity=Severity.CRITICAL,
                cohort_id=cohort_id,
                subject_id=None,
                category='template',
                message=f"Empty template - no behavioral data imported (0 weights, 0 pellets, 0 ramp)",
                detail="Populate Excel sheets with data before re-importing",
            ))

        # Still check surgery params if surgeries exist
        if report.data_counts['surgeries'] > 0 and not has_real_surgeries:
            zero_param_count = (
                session.query(Surgery)
                .filter(
                    Surgery.subject_id.in_(subject_ids),
                    Surgery.surgery_type == 'contusion',
                )
                .filter(
                    ((Surgery.force_kdyn == None) | (Surgery.force_kdyn == 0))  # noqa: E711
                    & ((Surgery.displacement_um == None) | (Surgery.displacement_um == 0))  # noqa: E711
                )
                .count()
            )
            if zero_param_count > 0:
                sev = Severity.INFO if is_pilot else Severity.WARNING
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=None,
                    category='surgeries',
                    message=f"{zero_param_count} contusion records have 0 force and 0 displacement",
                    detail="Enter actual contusion parameters in 4_Contusion_Injury_Details",
                ))

        # For empty templates, skip per-subject checks (would just duplicate)
        _check_metadata(session, subjects, report, is_pilot)
        return report

    # =========================================================================
    # Check 2: Per-subject weight completeness
    # =========================================================================
    if med_weights > 0:
        for sid in subject_ids:
            count = weight_counts.get(sid, 0)
            if count == 0:
                sev = Severity.INFO if is_pilot else Severity.CRITICAL
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='weights',
                    message=f"{sid} has 0 weights (cohort median: {med_weights:.0f})",
                    detail="Check Excel sheet 3d_Weights for missing data",
                    actual_count=0,
                    expected_count=int(med_weights),
                ))
            elif count < med_weights * 0.5 and med_weights >= 4:
                sev = Severity.INFO if is_pilot else Severity.WARNING
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='weights',
                    message=f"{sid} has {count} weights (cohort median: {med_weights:.0f})",
                    detail="Check Excel sheet 3d_Weights for missing data",
                    actual_count=count,
                    expected_count=int(med_weights),
                ))

    # =========================================================================
    # Check 3: Per-subject pellet score completeness
    # =========================================================================
    if med_pellets > 0:
        for sid in subject_ids:
            count = pellet_counts.get(sid, 0)
            if count == 0:
                sev = Severity.INFO if is_pilot else Severity.CRITICAL
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='pellets',
                    message=f"{sid} has 0 pellet scores (cohort median: {med_pellets:.0f})",
                    detail="Enter pellet scores in Excel sheet 3b_Manual_Tray",
                    actual_count=0,
                    expected_count=int(med_pellets),
                ))
            elif count < med_pellets * 0.5 and med_pellets >= 80:
                sev = Severity.INFO if is_pilot else Severity.WARNING
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='pellets',
                    message=f"{sid} has {count} pellet scores (cohort median: {med_pellets:.0f})",
                    detail="Check Excel sheet 3b_Manual_Tray for missing data",
                    actual_count=count,
                    expected_count=int(med_pellets),
                ))

    # =========================================================================
    # Check 4: Per-subject ramp completeness
    # =========================================================================
    if med_ramp > 0:
        for sid in subject_ids:
            count = ramp_counts.get(sid, 0)
            if count == 0:
                sev = Severity.INFO if is_pilot else Severity.WARNING
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='ramp',
                    message=f"{sid} has 0 ramp entries (cohort median: {med_ramp:.0f})",
                    detail="Check Excel sheet 3a_Manual_Ramp for missing data",
                    actual_count=0,
                    expected_count=int(med_ramp),
                ))

    # =========================================================================
    # Check 5: Surgery records
    # =========================================================================
    if med_surgeries > 0:
        for sid in subject_ids:
            count = surgery_counts.get(sid, 0)
            if count == 0:
                sev = Severity.INFO if is_pilot else Severity.CRITICAL
                _add_finding(report, DiagnosticFinding(
                    severity=sev,
                    cohort_id=cohort_id,
                    subject_id=sid,
                    category='surgeries',
                    message=f"{sid} has 0 surgery records (cohort median: {med_surgeries:.0f})",
                    detail="Check Excel sheets 4_Contusion / 5_SC_Injection for missing data",
                    actual_count=0,
                    expected_count=int(med_surgeries),
                ))

    # =========================================================================
    # Check 6: Empty contusion parameters (force=0 AND displacement=0)
    # =========================================================================
    if report.data_counts['surgeries'] > 0:
        zero_param_count = (
            session.query(Surgery)
            .filter(
                Surgery.subject_id.in_(subject_ids),
                Surgery.surgery_type == 'contusion',
            )
            .filter(
                ((Surgery.force_kdyn == None) | (Surgery.force_kdyn == 0))  # noqa: E711
                & ((Surgery.displacement_um == None) | (Surgery.displacement_um == 0))  # noqa: E711
            )
            .count()
        )
        if zero_param_count > 0:
            sev = Severity.INFO if is_pilot else Severity.WARNING
            _add_finding(report, DiagnosticFinding(
                severity=sev,
                cohort_id=cohort_id,
                subject_id=None,
                category='surgeries',
                message=f"{zero_param_count} contusion records have 0 force and 0 displacement",
                detail="Enter actual contusion parameters in 4_Contusion_Injury_Details",
            ))

    # =========================================================================
    # Check 7: Metadata completeness
    # =========================================================================
    _check_metadata(session, subjects, report, is_pilot)

    return report


def _check_metadata(session, subjects, report: CohortReport, is_pilot: bool):
    """Check metadata fields (DOB, sex) for all subjects."""
    missing_dob = sum(1 for s in subjects if s.date_of_birth is None)
    missing_sex = sum(1 for s in subjects if s.sex is None)

    if missing_dob > 0:
        _add_finding(report, DiagnosticFinding(
            severity=Severity.INFO,
            cohort_id=report.cohort_id,
            subject_id=None,
            category='metadata',
            message=f"{missing_dob}/{len(subjects)} subjects missing date of birth",
            detail="Enter DOB in 0a_Metadata sheet or via GUI",
        ))

    if missing_sex > 0:
        _add_finding(report, DiagnosticFinding(
            severity=Severity.INFO,
            cohort_id=report.cohort_id,
            subject_id=None,
            category='metadata',
            message=f"{missing_sex}/{len(subjects)} subjects missing sex",
            detail="Enter sex in 0a_Metadata sheet or via GUI",
        ))


# =============================================================================
# AGGREGATE CHECK
# =============================================================================

def check_all_cohorts(
    session,
    pilot_cohorts: Optional[Set[str]] = None,
) -> CompletenessReport:
    """
    Run completeness checks on all cohorts in the database.

    Args:
        session: Active SQLAlchemy database session
        pilot_cohorts: Set of cohort IDs to treat as pilot/exploratory

    Returns:
        CompletenessReport with per-cohort findings
    """
    from .schema import Cohort
    from . import DEFAULT_DB_PATH

    cohorts = session.query(Cohort).order_by(Cohort.cohort_id).all()

    report = CompletenessReport(
        db_path=str(DEFAULT_DB_PATH),
    )

    for cohort in cohorts:
        cohort_report = check_cohort_completeness(
            session, cohort.cohort_id, pilot_cohorts
        )
        report.cohorts.append(cohort_report)

    return report


# =============================================================================
# OUTPUT FORMATTERS
# =============================================================================

def print_completeness_report(report: CompletenessReport, verbose: bool = False):
    """
    Print a human-readable completeness report.

    Args:
        report: CompletenessReport from check_all_cohorts
        verbose: If True, also show INFO-level findings
    """
    print(f"\n{'=' * 60}")
    print(f"  Data Completeness Report")
    print(f"{'=' * 60}")
    print(f"Database: {report.db_path}")
    print(f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S')}")

    total_critical = 0
    total_warning = 0
    total_info = 0
    cohorts_with_issues = set()

    for cr in report.cohorts:
        print_cohort_report(cr, verbose=verbose)
        total_critical += cr.summary['CRITICAL']
        total_warning += cr.summary['WARNING']
        total_info += cr.summary['INFO']
        if cr.summary['CRITICAL'] > 0 or cr.summary['WARNING'] > 0:
            cohorts_with_issues.add(cr.cohort_id)

    # Overall summary
    print(f"\n{'=' * 60}")
    print(f"  Summary")
    print(f"{'=' * 60}")
    print(f"  CRITICAL: {total_critical} across {sum(1 for cr in report.cohorts if cr.summary['CRITICAL'] > 0)} cohorts")
    print(f"  WARNING:  {total_warning} across {sum(1 for cr in report.cohorts if cr.summary['WARNING'] > 0)} cohorts")
    print(f"  INFO:     {total_info}")

    if total_critical == 0 and total_warning == 0:
        print(f"\n  All cohorts have complete data!")
    else:
        print(f"\n  Cohorts needing attention: {', '.join(sorted(cohorts_with_issues))}")


def print_cohort_report(report: CohortReport, verbose: bool = False):
    """
    Print a single cohort's completeness report.

    Args:
        report: CohortReport from check_cohort_completeness
        verbose: If True, show INFO-level findings too
    """
    # Header line with counts
    counts_parts = []
    for key in ['weights', 'pellets', 'surgeries', 'ramp', 'ladder']:
        if key in report.data_counts:
            counts_parts.append(f"{key.capitalize()}: {report.data_counts[key]:,}")

    # Determine if pilot
    is_pilot = any(
        f.category == 'template' and 'Pilot' in f.message
        for f in report.findings
    )

    pilot_tag = " (Pilot)" if is_pilot else ""
    template_tag = ""
    if any(f.category == 'template' and 'Empty template' in f.message for f in report.findings):
        template_tag = " (Empty Template)"

    print(f"\n--- {report.cohort_id}{pilot_tag}{template_tag} ---")
    print(f"  Subjects: {report.num_subjects}", end="")
    if counts_parts:
        print(f" | {' | '.join(counts_parts)}")
    else:
        print()

    # Print findings
    printed_any = False
    for finding in report.findings:
        if finding.severity == Severity.INFO and not verbose:
            continue

        severity_str = finding.severity.value
        indent = "  "
        print(f"{indent}[{severity_str}] {finding.message}")
        if finding.detail:
            print(f"{indent}         -> {finding.detail}")
        printed_any = True

    if not printed_any:
        if report.summary['INFO'] > 0 and not verbose:
            print(f"  No issues (hiding {report.summary['INFO']} INFO notes, use --verbose)")
        else:
            print(f"  No issues found")


def format_report_as_dict(report: CompletenessReport) -> dict:
    """
    Convert a CompletenessReport to a JSON-serializable dict.

    Args:
        report: CompletenessReport to convert

    Returns:
        Dict suitable for json.dumps()
    """
    return {
        'generated_at': report.generated_at.isoformat(),
        'db_path': report.db_path,
        'cohorts': [
            {
                'cohort_id': cr.cohort_id,
                'num_subjects': cr.num_subjects,
                'data_counts': cr.data_counts,
                'summary': cr.summary,
                'findings': [
                    {
                        'severity': f.severity.value,
                        'cohort_id': f.cohort_id,
                        'subject_id': f.subject_id,
                        'category': f.category,
                        'message': f.message,
                        'detail': f.detail,
                        'actual_count': f.actual_count,
                        'expected_count': f.expected_count,
                    }
                    for f in cr.findings
                ],
            }
            for cr in report.cohorts
        ],
        'totals': {
            'CRITICAL': sum(cr.summary['CRITICAL'] for cr in report.cohorts),
            'WARNING': sum(cr.summary['WARNING'] for cr in report.cohorts),
            'INFO': sum(cr.summary['INFO'] for cr in report.cohorts),
        },
    }
