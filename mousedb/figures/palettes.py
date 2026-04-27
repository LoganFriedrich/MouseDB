"""
Canonical color palettes for the Connectome project.

Every figure script imports colors from here. Do NOT define colors inline
in figure scripts -- if a color is missing, add it here.

Palette sources:
    - Cohort colors: lab convention (red/blue/green/purple for CNT_01-04)
    - Phase colors: Wong 2011 colorblind-safe palette
    - Outcome colors: green/orange/red traffic-light convention
    - Domain colors: project-specific (brain/reach/cam/db/slice)
"""

import numpy as np

# =============================================================================
# Cohort colors (lab standard, used in fig5a-style connected dot plots)
# =============================================================================

COHORT_COLORS = {
    "CNT_01": "#e74c3c",  # Red
    "CNT_02": "#3498db",  # Blue
    "CNT_03": "#2ecc71",  # Green
    "CNT_04": "#9b59b6",  # Purple
}

# Lighter versions for background/individual traces
COHORT_COLORS_LIGHT = {
    "CNT_01": "#f5b7b1",
    "CNT_02": "#aed6f1",
    "CNT_03": "#abebc6",
    "CNT_04": "#d2b4de",
}


# =============================================================================
# Phase colors (colorblind-safe, Wong 2011)
# Used for trajectory plots and time-series overlays
# =============================================================================

PHASE_COLORS = {
    "Pre-Injury": "#0072B2",   # Blue
    "Post-Injury": "#D55E00",  # Vermillion/orange
    "Post-Rehab": "#009E73",   # Bluish green
}

PHASE_COLORS_LIGHT = {
    "Pre-Injury": "#a8cfe0",
    "Post-Injury": "#f0b89a",
    "Post-Rehab": "#8fd4bc",
}

# Extended phase palette (for all experimental phases including training)
PHASE_COLORS_EXTENDED = {
    "Ramp": "#95a5a6",
    "Flat": "#bdc3c7",
    "Easy": "#82e0aa",
    "Training": "#95a5a6",
    "Pre-Injury": "#0072B2",
    "Post-Injury": "#D55E00",
    "Rehab": "#009E73",
    "Post-Rehab": "#009E73",
}


# =============================================================================
# Pellet score phase colors (4-group bar/dot charts)
# =============================================================================

PELLET_PHASE_COLORS = {
    "Last 3": "#0072B2",
    "Post Injury 1": "#D55E00",
    "Post Injury 2-4": "#E69F00",
    "Rehab Pillar": "#009E73",
}


# =============================================================================
# Outcome colors (traffic-light convention)
# =============================================================================

OUTCOME_COLORS = {
    "retrieved": "#27AE60",        # Green
    "displaced": "#F39C12",        # Orange (generic)
    "displaced_sa": "#F39C12",     # Orange (same area)
    "displaced_outside": "#E67E22",# Darker orange
    "miss": "#E74C3C",             # Red
    "untouched": "#95A5A6",        # Gray
    "uncertain_outside": "#BDC3C7",# Light gray
}


# =============================================================================
# Domain colors (project-level, for cross-domain figures)
# =============================================================================

DOMAIN_COLORS = {
    "brain": "#2980B9",   # Blue (3D tissue)
    "reach": "#E67E22",   # Orange (behavior)
    "cam": "#8E44AD",     # Purple (video)
    "db": "#27AE60",      # Green (database)
    "slice": "#E74C3C",   # Red (2D tissue)
}


# =============================================================================
# Kinematic category colors (for recovery index and phase comparison figures)
# =============================================================================

KINEMATIC_CATEGORY_COLORS = {
    "distance": "#3498DB",   # Blue
    "velocity": "#E74C3C",   # Red
    "timing": "#F39C12",     # Amber
    "quality": "#2ECC71",    # Green
    "posture": "#9B59B6",    # Purple
}

# 4-timepoint kinematic phase palette (explicit date-based phases for CNT_01)
KINEMATIC_PHASE_COLORS = {
    "Pre-Injury": "#2ECC71",       # Green
    "Post-Injury_1": "#E74C3C",    # Red
    "Post-Injury_2-4": "#E67E22",  # Orange
    "Rehab_Pillar": "#3498DB",     # Blue
}

KINEMATIC_PHASE_LABELS = {
    "Pre-Injury": "Pre-Injury",
    "Post-Injury_1": "Post-Injury\n1 Wk",
    "Post-Injury_2-4": "Post-Injury\n2-4 Wk",
    "Rehab_Pillar": "Post-Rehab\n(Last 3)",
}


# =============================================================================
# Pipeline stage colors (for cell count pipeline comparison figures)
# =============================================================================

PIPELINE_STAGE_COLORS = {
    "Raw Detection": "#95A5A6",      # Gray - everything cellfinder finds
    "After Prefilter": "#F39C12",    # Orange - surface artifacts removed
    "Full Classification": "#E74C3C",  # Red - ML classifier applied to ALL candidates
    "After Classification": "#3498DB",  # Blue - (legacy alias)
    "Region-Assigned": "#2ECC71",    # Green - cells mapped to atlas regions
    "Selective": "#8E44AD",          # Purple - selective classification (trust interior, classify surface)
}


# =============================================================================
# Brain sample colors (for multi-brain comparison plots)
# =============================================================================

BRAIN_COLORS = ["#5DADE2", "#58D68D", "#EC7063", "#F4D03F"]

# Subject colors for ENCR analysis (per animal)
ENCR_SUBJECT_COLORS = {
    "E02_01": "#4C72B0",
    "E02_02": "#DD8452",
    "E02_03": "#55A868",
}


# =============================================================================
# Utility functions
# =============================================================================

def get_subject_colors(subject_ids, by_cohort=True):
    """Assign consistent colors to subjects.

    Parameters
    ----------
    subject_ids : list of str
        Subject IDs (e.g., ["CNT_01_03", "CNT_02_05"]).
    by_cohort : bool
        If True, color by cohort (all CNT_01 subjects share red).
        If False, assign unique colors per subject from tab20.

    Returns
    -------
    dict : {subject_id: hex_color}
    """
    if by_cohort:
        colors = {}
        for sid in subject_ids:
            # Extract cohort from subject_id (e.g., "CNT_01_03" -> "CNT_01")
            parts = sid.rsplit("_", 1)
            cohort = parts[0] if len(parts) == 2 else sid
            colors[sid] = COHORT_COLORS.get(cohort, "#888888")
        return colors
    else:
        import matplotlib.pyplot as plt
        cmap = plt.cm.get_cmap("tab20", max(len(subject_ids), 20))
        return {
            sid: "#{:02x}{:02x}{:02x}".format(
                int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)
            )
            for sid, c in zip(sorted(subject_ids), [cmap(i) for i in range(len(subject_ids))])
        }


# =============================================================================
# Tray type definitions and filtering
# =============================================================================

TRAY_TYPES = {
    "P": {"name": "Pillar", "valid_for": ["performance", "kinematics"], "color": "#2980B9"},
    "F": {"name": "Flat", "valid_for": ["engagement"], "color": "#95A5A6"},
    "E": {"name": "Easy", "valid_for": ["engagement"], "color": "#BDC3C7"},
    "R": {"name": "Ramp", "valid_for": ["training"], "color": "#F39C12"},
    "T": {"name": "Training", "valid_for": ["training"], "color": "#82E0AA"},
}


def tray_name(code):
    """Get full tray name from single-letter code.

    Parameters
    ----------
    code : str
        Single-letter tray code (e.g., "P", "F", "E").

    Returns
    -------
    str : Full name (e.g., "Pillar") or the code itself if unknown.
    """
    info = TRAY_TYPES.get(code)
    return info["name"] if info else code


def validate_tray_filter(df, analysis_type="performance", tray_col="tray_type"):
    """Filter DataFrame to valid tray types for the analysis.

    Parameters
    ----------
    df : DataFrame
        Must contain a tray_type column.
    analysis_type : str
        "performance" (pillar only), "kinematics" (pillar only),
        "engagement" (flat/easy), or "all" (no filter).
    tray_col : str
        Column name containing tray type codes.

    Returns
    -------
    tuple of (DataFrame, str)
        filtered_df : DataFrame with only valid tray types.
        description : Filter description for methodology panel.

    Raises
    ------
    ValueError
        If no valid data remains after filtering.
    """
    if analysis_type == "all":
        return df.copy(), "All tray types included (no filter)"

    if tray_col not in df.columns:
        print(
            f"  [!] Tray column '{tray_col}' not found -- skipping tray filter",
            flush=True,
        )
        return df.copy(), "Tray type column not available"

    valid_codes = [
        code for code, info in TRAY_TYPES.items()
        if analysis_type in info["valid_for"]
    ]

    if not valid_codes:
        raise ValueError(
            f"No tray types are valid for analysis_type='{analysis_type}'. "
            f"Valid analysis types: performance, kinematics, engagement, all"
        )

    before = len(df)
    filtered = df[df[tray_col].isin(valid_codes)].copy()

    valid_names = [TRAY_TYPES[c]["name"] for c in valid_codes]
    description = (
        f"Filtered to {', '.join(valid_names)} tray(s) only "
        f"({len(filtered)}/{before} sessions retained)"
    )

    if len(filtered) == 0:
        raise ValueError(
            f"No data remains after tray filter (analysis_type='{analysis_type}', "
            f"valid codes={valid_codes}). Check tray_type column values."
        )

    print(f"  Tray filter ({analysis_type}): {description}", flush=True)
    return filtered, description


# =============================================================================
# Subject label formatting
# =============================================================================

def get_subject_label(subject_id):
    """Format subject ID with cohort context for figure labels.

    Parameters
    ----------
    subject_id : str
        Full subject ID (e.g., "CNT_01_03").

    Returns
    -------
    str : Formatted label (e.g., "03 (CNT_01)").
    """
    parts = subject_id.rsplit("_", 1)
    if len(parts) == 2:
        cohort, num = parts
        return f"{num} ({cohort})"
    return subject_id


# =============================================================================
# Persistent subject colors (consistent across all figures in a session)
# =============================================================================

_SUBJECT_COLOR_CACHE = {}


def get_persistent_subject_colors(subject_ids):
    """Assign colors that are consistent across ALL figures in a session.

    Once a subject gets a color, it keeps that color for the entire
    Python session. New subjects get the next available color from tab20.

    Parameters
    ----------
    subject_ids : list of str
        Subject IDs to assign colors to.

    Returns
    -------
    dict : {subject_id: hex_color}
    """
    import matplotlib.pyplot as plt

    # Assign colors to any new subjects
    cmap = plt.cm.get_cmap("tab20", 20)
    next_idx = len(_SUBJECT_COLOR_CACHE)

    for sid in sorted(subject_ids):
        if sid not in _SUBJECT_COLOR_CACHE:
            c = cmap(next_idx % 20)
            _SUBJECT_COLOR_CACHE[sid] = "#{:02x}{:02x}{:02x}".format(
                int(c[0] * 255), int(c[1] * 255), int(c[2] * 255)
            )
            next_idx += 1

    return {sid: _SUBJECT_COLOR_CACHE[sid] for sid in subject_ids}


def reset_subject_colors():
    """Clear the persistent color cache. Call between independent analyses."""
    _SUBJECT_COLOR_CACHE.clear()
