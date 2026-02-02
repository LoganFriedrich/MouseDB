"""
Protocol Management Module for Connectome Data Entry.

This module provides functions for creating, managing, and using behavioral
testing protocols. Protocols define the sequence of phases (training, testing,
surgery, rehab) that cohorts follow.

Key Functions:
- Tray type management (get_tray_types, add_tray_type)
- Protocol CRUD (create_protocol, get_protocol, list_protocols)
- Variant support (create_variant, get_effective_phases)
- Cohort assignment (assign_cohort_to_protocol)
- Schedule generation (generate_schedule, generate_empty_records)
"""

from datetime import date, timedelta
from typing import List, Dict, Optional, Any
from sqlalchemy.orm import Session

from .schema import (
    TrayType, Protocol, ProtocolPhase, ProtocolPhaseOverride,
    SubjectStaggerGroup, Cohort, Subject, Weight, PelletScore, RampEntry
)


# =============================================================================
# TRAY TYPE MANAGEMENT
# =============================================================================

def get_tray_types(session: Session, active_only: bool = True) -> List[TrayType]:
    """
    Get all tray types from the database.

    Args:
        session: Database session
        active_only: If True, only return active tray types

    Returns:
        List of TrayType objects ordered by code
    """
    query = session.query(TrayType)
    if active_only:
        query = query.filter(TrayType.is_active == 1)
    return query.order_by(TrayType.code).all()


def get_tray_type(session: Session, code: str) -> Optional[TrayType]:
    """Get a specific tray type by code."""
    return session.query(TrayType).filter_by(code=code.upper()).first()


def add_tray_type(
    session: Session,
    code: str,
    name: str,
    description: str = None
) -> TrayType:
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
    session.flush()  # Get the ID without committing
    return tray_type


def deactivate_tray_type(session: Session, code: str) -> bool:
    """
    Deactivate a tray type (never delete - preserves history).

    Args:
        session: Database session
        code: Tray type code to deactivate

    Returns:
        True if deactivated, False if not found
    """
    tray_type = session.query(TrayType).filter_by(code=code.upper()).first()
    if tray_type:
        tray_type.is_active = 0
        return True
    return False


# =============================================================================
# PROTOCOL MANAGEMENT
# =============================================================================

def create_protocol(
    session: Session,
    name: str,
    phases: List[Dict[str, Any]],
    description: str = None,
    parent_protocol_id: int = None
) -> Protocol:
    """
    Create a new protocol with phases.

    Args:
        session: Database session
        name: Protocol name (e.g., "Connectome Standard Protocol")
        phases: List of phase dictionaries with keys:
            - phase_name: str (required)
            - duration_days: int (required)
            - phase_type: str (optional) - 'training', 'testing', 'surgery', etc.
            - tray_type_code: str (optional) - 'R', 'E', 'F', 'P'
            - include_weekends: bool (optional, default False)
            - day_of_week_constraint: str (optional)
            - sessions_per_day: int (optional, default 1)
            - stagger_group_size: int (optional)
            - stagger_interval_days: int (optional)
            - expects_weights: bool (optional, default True)
            - expects_pellets: bool (optional, default True)
            - expects_ladder: bool (optional, default False)
            - expects_surgery: bool (optional, default False)
            - food_dep_status: str (optional) - 'on', 'off', 'transition'
            - notes: str (optional)
        description: Protocol description
        parent_protocol_id: If creating a variant, the parent protocol ID

    Returns:
        The created Protocol object with phases
    """
    # Create the protocol
    protocol = Protocol(
        name=name,
        description=description,
        parent_protocol_id=parent_protocol_id,
        version=1
    )
    session.add(protocol)
    session.flush()  # Get the protocol ID

    # Create phases
    for order, phase_data in enumerate(phases, start=1):
        phase = ProtocolPhase(
            protocol_id=protocol.id,
            phase_order=order,
            phase_name=phase_data['phase_name'],
            duration_days=phase_data['duration_days'],
            phase_type=phase_data.get('phase_type'),
            tray_type_code=phase_data.get('tray_type_code'),
            include_weekends=1 if phase_data.get('include_weekends') else 0,
            day_of_week_constraint=phase_data.get('day_of_week_constraint'),
            sessions_per_day=phase_data.get('sessions_per_day', 1),
            stagger_group_size=phase_data.get('stagger_group_size'),
            stagger_interval_days=phase_data.get('stagger_interval_days'),
            expects_weights=1 if phase_data.get('expects_weights', True) else 0,
            expects_pellets=1 if phase_data.get('expects_pellets', True) else 0,
            expects_ladder=1 if phase_data.get('expects_ladder', False) else 0,
            expects_surgery=1 if phase_data.get('expects_surgery', False) else 0,
            food_dep_status=phase_data.get('food_dep_status'),
            notes=phase_data.get('notes')
        )
        session.add(phase)

    session.flush()
    return protocol


def get_protocol(session: Session, protocol_id: int) -> Optional[Protocol]:
    """
    Get a protocol by ID with all its phases.

    Args:
        session: Database session
        protocol_id: Protocol ID

    Returns:
        Protocol object with phases loaded, or None if not found
    """
    return session.query(Protocol).filter_by(id=protocol_id).first()


def get_protocol_by_name(session: Session, name: str) -> Optional[Protocol]:
    """Get a protocol by name."""
    return session.query(Protocol).filter_by(name=name, is_active=1).first()


def list_protocols(session: Session, active_only: bool = True) -> List[Protocol]:
    """
    List all protocols.

    Args:
        session: Database session
        active_only: If True, only return active protocols

    Returns:
        List of Protocol objects
    """
    query = session.query(Protocol)
    if active_only:
        query = query.filter(Protocol.is_active == 1)
    return query.order_by(Protocol.name).all()


def update_protocol_version(session: Session, protocol_id: int) -> Protocol:
    """
    Increment protocol version (call when editing a protocol).

    Args:
        session: Database session
        protocol_id: Protocol ID to version

    Returns:
        Updated Protocol object
    """
    protocol = session.query(Protocol).filter_by(id=protocol_id).first()
    if protocol:
        protocol.version += 1
    return protocol


def archive_protocol(session: Session, protocol_id: int) -> bool:
    """
    Archive a protocol (set is_active=0).

    Args:
        session: Database session
        protocol_id: Protocol ID to archive

    Returns:
        True if archived, False if not found
    """
    protocol = session.query(Protocol).filter_by(id=protocol_id).first()
    if protocol:
        protocol.is_active = 0
        return True
    return False


# =============================================================================
# PROTOCOL VARIANTS
# =============================================================================

def create_variant(
    session: Session,
    parent_protocol_id: int,
    name: str,
    overrides: List[Dict[str, Any]],
    description: str = None
) -> Protocol:
    """
    Create a protocol variant that inherits from a parent protocol.

    Args:
        session: Database session
        parent_protocol_id: ID of the parent protocol to inherit from
        name: Name for the variant
        overrides: List of override dictionaries with keys:
            - base_phase_order: int (which phase to override)
            - field: str (which field to override)
            - value: Any (new value, will be stored as text)
        description: Variant description

    Returns:
        The created Protocol object (variant)

    Raises:
        ValueError: If parent protocol not found
    """
    parent = session.query(Protocol).filter_by(id=parent_protocol_id).first()
    if not parent:
        raise ValueError(f"Parent protocol {parent_protocol_id} not found")

    # Create the variant protocol (no phases - inherits from parent)
    variant = Protocol(
        name=name,
        description=description,
        parent_protocol_id=parent_protocol_id,
        version=1
    )
    session.add(variant)
    session.flush()

    # Add overrides
    for override_data in overrides:
        override = ProtocolPhaseOverride(
            protocol_id=variant.id,
            base_phase_order=override_data['base_phase_order'],
            override_field=override_data['field'],
            override_value=str(override_data['value']) if override_data['value'] is not None else None
        )
        session.add(override)

    session.flush()
    return variant


def get_effective_phases(session: Session, protocol_id: int) -> List[Dict[str, Any]]:
    """
    Get the effective phases for a protocol, applying variant overrides if applicable.

    For a base protocol, returns its phases directly.
    For a variant, returns parent phases with overrides applied.

    Args:
        session: Database session
        protocol_id: Protocol ID

    Returns:
        List of phase dictionaries with all properties
    """
    protocol = session.query(Protocol).filter_by(id=protocol_id).first()
    if not protocol:
        return []

    # If this is a base protocol (no parent), return its phases directly
    if protocol.parent_protocol_id is None:
        phases = []
        for phase in protocol.phases:
            phases.append(_phase_to_dict(phase))
        return phases

    # This is a variant - get parent phases and apply overrides
    parent_phases = get_effective_phases(session, protocol.parent_protocol_id)

    # Get overrides for this variant
    overrides = session.query(ProtocolPhaseOverride).filter_by(
        protocol_id=protocol_id
    ).all()

    # Build override lookup: {phase_order: {field: value}}
    override_lookup = {}
    for override in overrides:
        if override.base_phase_order not in override_lookup:
            override_lookup[override.base_phase_order] = {}
        override_lookup[override.base_phase_order][override.override_field] = override.override_value

    # Apply overrides to parent phases
    for phase in parent_phases:
        phase_order = phase['phase_order']
        if phase_order in override_lookup:
            for field, value in override_lookup[phase_order].items():
                # Convert value back to appropriate type
                phase[field] = _cast_override_value(field, value)

    return parent_phases


def _phase_to_dict(phase: ProtocolPhase) -> Dict[str, Any]:
    """Convert a ProtocolPhase object to a dictionary."""
    return {
        'id': phase.id,
        'protocol_id': phase.protocol_id,
        'phase_order': phase.phase_order,
        'phase_name': phase.phase_name,
        'phase_type': phase.phase_type,
        'duration_days': phase.duration_days,
        'tray_type_code': phase.tray_type_code,
        'include_weekends': bool(phase.include_weekends),
        'day_of_week_constraint': phase.day_of_week_constraint,
        'sessions_per_day': phase.sessions_per_day,
        'stagger_group_size': phase.stagger_group_size,
        'stagger_interval_days': phase.stagger_interval_days,
        'expects_weights': bool(phase.expects_weights),
        'expects_pellets': bool(phase.expects_pellets),
        'expects_ladder': bool(phase.expects_ladder),
        'expects_surgery': bool(phase.expects_surgery),
        'food_dep_status': phase.food_dep_status,
        'notes': phase.notes,
    }


def _cast_override_value(field: str, value: str) -> Any:
    """Cast an override value from string to appropriate type."""
    if value is None:
        return None

    # Integer fields
    int_fields = ['duration_days', 'sessions_per_day', 'stagger_group_size',
                  'stagger_interval_days', 'phase_order']
    if field in int_fields:
        return int(value)

    # Boolean fields (stored as 0/1)
    bool_fields = ['include_weekends', 'expects_weights', 'expects_pellets',
                   'expects_ladder', 'expects_surgery']
    if field in bool_fields:
        return value.lower() in ('1', 'true', 'yes')

    # String fields - return as-is
    return value


# =============================================================================
# COHORT ASSIGNMENT
# =============================================================================

def assign_cohort_to_protocol(
    session: Session,
    cohort_id: str,
    protocol_id: int,
    auto_generate_records: bool = True
) -> Cohort:
    """
    Assign a cohort to a protocol.

    Args:
        session: Database session
        cohort_id: Cohort ID to assign
        protocol_id: Protocol ID to assign to
        auto_generate_records: If True, generate empty data records based on protocol

    Returns:
        Updated Cohort object

    Raises:
        ValueError: If cohort or protocol not found
    """
    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if not cohort:
        raise ValueError(f"Cohort {cohort_id} not found")

    protocol = session.query(Protocol).filter_by(id=protocol_id).first()
    if not protocol:
        raise ValueError(f"Protocol {protocol_id} not found")

    # Assign protocol
    cohort.protocol_id = protocol_id
    cohort.protocol_version = protocol.version

    session.flush()

    # Generate records if requested
    if auto_generate_records and cohort.start_date:
        generate_schedule(session, cohort_id)
        generate_empty_records(session, cohort_id)

    return cohort


# =============================================================================
# SCHEDULE GENERATION
# =============================================================================

def generate_schedule(session: Session, cohort_id: str) -> Dict[str, Any]:
    """
    Generate the complete schedule for a cohort based on its assigned protocol.

    This calculates the actual dates for each phase and handles:
    - Weekend skipping (if include_weekends=False)
    - Day-of-week constraints (e.g., friday_only for testing)
    - Stagger groups (e.g., surgery in waves)

    Args:
        session: Database session
        cohort_id: Cohort ID to generate schedule for

    Returns:
        Dictionary with schedule information:
        {
            'cohort_id': str,
            'protocol_id': int,
            'start_date': date,
            'phases': [
                {
                    'phase_name': str,
                    'start_date': date,
                    'end_date': date,
                    'days': [date, ...],  # Actual working days
                    'stagger_groups': {1: date, 2: date, ...} if staggered
                },
                ...
            ],
            'end_date': date
        }

    Raises:
        ValueError: If cohort has no protocol or start_date
    """
    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if not cohort:
        raise ValueError(f"Cohort {cohort_id} not found")
    if not cohort.protocol_id:
        raise ValueError(f"Cohort {cohort_id} has no assigned protocol")
    if not cohort.start_date:
        raise ValueError(f"Cohort {cohort_id} has no start date")

    # Get effective phases (handles variants)
    phases = get_effective_phases(session, cohort.protocol_id)

    schedule = {
        'cohort_id': cohort_id,
        'protocol_id': cohort.protocol_id,
        'start_date': cohort.start_date,
        'phases': [],
        'end_date': None
    }

    current_date = cohort.start_date

    for phase in phases:
        phase_schedule = {
            'phase_name': phase['phase_name'],
            'phase_order': phase['phase_order'],
            'phase_type': phase['phase_type'],
            'tray_type_code': phase['tray_type_code'],
            'sessions_per_day': phase['sessions_per_day'],
            'start_date': current_date,
            'days': [],
            'stagger_groups': None
        }

        # Calculate days for this phase
        duration = phase['duration_days']
        include_weekends = phase['include_weekends']
        day_constraint = phase['day_of_week_constraint']

        days_added = 0
        check_date = current_date

        while days_added < duration:
            is_weekend = check_date.weekday() >= 5  # Saturday=5, Sunday=6

            # Check if this day should be included
            include_day = True
            if not include_weekends and is_weekend:
                include_day = False
            if day_constraint:
                include_day = include_day and _matches_day_constraint(check_date, day_constraint)

            if include_day:
                phase_schedule['days'].append(check_date)
                days_added += 1

            check_date += timedelta(days=1)

        # Set end date for phase
        phase_schedule['end_date'] = phase_schedule['days'][-1] if phase_schedule['days'] else current_date

        # Handle stagger groups if applicable
        if phase['stagger_group_size']:
            phase_schedule['stagger_groups'] = _calculate_stagger_dates(
                session, cohort, phase, phase_schedule['start_date']
            )

        schedule['phases'].append(phase_schedule)

        # Move current_date to day after phase ends
        current_date = phase_schedule['end_date'] + timedelta(days=1)

    schedule['end_date'] = current_date - timedelta(days=1)
    return schedule


def _matches_day_constraint(check_date: date, constraint: str) -> bool:
    """Check if a date matches a day-of-week constraint."""
    weekday = check_date.weekday()  # Monday=0, Sunday=6

    if constraint == 'friday_only':
        return weekday == 4
    elif constraint == 'weekdays':
        return weekday < 5
    elif constraint == 'monday_only':
        return weekday == 0
    elif constraint == 'wednesday_only':
        return weekday == 2
    # Add more constraints as needed

    return True  # Unknown constraint - allow


def _calculate_stagger_dates(
    session: Session,
    cohort: Cohort,
    phase: Dict[str, Any],
    phase_start_date: date
) -> Dict[int, date]:
    """
    Calculate stagger group dates for a phase.

    Args:
        session: Database session
        cohort: Cohort object
        phase: Phase dictionary
        phase_start_date: Start date of the phase

    Returns:
        Dictionary mapping stagger group number to date
    """
    group_size = phase['stagger_group_size']
    interval_days = phase.get('stagger_interval_days', 7)  # Default 7 days between groups

    # Get subjects in cohort
    subjects = session.query(Subject).filter_by(
        cohort_id=cohort.cohort_id,
        is_active=1
    ).order_by(Subject.subject_id).all()

    # Calculate number of groups needed
    num_groups = (len(subjects) + group_size - 1) // group_size

    # Calculate dates for each group
    stagger_dates = {}
    for group_num in range(1, num_groups + 1):
        group_date = phase_start_date + timedelta(days=(group_num - 1) * interval_days)
        stagger_dates[group_num] = group_date

    return stagger_dates


def assign_subjects_to_stagger_groups(
    session: Session,
    cohort_id: str,
    phase_id: int,
    assignments: Dict[str, int] = None
) -> List[SubjectStaggerGroup]:
    """
    Assign subjects to stagger groups for a phase.

    Args:
        session: Database session
        cohort_id: Cohort ID
        phase_id: Protocol phase ID
        assignments: Optional dict of {subject_id: group_number}.
                    If None, auto-assigns sequentially.

    Returns:
        List of created SubjectStaggerGroup objects
    """
    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    phase = session.query(ProtocolPhase).filter_by(id=phase_id).first()

    if not cohort or not phase:
        raise ValueError("Cohort or phase not found")

    subjects = session.query(Subject).filter_by(
        cohort_id=cohort_id,
        is_active=1
    ).order_by(Subject.subject_id).all()

    group_size = phase.stagger_group_size or len(subjects)

    # Generate auto-assignments if not provided
    if assignments is None:
        assignments = {}
        for i, subject in enumerate(subjects):
            group_num = (i // group_size) + 1
            assignments[subject.subject_id] = group_num

    # Get schedule to find actual dates
    schedule = generate_schedule(session, cohort_id)
    phase_schedule = None
    for ps in schedule['phases']:
        if ps['phase_order'] == phase.phase_order:
            phase_schedule = ps
            break

    created = []
    for subject_id, group_num in assignments.items():
        # Check if assignment already exists
        existing = session.query(SubjectStaggerGroup).filter_by(
            subject_id=subject_id,
            protocol_phase_id=phase_id
        ).first()

        if existing:
            existing.stagger_group = group_num
            if phase_schedule and phase_schedule.get('stagger_groups'):
                existing.actual_date = phase_schedule['stagger_groups'].get(group_num)
            created.append(existing)
        else:
            actual_date = None
            if phase_schedule and phase_schedule.get('stagger_groups'):
                actual_date = phase_schedule['stagger_groups'].get(group_num)

            ssg = SubjectStaggerGroup(
                subject_id=subject_id,
                protocol_phase_id=phase_id,
                stagger_group=group_num,
                actual_date=actual_date
            )
            session.add(ssg)
            created.append(ssg)

    session.flush()
    return created


# =============================================================================
# EMPTY RECORD GENERATION
# =============================================================================

def generate_empty_records(
    session: Session,
    cohort_id: str,
    overwrite: bool = False
) -> Dict[str, int]:
    """
    Pre-generate empty data records based on protocol schedule.

    This creates placeholder records for:
    - Weights (if expects_weights=True)
    - Pellet scores (if expects_pellets=True)
    - Ramp entries (for ramp phases)

    Having these records allows tracking completeness and shows what data
    is expected vs. what has been entered.

    Args:
        session: Database session
        cohort_id: Cohort ID to generate records for
        overwrite: If True, delete existing records first

    Returns:
        Dictionary with counts of records created:
        {'weights': int, 'pellet_scores': int, 'ramp_entries': int}
    """
    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if not cohort or not cohort.protocol_id:
        raise ValueError(f"Cohort {cohort_id} not found or has no protocol")

    # Get subjects
    subjects = session.query(Subject).filter_by(
        cohort_id=cohort_id,
        is_active=1
    ).all()

    if not subjects:
        return {'weights': 0, 'pellet_scores': 0, 'ramp_entries': 0}

    # Get schedule
    schedule = generate_schedule(session, cohort_id)

    counts = {'weights': 0, 'pellet_scores': 0, 'ramp_entries': 0}

    for phase_info in schedule['phases']:
        phase_name = phase_info['phase_name']
        tray_type = phase_info['tray_type_code']
        sessions_per_day = phase_info['sessions_per_day']
        days = phase_info['days']

        # Get phase details from protocol
        phases = get_effective_phases(session, cohort.protocol_id)
        phase_dict = None
        for p in phases:
            if p['phase_order'] == phase_info['phase_order']:
                phase_dict = p
                break

        if not phase_dict:
            continue

        for subject in subjects:
            for day in days:
                # Generate weight record if expected
                if phase_dict['expects_weights']:
                    counts['weights'] += _create_weight_if_not_exists(
                        session, subject.subject_id, day, overwrite
                    )

                # Generate pellet score records if expected
                if phase_dict['expects_pellets'] and tray_type:
                    counts['pellet_scores'] += _create_pellet_scores_if_not_exists(
                        session, subject.subject_id, day, phase_name,
                        tray_type, sessions_per_day, overwrite
                    )

                # Generate ramp entries for ramp phases
                if phase_dict['phase_type'] == 'ramp' or 'ramp' in phase_name.lower():
                    ramp_day = days.index(day)
                    counts['ramp_entries'] += _create_ramp_entry_if_not_exists(
                        session, subject.subject_id, day, ramp_day, overwrite
                    )

    session.flush()
    return counts


def _create_weight_if_not_exists(
    session: Session,
    subject_id: str,
    record_date: date,
    overwrite: bool
) -> int:
    """Create a weight record if it doesn't exist. Returns 1 if created, 0 otherwise."""
    existing = session.query(Weight).filter_by(
        subject_id=subject_id,
        date=record_date
    ).first()

    if existing:
        if overwrite:
            session.delete(existing)
        else:
            return 0

    # We don't create empty weight records - weights must be entered
    # This function is a placeholder for potential future use
    return 0


def _create_pellet_scores_if_not_exists(
    session: Session,
    subject_id: str,
    record_date: date,
    phase_name: str,
    tray_type: str,
    sessions_per_day: int,
    overwrite: bool
) -> int:
    """Create pellet score records if they don't exist. Returns count created."""
    created = 0

    # Each session is a tray
    for tray_num in range(1, sessions_per_day + 1):
        for pellet_num in range(1, 21):  # 20 pellets per tray
            existing = session.query(PelletScore).filter_by(
                subject_id=subject_id,
                session_date=record_date,
                tray_type=tray_type,
                tray_number=tray_num,
                pellet_number=pellet_num
            ).first()

            if existing:
                if overwrite:
                    session.delete(existing)
                else:
                    continue

            # Note: We don't actually create placeholder pellet scores
            # because they require a score value. Instead, the GUI will
            # know what to expect based on the protocol schedule.
            # This is intentional - we only count what SHOULD exist.
            created += 1

    return 0  # Return 0 because we don't actually create placeholder records


def _create_ramp_entry_if_not_exists(
    session: Session,
    subject_id: str,
    record_date: date,
    ramp_day: int,
    overwrite: bool
) -> int:
    """Create a ramp entry if it doesn't exist. Returns 1 if created, 0 otherwise."""
    existing = session.query(RampEntry).filter_by(
        subject_id=subject_id,
        date=record_date
    ).first()

    if existing:
        if overwrite:
            session.delete(existing)
        else:
            return 0

    # We don't create empty ramp entries - they must be entered with actual data
    return 0


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def get_cohort_current_phase(session: Session, cohort_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the current phase for a cohort based on today's date.

    Args:
        session: Database session
        cohort_id: Cohort ID

    Returns:
        Phase dictionary if cohort is in a phase today, None otherwise
    """
    from datetime import date as date_type
    today = date_type.today()

    try:
        schedule = generate_schedule(session, cohort_id)
    except ValueError:
        return None

    for phase in schedule['phases']:
        if phase['start_date'] <= today <= phase['end_date']:
            return phase

    return None


def get_expected_data_for_date(
    session: Session,
    cohort_id: str,
    check_date: date
) -> Dict[str, Any]:
    """
    Get what data is expected for a specific date.

    Args:
        session: Database session
        cohort_id: Cohort ID
        check_date: Date to check

    Returns:
        Dictionary with expected data:
        {
            'phase_name': str or None,
            'expects_weights': bool,
            'expects_pellets': bool,
            'tray_type': str or None,
            'sessions_per_day': int,
            'is_testing_day': bool
        }
    """
    result = {
        'phase_name': None,
        'expects_weights': False,
        'expects_pellets': False,
        'tray_type': None,
        'sessions_per_day': 0,
        'is_testing_day': False
    }

    try:
        schedule = generate_schedule(session, cohort_id)
    except ValueError:
        return result

    for phase in schedule['phases']:
        if check_date in phase['days']:
            # Get phase details
            phases = get_effective_phases(session, schedule['protocol_id'])
            for p in phases:
                if p['phase_order'] == phase['phase_order']:
                    result['phase_name'] = phase['phase_name']
                    result['expects_weights'] = p['expects_weights']
                    result['expects_pellets'] = p['expects_pellets']
                    result['tray_type'] = phase['tray_type_code']
                    result['sessions_per_day'] = phase['sessions_per_day']
                    result['is_testing_day'] = True
                    break
            break

    return result


def get_protocol_summary(session: Session, protocol_id: int) -> Dict[str, Any]:
    """
    Get a summary of a protocol for display.

    Args:
        session: Database session
        protocol_id: Protocol ID

    Returns:
        Dictionary with protocol summary
    """
    protocol = get_protocol(session, protocol_id)
    if not protocol:
        return {}

    phases = get_effective_phases(session, protocol_id)

    total_days = sum(p['duration_days'] for p in phases)
    phase_types = set(p['phase_type'] for p in phases if p['phase_type'])
    tray_types = set(p['tray_type_code'] for p in phases if p['tray_type_code'])

    return {
        'id': protocol.id,
        'name': protocol.name,
        'version': protocol.version,
        'description': protocol.description,
        'is_variant': protocol.parent_protocol_id is not None,
        'parent_protocol_id': protocol.parent_protocol_id,
        'total_days': total_days,
        'num_phases': len(phases),
        'phase_types': list(phase_types),
        'tray_types': list(tray_types),
        'phases': phases
    }


# =============================================================================
# TIMELINE-BASED SCHEDULE INFERENCE (for cohorts without formal protocols)
# =============================================================================

def generate_schedule_from_timeline(
    session: Session, cohort_id: str, validate_with_data: bool = True
) -> Dict[str, Any]:
    """
    Generate a schedule for a cohort using the TIMELINE constant from schema.py.

    This works for old cohorts that don't have a formal protocol assigned.
    It uses start_date + TIMELINE day offsets to compute phase dates, and
    optionally validates against actual pellet_score/surgery data in the DB.

    Args:
        session: Database session
        cohort_id: Cohort ID
        validate_with_data: If True, check actual DB records to confirm phases

    Returns:
        Schedule dict compatible with generate_schedule() output
    """
    from .schema import TIMELINE, INJURY_DAY, TRACING_DAY, PERFUSION_DAY

    cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
    if not cohort or not cohort.start_date:
        return {}

    start = cohort.start_date

    # Group TIMELINE entries into contiguous phase blocks
    # e.g., Training_Flat covers days 4-6, Training_Pillar covers days 7-13, etc.
    phase_blocks = []
    current_block = None

    for day_offset, phase_name, tray_type, trays in TIMELINE:
        # Derive block name from phase (strip trailing _N number)
        parts = phase_name.rsplit('_', 1)
        if parts[-1].isdigit():
            block_name = parts[0]
        else:
            block_name = phase_name

        phase_date = start + timedelta(days=day_offset)

        if current_block and current_block['block_name'] == block_name:
            # Extend current block
            current_block['days'].append(phase_date)
            current_block['end_date'] = phase_date
            current_block['phase_names'].append(phase_name)
        else:
            # Start new block
            if current_block:
                phase_blocks.append(current_block)
            current_block = {
                'block_name': block_name,
                'tray_type_code': tray_type,
                'sessions_per_day': trays,
                'start_date': phase_date,
                'end_date': phase_date,
                'days': [phase_date],
                'phase_names': [phase_name],
            }

    if current_block:
        phase_blocks.append(current_block)

    # Add special days (surgery, tracing, perfusion)
    special_phases = [
        ('Ramp', None, start, start + timedelta(days=3)),
        ('Surgery', None, start + timedelta(days=INJURY_DAY),
         start + timedelta(days=INJURY_DAY)),
        ('Recovery', None, start + timedelta(days=INJURY_DAY + 1),
         start + timedelta(days=24)),
    ]

    if TRACING_DAY:
        special_phases.append(
            ('Tracing', None, start + timedelta(days=TRACING_DAY),
             start + timedelta(days=TRACING_DAY))
        )
    if PERFUSION_DAY:
        special_phases.append(
            ('Perfusion', None, start + timedelta(days=PERFUSION_DAY),
             start + timedelta(days=PERFUSION_DAY))
        )

    # Build full phase list sorted by start_date
    all_phases = []

    for sp_name, sp_tray, sp_start, sp_end in special_phases:
        all_phases.append({
            'phase_name': sp_name,
            'tray_type_code': sp_tray,
            'sessions_per_day': 0,
            'start_date': sp_start,
            'end_date': sp_end,
            'days': [sp_start + timedelta(days=d)
                     for d in range((sp_end - sp_start).days + 1)],
        })

    for block in phase_blocks:
        all_phases.append({
            'phase_name': block['block_name'],
            'tray_type_code': block['tray_type_code'],
            'sessions_per_day': block['sessions_per_day'],
            'start_date': block['start_date'],
            'end_date': block['end_date'],
            'days': block['days'],
        })

    all_phases.sort(key=lambda p: p['start_date'])

    # Add phase_order
    for i, phase in enumerate(all_phases):
        phase['phase_order'] = i + 1
        phase['stagger_groups'] = None

    # Optionally validate against actual data
    if validate_with_data:
        actual_phases = set()
        pellets = session.query(PelletScore.test_phase).filter(
            PelletScore.subject_id.like(f'{cohort_id}_%')
        ).distinct().all()
        for (tp,) in pellets:
            if tp:
                actual_phases.add(tp)

        # Mark phases that have actual data
        for phase in all_phases:
            phase_names = [phase['phase_name']]
            # Check if any of the individual day-phases have data
            for block in phase_blocks:
                if block['block_name'] == phase['phase_name']:
                    phase_names.extend(block['phase_names'])
            phase['has_data'] = bool(actual_phases & set(phase_names))

    schedule = {
        'cohort_id': cohort_id,
        'protocol_id': None,
        'protocol_name': 'Inferred from Timeline',
        'start_date': start,
        'phases': all_phases,
        'end_date': start + timedelta(days=max(PERFUSION_DAY or 0, 84)),
    }

    return schedule
