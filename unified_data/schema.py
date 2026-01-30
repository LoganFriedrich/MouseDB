"""
SQLAlchemy schema models with validation for Connectome Data Entry.

All validation happens at both the Python level (via validators) and
the database level (via CHECK constraints) for bulletproof data integrity.
"""

import re
from datetime import datetime, date
from typing import Optional, List
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Date, DateTime,
    Text, ForeignKey, CheckConstraint, UniqueConstraint, event
)
from sqlalchemy.orm import declarative_base, relationship, validates, Session
from sqlalchemy.engine import Engine

Base = declarative_base()

# =============================================================================
# VALIDATION HELPERS
# =============================================================================

SUBJECT_ID_PATTERN = re.compile(r'^[A-Z]+_\d{2}_\d{2}$')  # CNT_05_01
COHORT_ID_PATTERN = re.compile(r'^[A-Z]+_\d{2}$')  # CNT_05
PROJECT_CODE_PATTERN = re.compile(r'^[A-Z]+$')  # CNT, ENCR

def validate_subject_id(subject_id: str) -> bool:
    """Validate subject ID format: CNT_XX_YY"""
    return bool(SUBJECT_ID_PATTERN.match(subject_id))

def validate_cohort_id(cohort_id: str) -> bool:
    """Validate cohort ID format: CNT_XX"""
    return bool(COHORT_ID_PATTERN.match(cohort_id))

def parse_subject_id(subject_id: str) -> tuple:
    """Parse subject ID into (project, cohort_num, subject_num)."""
    if not validate_subject_id(subject_id):
        raise ValueError(f"Invalid subject ID format: {subject_id}")
    parts = subject_id.split('_')
    return parts[0], int(parts[1]), int(parts[2])

def derive_cohort_id(subject_id: str) -> str:
    """Derive cohort ID from subject ID: CNT_05_01 -> CNT_05"""
    project, cohort_num, _ = parse_subject_id(subject_id)
    return f"{project}_{cohort_num:02d}"

# =============================================================================
# TIMELINE CONFIGURATION
# =============================================================================

# Based on EXPERIMENTAL_TIMELINE.md from 0_Make_or_Fix_Sheets.py
TIMELINE = [
    # (day_offset, phase_name, tray_type, trays_per_day)
    # Days 0-3: Ramp (no testing)
    (4, "Training_Flat_1", "F", 4),
    (5, "Training_Flat_2", "F", 4),
    (6, "Training_Flat_3", "F", 4),
    (7, "Training_Pillar_1", "P", 4),
    (8, "Training_Pillar_2", "P", 4),
    (9, "Training_Pillar_3", "P", 4),
    (10, "Training_Pillar_4", "P", 4),
    (11, "Training_Pillar_5", "P", 4),
    (12, "Training_Pillar_6", "P", 4),
    (13, "Training_Pillar_7", "P", 4),
    (14, "Pre-Injury_Test_Pillar_1", "P", 4),
    (15, "Pre-Injury_Test_Pillar_2", "P", 4),
    (16, "Pre-Injury_Test_Pillar_3", "P", 4),
    # Day 17: Injury (no testing)
    # Days 18-24: Recovery (no testing)
    (25, "Post-Injury_Test_1", "P", 2),  # DPI 9
    (32, "Post-Injury_Test_2", "P", 2),  # DPI 16
    (39, "Post-Injury_Test_3", "P", 2),  # DPI 23
    (46, "Post-Injury_Test_4", "P", 2),  # DPI 30
    (47, "Rehab_Easy_1", "E", 4),
    (48, "Rehab_Easy_2", "E", 4),
    (49, "Rehab_Easy_3", "E", 4),
    (50, "Rehab_Easy_4", "E", 4),
    (51, "Rehab_Easy_5", "E", 4),
    (52, "Rehab_Easy_6", "E", 4),
    (53, "Rehab_Easy_7", "E", 4),
    (54, "Rehab_Easy_8", "E", 4),
    (55, "Rehab_Easy_9", "E", 4),
    (56, "Rehab_Flat_1", "F", 4),
    (57, "Rehab_Flat_2", "F", 4),
    (58, "Rehab_Flat_3", "F", 4),
    (59, "Rehab_Flat_4", "F", 4),
    (60, "Rehab_Flat_5", "F", 4),
    (61, "Rehab_Flat_6", "F", 4),
    (62, "Rehab_Flat_7", "F", 4),
    (63, "Rehab_Pillar_1", "P", 4),
    (64, "Rehab_Pillar_2", "P", 4),
    (65, "Rehab_Pillar_3", "P", 4),
    (66, "Rehab_Pillar_4", "P", 4),
    (67, "Rehab_Pillar_5", "P", 4),
    (68, "Rehab_Pillar_6", "P", 4),
    (69, "Rehab_Pillar_7", "P", 4),
]

INJURY_DAY = 17
TRACING_DAY = 70
PERFUSION_DAY = 84

# Valid test phases (derived from TIMELINE)
VALID_PHASES = [phase for _, phase, _, _ in TIMELINE]

# =============================================================================
# TRAY TYPE MODEL (Single Source of Truth for Apparatus Codes)
# =============================================================================

class TrayType(Base):
    """
    Physical tray apparatus types (extensible).

    This is the SINGLE SOURCE OF TRUTH for all tray type codes used throughout
    the system. Tray codes describe the physical apparatus, NOT the usage/purpose.
    The protocol phase defines what the tray is used for (training, testing, rehab).

    Default types: R (Ramp), E (Easy), F (Flat), P (Pillar)
    Users can add new tray topologies as needed.
    """
    __tablename__ = 'tray_types'

    code = Column(String(5), primary_key=True)  # Single letter code: R, E, F, P
    name = Column(String(50), nullable=False)  # Human-readable: "Ramp", "Easy", "Flat", "Pillar"
    description = Column(Text)  # Physical description of apparatus
    is_active = Column(Integer, default=1)  # Can be deactivated if no longer used
    created_at = Column(DateTime, default=datetime.now)

    @validates('code')
    def validate_code(self, key, value):
        if not value or len(value) > 5:
            raise ValueError(f"Tray type code must be 1-5 characters: {value}")
        return value.upper()


# =============================================================================
# PROTOCOL SYSTEM MODELS
# =============================================================================

class Protocol(Base):
    """
    Reusable behavioral testing protocol template.

    Protocols define the sequence of phases (training, testing, surgery, rehab)
    that cohorts follow. Cohorts are assigned to protocols, which auto-generates
    the expected schedule and empty data records.

    Supports versioning: editing a protocol creates a new version, and existing
    cohorts stay on their original version.

    Supports variants: a protocol can inherit from a parent protocol, with only
    the differences stored in protocol_phase_overrides.
    """
    __tablename__ = 'protocols'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # e.g., "Connectome Standard Protocol"
    version = Column(Integer, default=1)
    description = Column(Text)

    # For variants - parent protocol this inherits from
    parent_protocol_id = Column(Integer, ForeignKey('protocols.id'))

    is_active = Column(Integer, default=1)  # 1=available for use, 0=archived
    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    phases = relationship("ProtocolPhase", back_populates="protocol",
                         order_by="ProtocolPhase.phase_order")
    parent_protocol = relationship("Protocol", remote_side=[id], backref="variants")
    phase_overrides = relationship("ProtocolPhaseOverride", back_populates="protocol")
    cohorts = relationship("Cohort", back_populates="protocol")


class ProtocolPhase(Base):
    """
    A single phase within a protocol (e.g., "Training_Flat", "Surgery", "Rehab_Easy").

    Each phase specifies:
    - Duration in days
    - Tray type to use (references tray_types table)
    - Weekend handling rules
    - Sessions per day
    - Stagger group size (for phases like surgery)
    - What data is expected (weights, pellets, ladder, surgery)
    - Food deprivation status
    """
    __tablename__ = 'protocol_phases'
    __table_args__ = (
        UniqueConstraint('protocol_id', 'phase_order', name='unique_phase_order_per_protocol'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    protocol_id = Column(Integer, ForeignKey('protocols.id'), nullable=False)
    phase_order = Column(Integer, nullable=False)  # Sequential order within protocol

    # Phase identification
    phase_name = Column(String(100), nullable=False)  # e.g., "Training_Flat", "Pre-Injury_Test"
    phase_type = Column(String(50))  # Category: 'training', 'testing', 'surgery', 'recovery', 'rehab'

    # Timing
    duration_days = Column(Integer, nullable=False)  # Number of days
    include_weekends = Column(Integer, default=0)  # 0=skip weekends, 1=include
    day_of_week_constraint = Column(String(20))  # e.g., 'friday_only', 'weekdays', None

    # Tray configuration
    tray_type_code = Column(String(5), ForeignKey('tray_types.code'))  # FK to tray_types
    sessions_per_day = Column(Integer, default=1)  # Number of test sessions per day

    # Staggering for phases like surgery
    stagger_group_size = Column(Integer)  # NULL = no staggering, else how many mice per group
    stagger_interval_days = Column(Integer)  # Days between stagger groups (e.g., 7 for weekly)

    # Data expectations (what records to pre-generate)
    expects_weights = Column(Integer, default=1)  # Generate weight records
    expects_pellets = Column(Integer, default=1)  # Generate pellet score records
    expects_ladder = Column(Integer, default=0)  # Generate ladder testing records
    expects_surgery = Column(Integer, default=0)  # Expect surgery data entry

    # Food deprivation tracking
    food_dep_status = Column(String(20))  # 'on', 'off', 'transition'

    notes = Column(Text)

    # Relationships
    protocol = relationship("Protocol", back_populates="phases")
    subject_stagger_groups = relationship("SubjectStaggerGroup", back_populates="protocol_phase")


class ProtocolPhaseOverride(Base):
    """
    Stores differences from parent protocol for variant protocols.

    Only stores the specific field overrides, not the entire phase definition.
    This allows variants to automatically inherit changes to the parent protocol
    except for explicitly overridden fields.

    Example: A "Control Variant" protocol might override only the surgery phase
    to use sham surgery instead of contusion.
    """
    __tablename__ = 'protocol_phase_overrides'
    __table_args__ = (
        UniqueConstraint('protocol_id', 'base_phase_order', 'override_field',
                         name='unique_override_per_field'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    protocol_id = Column(Integer, ForeignKey('protocols.id'), nullable=False)  # The variant protocol
    base_phase_order = Column(Integer, nullable=False)  # Which phase from parent to override
    override_field = Column(String(50), nullable=False)  # Which field is overridden
    override_value = Column(Text)  # New value (stored as text, cast as needed)

    # Relationships
    protocol = relationship("Protocol", back_populates="phase_overrides")


class SubjectStaggerGroup(Base):
    """
    Assigns subjects to stagger groups for phases that require staggering.

    For example, in surgery phases, mice are often done in groups of 8 on
    different days. This table tracks which subjects belong to which stagger
    group and the actual date for each subject in each staggered phase.
    """
    __tablename__ = 'subject_stagger_groups'
    __table_args__ = (
        UniqueConstraint('subject_id', 'protocol_phase_id', name='unique_subject_phase_assignment'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    protocol_phase_id = Column(Integer, ForeignKey('protocol_phases.id'), nullable=False)
    stagger_group = Column(Integer, nullable=False)  # Which group (1, 2, 3, etc.)
    actual_date = Column(Date)  # Actual date for this subject in this phase

    # Relationships
    subject = relationship("Subject", back_populates="stagger_assignments")
    protocol_phase = relationship("ProtocolPhase", back_populates="subject_stagger_groups")


# =============================================================================
# CORE MODELS
# =============================================================================

class Project(Base):
    """Research project (e.g., CNT = Connectome, ENCR = Enhancer)."""
    __tablename__ = 'projects'

    project_code = Column(String(10), primary_key=True)
    project_name = Column(String(100), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    cohorts = relationship("Cohort", back_populates="project")

    @validates('project_code')
    def validate_project_code(self, key, value):
        if not PROJECT_CODE_PATTERN.match(value):
            raise ValueError(f"Project code must be uppercase letters only: {value}")
        return value.upper()


class Cohort(Base):
    """A batch of mice with the same start date."""
    __tablename__ = 'cohorts'

    cohort_id = Column(String(20), primary_key=True)  # CNT_05
    project_code = Column(String(10), ForeignKey('projects.project_code'), nullable=False)
    start_date = Column(Date, nullable=False)  # Food deprivation start
    num_mice = Column(Integer, default=16)
    notes = Column(Text)
    is_archived = Column(Integer, default=0)  # 1=archived (soft-deleted), 0=active
    archived_at = Column(DateTime)  # When it was archived
    archived_reason = Column(Text)  # Why it was archived
    created_at = Column(DateTime, default=datetime.now)

    # Protocol assignment
    protocol_id = Column(Integer, ForeignKey('protocols.id'))  # Assigned protocol
    protocol_version = Column(Integer)  # Version of protocol when assigned

    # Relationships
    project = relationship("Project", back_populates="cohorts")
    subjects = relationship("Subject", back_populates="cohort")
    virus_preps = relationship("VirusPrep", back_populates="cohort")
    protocol = relationship("Protocol", back_populates="cohorts")

    @validates('cohort_id')
    def validate_cohort_id(self, key, value):
        if not validate_cohort_id(value):
            raise ValueError(f"Cohort ID must be format XXX_NN (e.g., CNT_05): {value}")
        return value.upper()

    def get_phase_for_date(self, session_date: date) -> Optional[str]:
        """Get the test phase for a given date based on timeline."""
        if self.start_date is None:
            return None
        day_offset = (session_date - self.start_date).days
        for offset, phase, _, _ in TIMELINE:
            if offset == day_offset:
                return phase
        return None

    def get_valid_dates(self) -> List[tuple]:
        """Get all valid testing dates with their phases."""
        from datetime import timedelta
        if self.start_date is None:
            return []
        return [
            (self.start_date + timedelta(days=offset), phase, tray_type, trays)
            for offset, phase, tray_type, trays in TIMELINE
        ]


class Subject(Base):
    """Individual mouse/animal."""
    __tablename__ = 'subjects'

    subject_id = Column(String(20), primary_key=True)  # CNT_05_01
    cohort_id = Column(String(20), ForeignKey('cohorts.cohort_id'), nullable=False)
    date_of_birth = Column(Date)
    date_of_death = Column(Date)
    sex = Column(String(1), CheckConstraint("sex IN ('M', 'F')"))
    ear_tag = Column(String(20))
    notes = Column(Text)
    is_active = Column(Integer, default=1)  # 1=active, 0=excluded/dead
    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    cohort = relationship("Cohort", back_populates="subjects")
    weights = relationship("Weight", back_populates="subject")
    pellet_scores = relationship("PelletScore", back_populates="subject")
    surgeries = relationship("Surgery", back_populates="subject")
    ramp_entries = relationship("RampEntry", back_populates="subject")
    ladder_entries = relationship("LadderEntry", back_populates="subject")
    session_exceptions = relationship("SessionException", back_populates="subject")
    brain_samples = relationship("BrainSample", back_populates="subject")
    stagger_assignments = relationship("SubjectStaggerGroup", back_populates="subject")

    @validates('subject_id')
    def validate_subject_id(self, key, value):
        if not validate_subject_id(value):
            raise ValueError(f"Subject ID must be format XXX_NN_NN (e.g., CNT_05_01): {value}")
        return value.upper()

    @validates('sex')
    def validate_sex(self, key, value):
        if value is not None and value.upper() not in ('M', 'F'):
            raise ValueError(f"Sex must be 'M' or 'F': {value}")
        return value.upper() if value else None


class Weight(Base):
    """Daily weight measurement."""
    __tablename__ = 'weights'
    __table_args__ = (
        UniqueConstraint('subject_id', 'date', name='unique_weight_per_day'),
        CheckConstraint('weight_grams > 0 AND weight_grams < 100', name='valid_weight_range'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    date = Column(Date, nullable=False)
    weight_grams = Column(Float, nullable=False)
    weight_percent = Column(Float)  # Percentage of baseline weight
    notes = Column(Text)
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="weights")

    @validates('weight_grams')
    def validate_weight(self, key, value):
        if value is not None and (value <= 0 or value >= 100):
            raise ValueError(f"Weight must be between 0 and 100 grams: {value}")
        return value


class PelletScore(Base):
    """Individual pellet score during a testing session."""
    __tablename__ = 'pellet_scores'
    __table_args__ = (
        UniqueConstraint('subject_id', 'session_date', 'tray_type', 'tray_number', 'pellet_number',
                         name='unique_pellet_score'),
        CheckConstraint("tray_type IN ('E', 'F', 'P')", name='valid_tray_type'),
        CheckConstraint('tray_number BETWEEN 1 AND 4', name='valid_tray_number'),
        CheckConstraint('pellet_number BETWEEN 1 AND 20', name='valid_pellet_number'),
        CheckConstraint('score IN (0, 1, 2)', name='valid_score'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    session_date = Column(Date, nullable=False)
    test_phase = Column(String(50), nullable=False)
    tray_type = Column(String(1), nullable=False)  # E=Easy, F=Flat, P=Pillar
    tray_number = Column(Integer, nullable=False)  # 1-4
    pellet_number = Column(Integer, nullable=False)  # 1-20
    score = Column(Integer, nullable=False)  # 0=miss, 1=displaced, 2=retrieved
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="pellet_scores")

    @validates('tray_type')
    def validate_tray_type(self, key, value):
        if value.upper() not in ('E', 'F', 'P'):
            raise ValueError(f"Tray type must be E, F, or P: {value}")
        return value.upper()

    @validates('tray_number')
    def validate_tray_number(self, key, value):
        if not 1 <= value <= 4:
            raise ValueError(f"Tray number must be 1-4: {value}")
        return value

    @validates('pellet_number')
    def validate_pellet_number(self, key, value):
        if not 1 <= value <= 20:
            raise ValueError(f"Pellet number must be 1-20: {value}")
        return value

    @validates('score')
    def validate_score(self, key, value):
        if value not in (0, 1, 2):
            raise ValueError(f"Score must be 0 (miss), 1 (displaced), or 2 (retrieved): {value}")
        return value


class RampEntry(Base):
    """
    Ramp phase entry (days 0-3) tracking body weight and food consumption.

    During the ramp phase, mice are on food deprivation. We track:
    - Body weight each day
    - Tray start/end weights to calculate food consumption
    """
    __tablename__ = 'ramp_entries'
    __table_args__ = (
        UniqueConstraint('subject_id', 'date', name='unique_ramp_entry_per_day'),
        CheckConstraint('body_weight_grams > 0 AND body_weight_grams < 100',
                        name='valid_body_weight_range'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    date = Column(Date, nullable=False)
    ramp_day = Column(Integer)  # 0, 1, 2, or 3

    # Body weight
    body_weight_grams = Column(Float, nullable=False)
    weight_percent_baseline = Column(Float)  # Percentage of baseline (day 0) weight

    # Tray weights for food consumption
    tray_start_grams = Column(Float)  # Weight of food tray at start
    tray_end_grams = Column(Float)  # Weight of food tray at end
    food_consumed_grams = Column(Float)  # Calculated: start - end

    notes = Column(Text)
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="ramp_entries")

    @validates('body_weight_grams')
    def validate_body_weight(self, key, value):
        if value is not None and (value <= 0 or value >= 100):
            raise ValueError(f"Body weight must be between 0 and 100 grams: {value}")
        return value


class LadderEntry(Base):
    """
    Ladder testing entry tracking step success/failure.

    The horizontal ladder test measures locomotor function. Each entry
    records total steps, misses, and success rate for one animal on one date.
    """
    __tablename__ = 'ladder_entries'
    __table_args__ = (
        UniqueConstraint('subject_id', 'date', name='unique_ladder_per_day'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    date = Column(Date, nullable=False)
    test_type = Column(String(50))        # e.g., "Horizontal Ladder"
    camera_type = Column(String(50))      # e.g., "GoPro", "Webcam"
    quality = Column(String(20))          # Recording quality assessment
    steps_succeeded = Column(Integer)
    steps_missed = Column(Integer)
    steps_total = Column(Integer)
    miss_pct = Column(Float)
    notes = Column(Text)
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="ladder_entries")


class SessionException(Base):
    """
    Records exceptions during testing sessions (spilled tray, incomplete, etc.).

    These exceptions explain why data may be missing or anomalous.
    """
    __tablename__ = 'session_exceptions'
    __table_args__ = (
        CheckConstraint(
            "exception_type IN ('spilled_tray', 'incomplete_session', 'equipment_issue', "
            "'early_termination', 'animal_distress', 'other')",
            name='valid_exception_type'
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    session_date = Column(Date, nullable=False)
    exception_type = Column(String(30), nullable=False)

    # For spilled tray, which tray was affected
    tray_number = Column(Integer)  # 1-4, NULL if affects whole session

    description = Column(Text)  # Free text explanation
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="session_exceptions")


class Surgery(Base):
    """Surgery record (contusion, tracing, or perfusion)."""
    __tablename__ = 'surgeries'
    __table_args__ = (
        CheckConstraint("surgery_type IN ('contusion', 'tracing', 'perfusion')",
                        name='valid_surgery_type'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    surgery_date = Column(Date, nullable=False)
    surgery_type = Column(String(20), nullable=False)  # contusion, tracing, perfusion

    # Pre-surgery weight (workflow addition)
    pre_surgery_weight_grams = Column(Float)  # Weight taken just before surgery

    # Contusion-specific fields
    force_kdyn = Column(Float)  # Force in kilodynes
    displacement_um = Column(Float)  # Displacement in micrometers
    velocity_mm_s = Column(Float)  # Velocity in mm/s
    dwell_time_s = Column(Float)  # Dwell time in seconds

    # Tracing-specific fields
    virus_name = Column(String(100))
    virus_lot = Column(String(50))
    volume_nl = Column(Float)  # Volume in nanoliters
    injection_site = Column(String(100))

    # Perfusion-specific fields
    perfusion_solution = Column(String(100))
    perfusion_volume_ml = Column(Float)

    # Common fields
    surgeon = Column(String(50))
    anesthesia = Column(String(100))
    survived = Column(Integer, default=1)  # 1=survived, 0=did not survive surgery
    notes = Column(Text)
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="surgeries")

    @validates('surgery_type')
    def validate_surgery_type(self, key, value):
        valid_types = ('contusion', 'tracing', 'perfusion')
        if value.lower() not in valid_types:
            raise ValueError(f"Surgery type must be one of {valid_types}: {value}")
        return value.lower()


class VirusPrep(Base):
    """
    Virus preparation and injection calculations for tracing surgery.

    This is a cohort-level record used by the surgeon to track:
    - Which virus is being used (name, lot, titer)
    - Dilution calculations for preparing the injection solution
    - Final volumes and concentrations

    The surgeon uses this to prepare the virus properly before surgery day.
    """
    __tablename__ = 'virus_preps'
    __table_args__ = (
        UniqueConstraint('cohort_id', 'prep_date', name='unique_prep_per_cohort_date'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    cohort_id = Column(String(20), ForeignKey('cohorts.cohort_id'), nullable=False)
    prep_date = Column(Date, nullable=False)  # Date virus was prepared

    # Virus identification
    virus_name = Column(String(100), nullable=False)  # e.g., "AAV2-hSyn-GFP"
    virus_lot = Column(String(50))  # Lot number from supplier
    virus_source = Column(String(100))  # e.g., "Addgene", "UNC Vector Core"
    construct_id = Column(String(50))  # Plasmid/construct identifier

    # Stock virus properties
    stock_titer = Column(Float)  # Stock titer in gc/ml (e.g., 1e13)
    stock_titer_unit = Column(String(20), default='gc/ml')  # gc/ml, vg/ml, etc.
    stock_volume_ul = Column(Float)  # Volume of stock aliquot used (µL)

    # Dilution calculations
    target_titer = Column(Float)  # Desired final titer (e.g., 1e12)
    diluent = Column(String(50), default='PBS')  # What to dilute with
    diluent_volume_ul = Column(Float)  # Volume of diluent to add (µL)
    final_volume_ul = Column(Float)  # Total final volume (µL)
    final_titer = Column(Float)  # Actual final titer after dilution

    # Injection parameters
    injection_volume_nl = Column(Float)  # Volume per injection site (nL)
    num_injection_sites = Column(Integer)  # Number of sites per animal
    total_volume_per_animal_nl = Column(Float)  # injection_volume × num_sites
    num_animals = Column(Integer)  # Animals to be injected
    total_volume_needed_ul = Column(Float)  # Total volume needed + overage

    # Storage and handling
    storage_temp = Column(String(20), default='-80°C')  # Storage temperature
    thaw_date = Column(Date)  # When aliquot was thawed
    aliquot_id = Column(String(50))  # Identifier for specific aliquot used

    # Quality control
    sterility_check = Column(Integer, default=0)  # 1=passed, 0=not done
    potency_verified = Column(Integer, default=0)  # 1=verified, 0=not done

    # Notes and metadata
    preparation_notes = Column(Text)  # Special handling instructions
    calculation_notes = Column(Text)  # Notes on dilution math
    surgeon = Column(String(50))  # Who prepared the virus
    entered_by = Column(String(50))
    entered_at = Column(DateTime, default=datetime.now)

    # Relationships
    cohort = relationship("Cohort", back_populates="virus_preps")

    @validates('stock_titer', 'target_titer', 'final_titer')
    def validate_titer(self, key, value):
        """Titer must be positive."""
        if value is not None and value <= 0:
            raise ValueError(f"{key} must be positive: {value}")
        return value

    def calculate_dilution(self):
        """Calculate diluent volume needed for target titer."""
        if self.stock_titer and self.target_titer and self.stock_volume_ul:
            # C1V1 = C2V2, solve for V2
            # stock_titer * stock_volume = target_titer * final_volume
            final_vol = (self.stock_titer * self.stock_volume_ul) / self.target_titer
            diluent_vol = final_vol - self.stock_volume_ul
            return max(0, diluent_vol)  # Can't add negative diluent
        return None

    def calculate_total_needed(self, overage_factor=1.2):
        """Calculate total volume needed with overage."""
        if self.injection_volume_nl and self.num_injection_sites and self.num_animals:
            per_animal_nl = self.injection_volume_nl * self.num_injection_sites
            total_nl = per_animal_nl * self.num_animals
            total_ul = total_nl / 1000  # Convert to µL
            return total_ul * overage_factor  # Add 20% overage by default
        return None


class PipelineData(Base):
    """Auto-populated data from MouseReach and BrainGlobe pipelines."""
    __tablename__ = 'pipeline_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    data_type = Column(String(50), nullable=False)  # mousereach_reach, brainglobe_region
    data_json = Column(Text, nullable=False)  # Full JSON blob
    source_file = Column(String(500))
    version = Column(String(20))
    imported_at = Column(DateTime, default=datetime.now)


class ReachData(Base):
    """
    Flattened per-reach records from MouseReach pipeline.

    One row per reach with linked outcome, kinematic features, and session
    context. Populated by mousereach-sync from _features.json files (Step 5).

    This is the primary analysis table for reach behavior data.
    """
    __tablename__ = 'reach_data'
    __table_args__ = (
        UniqueConstraint('video_name', 'reach_id', name='uq_reach_video'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Session identity (parsed from video name: YYYYMMDD_CNTxxxx_TypeRun)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    video_name = Column(String(200), nullable=False)
    session_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    tray_type = Column(String(10))  # P, E, F, etc.
    run_number = Column(Integer)

    # Reach identity
    segment_num = Column(Integer, nullable=False)
    reach_id = Column(Integer, nullable=False)  # Global ID within video
    reach_num = Column(Integer, nullable=False)  # Within segment (1-indexed)

    # Outcome linkage (the core purpose)
    outcome = Column(String(30))  # retrieved|displaced_sa|displaced_outside|untouched|NULL
    causal_reach = Column(Integer, nullable=False, default=0)  # 1 = caused the outcome
    interaction_frame = Column(Integer)  # Frame when pellet was touched
    distance_to_interaction = Column(Integer)  # Frames between apex and contact

    # Reach context
    is_first_reach = Column(Integer, nullable=False, default=0)
    is_last_reach = Column(Integer, nullable=False, default=0)
    n_reaches_in_segment = Column(Integer, nullable=False, default=0)

    # Temporal features
    start_frame = Column(Integer, nullable=False)
    apex_frame = Column(Integer)
    end_frame = Column(Integer, nullable=False)
    duration_frames = Column(Integer, nullable=False)

    # Extent features
    max_extent_pixels = Column(Float)
    max_extent_ruler = Column(Float)
    max_extent_mm = Column(Float)

    # Velocity features
    velocity_at_apex_px_per_frame = Column(Float)
    velocity_at_apex_mm_per_sec = Column(Float)
    peak_velocity_px_per_frame = Column(Float)
    mean_velocity_px_per_frame = Column(Float)

    # Trajectory features
    trajectory_straightness = Column(Float)
    trajectory_smoothness = Column(Float)

    # Hand orientation
    hand_angle_at_apex_deg = Column(Float)
    hand_rotation_total_deg = Column(Float)

    # Grasp aperture
    grasp_aperture_max_mm = Column(Float)
    grasp_aperture_at_contact_mm = Column(Float)

    # Body/posture at apex
    head_width_at_apex_mm = Column(Float)
    nose_to_slit_at_apex_mm = Column(Float)
    head_angle_at_apex_deg = Column(Float)
    head_angle_change_deg = Column(Float)

    # Spatial context
    apex_distance_to_pellet_mm = Column(Float)
    lateral_deviation_mm = Column(Float)

    # Tracking quality
    mean_likelihood = Column(Float)
    frames_low_confidence = Column(Integer, default=0)
    tracking_quality_score = Column(Float)

    # Flags
    flagged_for_review = Column(Integer, nullable=False, default=0)
    flag_reason = Column(Text)

    # Segment-level context (denormalized for easy querying)
    segment_outcome = Column(String(30))
    segment_outcome_confidence = Column(Float)
    segment_outcome_flagged = Column(Integer, default=0)
    attention_score = Column(Float)
    pellet_position_idealness = Column(Float)

    # Metadata
    source_file = Column(String(500), nullable=False)
    extractor_version = Column(String(20))
    imported_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject")


# =============================================================================
# BRAINGLOBE INTEGRATION MODELS
# =============================================================================

class BrainSample(Base):
    """
    Brain sample linking a subject to their tissue imaging data.

    Brain naming convention: {BRAIN#}_{PROJECT}_{COHORT}_{SUBJECT}_{MAG}x_z{ZSTEP}
    Example: 349_CNT_01_02_1p625x_z4
    """
    __tablename__ = 'brain_samples'
    __table_args__ = (
        UniqueConstraint('subject_id', 'brain_id', name='unique_brain_per_subject'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    subject_id = Column(String(20), ForeignKey('subjects.subject_id'), nullable=False)
    brain_id = Column(String(50), nullable=False)  # e.g., "349_CNT_01_02_1p625x_z4"
    brain_number = Column(Integer)  # Sequential brain number (349)

    # Imaging parameters
    magnification = Column(Float)  # e.g., 1.625
    z_step_um = Column(Float)  # Z-step in micrometers (e.g., 4)
    voxel_size_x_um = Column(Float)  # Voxel size in X
    voxel_size_y_um = Column(Float)  # Voxel size in Y
    voxel_size_z_um = Column(Float)  # Voxel size in Z

    # Registration info
    atlas_name = Column(String(50), default='allen_mouse_25um')  # Atlas used
    registration_date = Column(Date)
    registration_quality = Column(Float)  # Quality score 0-1
    registration_approved = Column(Integer, default=0)  # 1=approved, 0=not approved

    # File paths (relative to brain folder)
    raw_path = Column(String(500))  # Path to raw .ims file
    registered_path = Column(String(500))  # Path to registered data
    detection_xml_path = Column(String(500))  # Path to cell detection XML

    # Status
    status = Column(String(20), default='pending')  # pending, registered, detected, analyzed
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    subject = relationship("Subject", back_populates="brain_samples")
    region_counts = relationship("RegionCount", back_populates="brain_sample")
    detected_cells = relationship("DetectedCell", back_populates="brain_sample")
    calibration_runs = relationship("CalibrationRun", back_populates="brain_sample")


class RegionCount(Base):
    """
    Cell counts per brain region from BrainGlobe analysis.

    Each row represents the count of cells in one brain region for one brain sample.
    Regions are from the Allen Mouse Brain Atlas.
    """
    __tablename__ = 'region_counts'
    __table_args__ = (
        UniqueConstraint('brain_sample_id', 'region_id', 'hemisphere', 'cell_type',
                         name='unique_region_count'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    brain_sample_id = Column(Integer, ForeignKey('brain_samples.id'), nullable=False)

    # Region identification (Allen Atlas)
    region_id = Column(Integer, nullable=False)  # Allen Atlas region ID
    region_name = Column(String(200), nullable=False)  # Full region name
    region_acronym = Column(String(20))  # Abbreviated name (e.g., "MOp")
    parent_region_id = Column(Integer)  # Parent region for hierarchy
    hemisphere = Column(String(5), default='both')  # 'left', 'right', or 'both'

    # Counts
    cell_count = Column(Integer, nullable=False, default=0)
    cell_density = Column(Float)  # Cells per mm³
    region_volume_mm3 = Column(Float)  # Region volume

    # Cell type (if classification was done)
    cell_type = Column(String(50), default='all')  # 'all', 'neuron', 'glia', etc.

    # Detection parameters used
    calibration_run_id = Column(Integer, ForeignKey('calibration_runs.id'))

    # Quality flags
    is_final = Column(Integer, default=0)  # 1=final production count, 0=calibration
    confidence = Column(Float)  # Confidence score 0-1

    # Metadata
    source_file = Column(String(500))  # CSV file this came from
    imported_at = Column(DateTime, default=datetime.now)

    # Relationships
    brain_sample = relationship("BrainSample", back_populates="region_counts")
    calibration_run = relationship("CalibrationRun", back_populates="region_counts")


class DetectedCell(Base):
    """
    Individual detected cell with coordinates in atlas space.

    Stores XYZ coordinates for each detected cell, allowing detailed spatial analysis.
    Optional - only populated if full cell coordinates are needed.
    """
    __tablename__ = 'detected_cells'
    __table_args__ = (
        # Index for spatial queries
        {'sqlite_autoincrement': True},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    brain_sample_id = Column(Integer, ForeignKey('brain_samples.id'), nullable=False)

    # Coordinates in atlas space (micrometers)
    x_um = Column(Float, nullable=False)
    y_um = Column(Float, nullable=False)
    z_um = Column(Float, nullable=False)

    # Region assignment
    region_id = Column(Integer)  # Allen Atlas region ID at this coordinate
    region_name = Column(String(200))
    hemisphere = Column(String(5))  # 'left' or 'right'

    # Cell properties
    cell_type = Column(String(50))  # Classification result
    confidence = Column(Float)  # Detection confidence 0-1
    volume_um3 = Column(Float)  # Estimated cell volume

    # Detection source
    calibration_run_id = Column(Integer, ForeignKey('calibration_runs.id'))
    detection_channel = Column(String(20))  # Which channel detected this cell

    # Metadata
    imported_at = Column(DateTime, default=datetime.now)

    # Relationships
    brain_sample = relationship("BrainSample", back_populates="detected_cells")
    calibration_run = relationship("CalibrationRun", back_populates="detected_cells")


class CalibrationRun(Base):
    """
    BrainGlobe cell detection calibration run tracking.

    Records all detection attempts with their parameters for reproducibility.
    Maps to calibration_runs.csv from the BrainGlobe pipeline.
    """
    __tablename__ = 'calibration_runs'

    id = Column(Integer, primary_key=True, autoincrement=True)
    brain_sample_id = Column(Integer, ForeignKey('brain_samples.id'), nullable=False)
    run_id = Column(String(50), nullable=False)  # Unique run identifier

    # Detection parameters
    ball_xy_um = Column(Float)  # Ball filter XY size
    ball_z_um = Column(Float)  # Ball filter Z size
    soma_diameter_um = Column(Float)  # Expected soma diameter
    threshold = Column(Float)  # Detection threshold

    # Classifier settings
    classifier_name = Column(String(100))  # Name of trained classifier
    classifier_path = Column(String(500))  # Path to classifier model

    # Results
    cells_detected = Column(Integer)  # Number of cells found
    detection_time_s = Column(Float)  # Processing time
    status = Column(String(20))  # 'started', 'completed', 'failed', 'best'

    # Quality markers
    is_best = Column(Integer, default=0)  # 1=marked as best run for this brain
    is_production = Column(Integer, default=0)  # 1=used for final analysis

    # Metadata
    run_date = Column(DateTime)
    user = Column(String(50))
    notes = Column(Text)
    source_csv = Column(String(500))  # Original calibration_runs.csv path
    imported_at = Column(DateTime, default=datetime.now)

    # Relationships
    brain_sample = relationship("BrainSample", back_populates="calibration_runs")
    region_counts = relationship("RegionCount", back_populates="calibration_run")
    detected_cells = relationship("DetectedCell", back_populates="calibration_run")


class ArchivedSummary(Base):
    """
    Archived Excel summary/stats data for validation against computed values.

    When importing from Excel, derived sheets (3c_Manual_Summary, 7_Stats)
    are archived here. The system then computes the same metrics from source
    data and compares against these archived values to validate import correctness.
    """
    __tablename__ = 'archived_summaries'

    id = Column(Integer, primary_key=True, autoincrement=True)
    cohort_id = Column(String(20), ForeignKey('cohorts.cohort_id'), nullable=False)
    sheet_name = Column(String(100), nullable=False)  # '3c_Manual_Summary', '7_Stats', etc.
    subject_id = Column(String(20))        # Per-animal rows (nullable for cohort-level)
    date = Column(Date)                     # Per-date columns (nullable for phase-level)
    phase = Column(String(50))             # Test phase label
    metric_name = Column(String(50), nullable=False)  # 'retrieved_pct', 'contacted_pct', etc.
    metric_value = Column(Float)           # The Excel-computed value
    source_file = Column(String(500))
    imported_at = Column(DateTime, default=datetime.now)


class AuditLog(Base):
    """Audit trail for all data modifications."""
    __tablename__ = 'audit_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.now)
    user = Column(String(50))
    action = Column(String(20))  # INSERT, UPDATE, DELETE
    table_name = Column(String(50))
    record_id = Column(String(50))
    old_values = Column(Text)  # JSON
    new_values = Column(Text)  # JSON


# =============================================================================
# DATABASE INITIALIZATION
# =============================================================================

@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    """Enable foreign keys for SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_default_projects(session: Session):
    """Create default project entries."""
    defaults = [
        Project(project_code='CNT', project_name='Connectome',
                description='Spinal cord injury connectomics study'),
        Project(project_code='ENCR', project_name='Enhancer',
                description='Enhancer aim project'),
    ]
    for proj in defaults:
        existing = session.query(Project).filter_by(project_code=proj.project_code).first()
        if not existing:
            session.add(proj)
    session.commit()


def create_default_tray_types(session: Session):
    """
    Create default tray type entries.

    This is the SINGLE SOURCE OF TRUTH for tray apparatus codes.
    Users can add new tray types via the Protocol Builder UI.
    """
    defaults = [
        TrayType(code='R', name='Ramp',
                 description='Ramp apparatus for food deprivation phase'),
        TrayType(code='E', name='Easy',
                 description='Easy tray - closer backstop, closer pellet divot, no recess'),
        TrayType(code='F', name='Flat',
                 description='Standard flat tray'),
        TrayType(code='P', name='Pillar',
                 description='Pillar tray - main testing apparatus'),
    ]
    for tray_type in defaults:
        existing = session.query(TrayType).filter_by(code=tray_type.code).first()
        if not existing:
            session.add(tray_type)
    session.commit()


def get_tray_types(session: Session, active_only: bool = True) -> List[TrayType]:
    """Get all tray types (for dropdowns, validation, etc.)."""
    query = session.query(TrayType)
    if active_only:
        query = query.filter(TrayType.is_active == 1)
    return query.order_by(TrayType.code).all()


def add_tray_type(session: Session, code: str, name: str, description: str = None) -> TrayType:
    """
    Add a new tray type to the system.

    Args:
        session: Database session
        code: Single letter code (e.g., 'G' for Gap)
        name: Human-readable name (e.g., 'Gap')
        description: Physical description of the apparatus

    Returns:
        The created TrayType object

    Raises:
        ValueError: If code already exists
    """
    code = code.upper()
    existing = session.query(TrayType).filter_by(code=code).first()
    if existing:
        raise ValueError(f"Tray type code '{code}' already exists")

    tray_type = TrayType(code=code, name=name, description=description)
    session.add(tray_type)
    session.commit()
    return tray_type
