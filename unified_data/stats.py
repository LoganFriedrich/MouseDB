"""
Summary statistics calculations for Connectome Data Entry.

Calculates the same stats as the legacy Excel tracking sheets:
- Per-tray counts and percentages
- Daily totals and averages
- Days post-injury calculations
- Weight percentage tracking
"""

from datetime import date, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from .schema import Subject, Cohort, Weight, PelletScore, Surgery


@dataclass
class TrayStats:
    """Statistics for a single tray (20 pellets)."""
    tray_type: str
    tray_number: int
    presented: int = 20  # Always 20 pellets per tray
    miss: int = 0
    displaced: int = 0
    retrieved: int = 0

    @property
    def contacted(self) -> int:
        """Displaced + Retrieved = Contacted"""
        return self.displaced + self.retrieved

    @property
    def entered(self) -> int:
        """Number of pellets actually scored"""
        return self.miss + self.displaced + self.retrieved

    @property
    def miss_pct(self) -> float:
        return (self.miss / self.presented * 100) if self.presented > 0 else 0.0

    @property
    def displaced_pct(self) -> float:
        return (self.displaced / self.presented * 100) if self.presented > 0 else 0.0

    @property
    def retrieved_pct(self) -> float:
        return (self.retrieved / self.presented * 100) if self.presented > 0 else 0.0

    @property
    def contacted_pct(self) -> float:
        return (self.contacted / self.presented * 100) if self.presented > 0 else 0.0


@dataclass
class DailyStats:
    """Statistics for a single day/session (typically 4 trays = 80 pellets)."""
    subject_id: str
    session_date: date
    test_phase: str
    trays: List[TrayStats] = field(default_factory=list)
    weight_grams: Optional[float] = None
    baseline_weight: Optional[float] = None
    injury_date: Optional[date] = None

    @property
    def total_presented(self) -> int:
        return sum(t.presented for t in self.trays)

    @property
    def total_miss(self) -> int:
        return sum(t.miss for t in self.trays)

    @property
    def total_displaced(self) -> int:
        return sum(t.displaced for t in self.trays)

    @property
    def total_retrieved(self) -> int:
        return sum(t.retrieved for t in self.trays)

    @property
    def total_contacted(self) -> int:
        return sum(t.contacted for t in self.trays)

    @property
    def total_entered(self) -> int:
        return sum(t.entered for t in self.trays)

    @property
    def miss_pct(self) -> float:
        return (self.total_miss / self.total_presented * 100) if self.total_presented > 0 else 0.0

    @property
    def displaced_pct(self) -> float:
        return (self.total_displaced / self.total_presented * 100) if self.total_presented > 0 else 0.0

    @property
    def retrieved_pct(self) -> float:
        return (self.total_retrieved / self.total_presented * 100) if self.total_presented > 0 else 0.0

    @property
    def contacted_pct(self) -> float:
        return (self.total_contacted / self.total_presented * 100) if self.total_presented > 0 else 0.0

    @property
    def avg_miss_pct(self) -> float:
        """Average miss percentage across trays"""
        if not self.trays:
            return 0.0
        return sum(t.miss_pct for t in self.trays) / len(self.trays)

    @property
    def avg_displaced_pct(self) -> float:
        if not self.trays:
            return 0.0
        return sum(t.displaced_pct for t in self.trays) / len(self.trays)

    @property
    def avg_retrieved_pct(self) -> float:
        if not self.trays:
            return 0.0
        return sum(t.retrieved_pct for t in self.trays) / len(self.trays)

    @property
    def avg_contacted_pct(self) -> float:
        if not self.trays:
            return 0.0
        return sum(t.contacted_pct for t in self.trays) / len(self.trays)

    @property
    def weight_pct(self) -> Optional[float]:
        """Weight as percentage of baseline"""
        if self.weight_grams and self.baseline_weight and self.baseline_weight > 0:
            return (self.weight_grams / self.baseline_weight) * 100
        return None

    @property
    def days_post_injury(self) -> Optional[int]:
        """Days since injury (negative = pre-injury)"""
        if self.injury_date:
            return (self.session_date - self.injury_date).days
        return None


@dataclass
class SubjectSummary:
    """Summary statistics for a single subject across all sessions."""
    subject_id: str
    cohort_id: str
    sex: str
    date_of_birth: Optional[date]
    injury_date: Optional[date]
    injury_force_kdyn: Optional[float]
    injury_displacement_um: Optional[float]
    sessions: List[DailyStats] = field(default_factory=list)

    @property
    def total_sessions(self) -> int:
        return len(self.sessions)

    @property
    def total_pellets_scored(self) -> int:
        return sum(s.total_entered for s in self.sessions)

    @property
    def overall_retrieved_pct(self) -> float:
        total = sum(s.total_presented for s in self.sessions)
        retrieved = sum(s.total_retrieved for s in self.sessions)
        return (retrieved / total * 100) if total > 0 else 0.0

    @property
    def overall_contacted_pct(self) -> float:
        total = sum(s.total_presented for s in self.sessions)
        contacted = sum(s.total_contacted for s in self.sessions)
        return (contacted / total * 100) if total > 0 else 0.0

    def get_pre_injury_sessions(self) -> List[DailyStats]:
        """Get all sessions before injury"""
        if not self.injury_date:
            return self.sessions
        return [s for s in self.sessions if s.session_date < self.injury_date]

    def get_post_injury_sessions(self) -> List[DailyStats]:
        """Get all sessions after injury"""
        if not self.injury_date:
            return []
        return [s for s in self.sessions if s.session_date >= self.injury_date]


def calculate_tray_stats(session: Session, subject_id: str,
                         session_date: date, tray_type: str,
                         tray_number: int) -> TrayStats:
    """Calculate stats for a single tray."""
    pellets = session.query(PelletScore).filter_by(
        subject_id=subject_id,
        session_date=session_date,
        tray_type=tray_type,
        tray_number=tray_number
    ).all()

    stats = TrayStats(tray_type=tray_type, tray_number=tray_number)
    for p in pellets:
        if p.score == 0:
            stats.miss += 1
        elif p.score == 1:
            stats.displaced += 1
        elif p.score == 2:
            stats.retrieved += 1

    return stats


def calculate_daily_stats(db_session: Session, subject_id: str,
                          session_date: date) -> DailyStats:
    """Calculate all stats for a single day/session."""
    # Get test phase and tray info
    pellets = db_session.query(PelletScore).filter_by(
        subject_id=subject_id,
        session_date=session_date
    ).all()

    if not pellets:
        return DailyStats(subject_id=subject_id, session_date=session_date, test_phase="")

    test_phase = pellets[0].test_phase

    # Get unique tray combinations
    tray_keys = set((p.tray_type, p.tray_number) for p in pellets)

    # Calculate per-tray stats
    trays = []
    for tray_type, tray_number in sorted(tray_keys):
        tray_stats = calculate_tray_stats(
            db_session, subject_id, session_date, tray_type, tray_number
        )
        trays.append(tray_stats)

    # Get weight for this day
    weight = db_session.query(Weight).filter_by(
        subject_id=subject_id,
        date=session_date
    ).first()

    # Get baseline weight (first recorded weight)
    baseline = db_session.query(Weight).filter_by(
        subject_id=subject_id
    ).order_by(Weight.date).first()

    # Get injury date
    contusion = db_session.query(Surgery).filter_by(
        subject_id=subject_id,
        surgery_type='contusion'
    ).first()

    return DailyStats(
        subject_id=subject_id,
        session_date=session_date,
        test_phase=test_phase,
        trays=trays,
        weight_grams=weight.weight_grams if weight else None,
        baseline_weight=baseline.weight_grams if baseline else None,
        injury_date=contusion.surgery_date if contusion else None
    )


def calculate_subject_summary(db_session: Session, subject_id: str) -> SubjectSummary:
    """Calculate complete summary for a subject."""
    # Get subject info
    subject = db_session.query(Subject).filter_by(subject_id=subject_id).first()
    if not subject:
        raise ValueError(f"Subject not found: {subject_id}")

    # Get contusion info
    contusion = db_session.query(Surgery).filter_by(
        subject_id=subject_id,
        surgery_type='contusion'
    ).first()

    # Get all unique session dates
    session_dates = db_session.query(PelletScore.session_date).filter_by(
        subject_id=subject_id
    ).distinct().order_by(PelletScore.session_date).all()

    # Calculate daily stats for each session
    sessions = []
    for (sess_date,) in session_dates:
        daily = calculate_daily_stats(db_session, subject_id, sess_date)
        sessions.append(daily)

    return SubjectSummary(
        subject_id=subject_id,
        cohort_id=subject.cohort_id,
        sex=subject.sex or '',
        date_of_birth=subject.date_of_birth,
        injury_date=contusion.surgery_date if contusion else None,
        injury_force_kdyn=contusion.force_kdyn if contusion else None,
        injury_displacement_um=contusion.displacement_um if contusion else None,
        sessions=sessions
    )


def calculate_cohort_summary(db_session: Session, cohort_id: str) -> Dict[str, SubjectSummary]:
    """Calculate summaries for all subjects in a cohort."""
    subjects = db_session.query(Subject).filter_by(cohort_id=cohort_id).all()

    summaries = {}
    for subject in subjects:
        summaries[subject.subject_id] = calculate_subject_summary(
            db_session, subject.subject_id
        )

    return summaries


def get_cohort_overview(db_session: Session, cohort_id: str) -> Dict[str, Any]:
    """Get high-level overview stats for a cohort."""
    cohort = db_session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if not cohort:
        return {}

    subjects = db_session.query(Subject).filter_by(cohort_id=cohort_id).all()
    subject_ids = [s.subject_id for s in subjects]

    # Count sessions per subject
    session_counts = db_session.query(
        PelletScore.subject_id,
        func.count(func.distinct(PelletScore.session_date))
    ).filter(
        PelletScore.subject_id.in_(subject_ids)
    ).group_by(PelletScore.subject_id).all()

    # Total pellet scores
    total_pellets = db_session.query(func.count(PelletScore.id)).filter(
        PelletScore.subject_id.in_(subject_ids)
    ).scalar() or 0

    # Score breakdown
    score_counts = db_session.query(
        PelletScore.score,
        func.count(PelletScore.id)
    ).filter(
        PelletScore.subject_id.in_(subject_ids)
    ).group_by(PelletScore.score).all()

    scores = {0: 0, 1: 0, 2: 0}
    for score, count in score_counts:
        scores[score] = count

    return {
        'cohort_id': cohort_id,
        'project_code': cohort.project_code,
        'start_date': cohort.start_date,
        'num_subjects': len(subjects),
        'sessions_per_subject': dict(session_counts),
        'total_pellets_scored': total_pellets,
        'total_miss': scores[0],
        'total_displaced': scores[1],
        'total_retrieved': scores[2],
        'overall_miss_pct': (scores[0] / total_pellets * 100) if total_pellets > 0 else 0,
        'overall_displaced_pct': (scores[1] / total_pellets * 100) if total_pellets > 0 else 0,
        'overall_retrieved_pct': (scores[2] / total_pellets * 100) if total_pellets > 0 else 0,
        'overall_contacted_pct': ((scores[1] + scores[2]) / total_pellets * 100) if total_pellets > 0 else 0,
    }
