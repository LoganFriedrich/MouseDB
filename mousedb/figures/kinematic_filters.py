"""
Kinematic data filtering for Connectome figures.

Pre-processing filters that MUST be applied before any kinematic
visualization. Removes detection artifacts, physically impossible
values, and non-reach events.

Rules enforced:
    30 - Kinematic data must be filtered for plausible ranges
    31 - Filter by outcome and relevance
"""

import pandas as pd


# =============================================================================
# Physiologically plausible ranges (lab-calibrated)
# =============================================================================

KINEMATIC_BOUNDS = {
    "max_extent_mm": (0.5, 50.0),
    "max_extent_px": (1, 800),
    "duration_frames": (3, 500),
    "peak_velocity_px_frame": (0.1, 100.0),
    "path_length_px": (1, 5000),
    "reaction_time_frames": (1, 300),
}

# Descriptions for methodology panel
_BOUND_DESCRIPTIONS = {
    "max_extent_mm": "max reach extent 0.5-50mm (excludes non-reaches and artifacts)",
    "max_extent_px": "max reach extent 1-800px",
    "duration_frames": "reach duration 3-500 frames (excludes 1-2 frame glitches and >17s events at 30fps)",
    "peak_velocity_px_frame": "peak velocity 0.1-100 px/frame",
    "path_length_px": "path length 1-5000px",
    "reaction_time_frames": "reaction time 1-300 frames",
}


# =============================================================================
# Main filtering functions
# =============================================================================

def filter_plausible_reaches(df, bounds=None, columns=None):
    """Filter reaches to physiologically plausible ranges.

    Parameters
    ----------
    df : DataFrame
        Reach-level data with kinematic feature columns.
    bounds : dict, optional
        Override bounds. Keys are column names, values are (min, max) tuples.
        Defaults to KINEMATIC_BOUNDS.
    columns : list of str, optional
        Only filter on these columns (must be subset of bounds keys).
        If None, filters on all bounds keys that exist in df.

    Returns
    -------
    tuple of (DataFrame, dict)
        filtered_df : DataFrame with implausible reaches removed.
        report : dict with per-feature exclusion counts and total.
    """
    if bounds is None:
        bounds = KINEMATIC_BOUNDS

    if columns is not None:
        bounds = {k: v for k, v in bounds.items() if k in columns}

    original_count = len(df)
    mask = pd.Series(True, index=df.index)
    feature_exclusions = {}

    for col, (lo, hi) in bounds.items():
        if col not in df.columns:
            continue

        col_data = pd.to_numeric(df[col], errors="coerce")
        bad = (col_data < lo) | (col_data > hi) | col_data.isna()
        n_bad = bad.sum()

        if n_bad > 0:
            feature_exclusions[col] = {
                "excluded": int(n_bad),
                "below_min": int((col_data < lo).sum()),
                "above_max": int((col_data > hi).sum()),
                "nan": int(col_data.isna().sum()),
                "bounds": (lo, hi),
            }

        mask = mask & ~bad

    filtered = df[mask].copy()
    total_excluded = original_count - len(filtered)

    report = {
        "original_count": original_count,
        "filtered_count": len(filtered),
        "total_excluded": total_excluded,
        "exclusion_rate": total_excluded / original_count if original_count > 0 else 0,
        "per_feature": feature_exclusions,
    }

    print(
        f"  Kinematic filter: {len(filtered)}/{original_count} reaches retained "
        f"({total_excluded} excluded, {report['exclusion_rate']:.1%})",
        flush=True,
    )

    return filtered, report


def filter_by_outcome(df, outcomes=("retrieved",), outcome_col="outcome"):
    """Filter reaches to specific outcomes.

    Parameters
    ----------
    df : DataFrame
        Reach-level data.
    outcomes : tuple of str
        Outcome values to keep. Common values:
        "retrieved", "displaced", "displaced_sa", "displaced_outside",
        "miss", "untouched".
    outcome_col : str
        Column name containing outcome labels.

    Returns
    -------
    DataFrame : Filtered to specified outcomes.
    """
    if outcome_col not in df.columns:
        print(
            f"  [!] Outcome column '{outcome_col}' not found -- skipping outcome filter",
            flush=True,
        )
        return df

    before = len(df)
    filtered = df[df[outcome_col].isin(outcomes)].copy()
    print(
        f"  Outcome filter ({', '.join(outcomes)}): "
        f"{len(filtered)}/{before} reaches retained",
        flush=True,
    )
    return filtered


# =============================================================================
# Reporting
# =============================================================================

def exclusion_report_text(report):
    """Format kinematic exclusion report for methodology panel.

    Parameters
    ----------
    report : dict
        Output from filter_plausible_reaches().

    Returns
    -------
    str : Multi-line text suitable for methodology panel.
    """
    lines = [
        f"FILTERING   {report['filtered_count']}/{report['original_count']} "
        f"reaches retained ({report['total_excluded']} excluded, "
        f"{report['exclusion_rate']:.1%})",
    ]

    for col, info in report.get("per_feature", {}).items():
        lo, hi = info["bounds"]
        desc = _BOUND_DESCRIPTIONS.get(col, f"{col} in [{lo}, {hi}]")
        lines.append(
            f"  {col}: {info['excluded']} excluded "
            f"({info['below_min']} below min, {info['above_max']} above max) -- {desc}"
        )

    return "\n".join(lines)
