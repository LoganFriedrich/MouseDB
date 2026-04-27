"""
Axis setup utilities for Connectome figures.

Provides standardized axis configurations for common experimental
designs: phase-based, days-post-injury, and session-number axes.

Rules enforced:
    13 - X-axis: experimental timepoints, not calendar dates
    23 - Days-based x-axis must handle gaps
    35 - Tick alignment must be unambiguous
"""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


def setup_phase_axis(ax, phases, phase_colors=None, fontsize=11,
                     fontweight="bold"):
    """Set up x-axis with experimental phase labels.

    Places phase labels as tick marks at integer positions 0, 1, 2, ...
    Optionally adds colored background spans for each phase.

    Parameters
    ----------
    ax : matplotlib Axes
    phases : list of str
        Phase names in order (e.g., ["Pre-Injury", "Post-Injury", "Rehab"]).
    phase_colors : dict, optional
        Mapping of phase name -> color for background shading.
        If provided, adds light-colored vertical spans behind each phase.
    fontsize : float
        Font size for phase labels.
    fontweight : str
        Font weight for phase labels.
    """
    x_positions = np.arange(len(phases))
    ax.set_xticks(x_positions)
    ax.set_xticklabels(phases, fontsize=fontsize, fontweight=fontweight)

    # Add phase shading if colors provided
    if phase_colors:
        for i, phase in enumerate(phases):
            color = phase_colors.get(phase)
            if color:
                ax.axvspan(
                    i - 0.4, i + 0.4,
                    alpha=0.08, color=color, zorder=0,
                )

    # Ensure data points align exactly on ticks (Rule 35)
    ax.set_xlim(-0.5, len(phases) - 0.5)

    return x_positions


def setup_dpi_axis(ax, day_values, gap_threshold=3, mark_injury=True,
                   label="Days Post-Injury"):
    """Set up DPI (days post-injury) axis with gap handling.

    Days with no data produce gaps in the axis rather than
    zero-value points (Rule 23).

    Parameters
    ----------
    ax : matplotlib Axes
    day_values : array-like
        Sorted unique day values that have data.
    gap_threshold : int
        Number of consecutive missing days before marking a gap.
    mark_injury : bool
        If True, draws a vertical line at day 0 labeled "Injury".
    label : str
        X-axis label.

    Returns
    -------
    dict : Mapping of {day_value: x_position} for plotting data.
    """
    days = sorted(set(day_values))
    if not days:
        return {}

    # Build sequential x positions, inserting gaps for missing stretches
    x_positions = {}
    x = 0
    gap_markers = []

    for i, day in enumerate(days):
        if i > 0:
            gap = day - days[i - 1]
            if gap > gap_threshold:
                # Insert a visual gap
                gap_markers.append((x + 0.5, days[i - 1], day))
                x += 1.5  # gap width
            else:
                x += 1
        x_positions[day] = x

    # Set up ticks at data positions only
    tick_positions = list(x_positions.values())
    tick_labels = [str(d) for d in x_positions.keys()]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=9)
    ax.set_xlabel(label, fontsize=11, fontweight="bold")

    # Draw gap markers
    for gap_x, day_before, day_after in gap_markers:
        ax.axvline(gap_x, color="#CCCCCC", linestyle=":", linewidth=1, alpha=0.5)
        ax.text(
            gap_x, ax.get_ylim()[1] * 0.98,
            f"({day_after - day_before - 1}d gap)",
            ha="center", va="top", fontsize=7, color="#999999",
        )

    # Injury marker at day 0
    if mark_injury and 0 in x_positions:
        ax.axvline(
            x_positions[0], color="#D55E00", linestyle="--",
            linewidth=1.5, alpha=0.7, zorder=1,
        )
        ax.text(
            x_positions[0], ax.get_ylim()[1] * 1.02,
            "Injury", ha="center", va="bottom",
            fontsize=9, fontweight="bold", color="#D55E00",
        )

    return x_positions


def setup_session_axis(ax, session_numbers, label="Session Number"):
    """Set up session-number axis (sequential, no gaps).

    Uses integer session numbers so every tick has exactly one
    data point (Rule 35).

    Parameters
    ----------
    ax : matplotlib Axes
    session_numbers : array-like
        Sequential session numbers (1, 2, 3, ...).
    label : str
        X-axis label.

    Returns
    -------
    list : The session numbers as x positions.
    """
    sessions = sorted(set(session_numbers))
    ax.set_xticks(sessions)
    ax.set_xticklabels([str(s) for s in sessions], fontsize=9)
    ax.set_xlabel(label, fontsize=11, fontweight="bold")

    if sessions:
        ax.set_xlim(min(sessions) - 0.5, max(sessions) + 0.5)

    return sessions


def add_phase_spans(ax, phase_boundaries, phase_colors=None, alpha=0.06):
    """Add colored vertical spans for experimental phases on a continuous axis.

    Parameters
    ----------
    ax : matplotlib Axes
    phase_boundaries : dict
        Mapping of phase name -> (x_start, x_end).
        E.g., {"Pre-Injury": (-10, -1), "Post-Injury": (0, 4), "Rehab": (5, 30)}
    phase_colors : dict, optional
        Mapping of phase name -> color.
    alpha : float
        Transparency of the spans.
    """
    if phase_colors is None:
        from .palettes import PHASE_COLORS
        phase_colors = PHASE_COLORS

    for phase, (x_start, x_end) in phase_boundaries.items():
        color = phase_colors.get(phase, "#888888")
        ax.axvspan(x_start, x_end, alpha=alpha, color=color, zorder=0)
        mid_x = (x_start + x_end) / 2
        ax.text(
            mid_x, ax.get_ylim()[1] * 1.01,
            phase, ha="center", va="bottom",
            fontsize=8, fontweight="bold", color=color, alpha=0.8,
        )
