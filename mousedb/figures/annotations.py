"""
Reusable annotation functions for Connectome figures.

Provides methodology panels, significance brackets, provenance footers,
and phase definition formatters that all figure scripts should use.
"""

from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np


# =============================================================================
# Methodology panel
# =============================================================================

def add_methodology_panel(ax_or_fig, text, position="bottom", fontsize=7,
                          theme=None):
    """Add a monospace methodology text box to a figure.

    Parameters
    ----------
    ax_or_fig : matplotlib Axes or Figure
        If Axes, places text in the axes (should be an invisible axes).
        If Figure, places text at the bottom of the figure.
    text : str
        Methodology text. Use KEY-VALUE format, e.g.:
            "EXPERIMENT  Skilled reaching task\\n"
            "SUBJECTS    N=11 learners from CNT_01-04\\n"
            "METRIC      % pellets eaten per subject per phase"
    position : str
        "bottom" (default). Reserved for future positions.
    fontsize : float
        Font size for the text. Default 7 (fits ~100 chars wide).
    theme : str, optional
        "light", "dark", or "print". If None, uses the active theme.
    """
    from .standards import get_theme
    t = get_theme(theme)

    style = dict(
        fontsize=fontsize,
        fontfamily="monospace",
        color=t["methodology_text"],
        verticalalignment="top",
        bbox=dict(
            boxstyle="round,pad=0.5",
            facecolor=t["methodology_bg"],
            edgecolor=t["methodology_border"],
            alpha=0.95,
        ),
    )

    if hasattr(ax_or_fig, "transAxes"):
        # It's an Axes -- place inside
        ax_or_fig.text(0.02, 0.95, text, transform=ax_or_fig.transAxes, **style)
    else:
        # It's a Figure -- place at bottom
        ax_or_fig.text(0.02, 0.01, text, **style)


# =============================================================================
# Significance brackets
# =============================================================================

def add_stat_bracket(ax, x1, x2, y, p, h=0.6, color="black", fontsize=11):
    """Draw a significance bracket between two x positions.

    Parameters
    ----------
    ax : matplotlib Axes
    x1, x2 : float
        X positions of the two groups being compared.
    y : float
        Y position of the bracket base.
    p : float
        P-value. Determines the label (* / ** / *** / n.s.).
    h : float
        Height of the bracket arms.
    color : str
        Bracket color.
    fontsize : float
        Size of the significance label.
    """
    if p < 0.001:
        label = "***"
    elif p < 0.01:
        label = "**"
    elif p < 0.05:
        label = "*"
    else:
        label = "n.s."

    ax.plot(
        [x1, x1, x2, x2], [y, y + h, y + h, y],
        color=color, linewidth=1.2, alpha=0.7,
    )
    ax.text(
        (x1 + x2) / 2, y + h + 0.15, label,
        ha="center", va="bottom", color=color,
        fontsize=fontsize, fontweight="bold",
    )


def add_stat_brackets(ax, comparisons, p_values, tp_to_x, start_y, step=4.0):
    """Draw multiple significance brackets stacked vertically.

    Parameters
    ----------
    ax : matplotlib Axes
    comparisons : list of (group_a, group_b, alternative)
        The comparisons that were tested.
    p_values : dict of {(group_a, group_b): p_value}
        Results from statistical tests.
    tp_to_x : dict of {group_name: x_position}
        Mapping from group names to x-axis positions.
    start_y : float
        Y position for the first bracket.
    step : float
        Vertical spacing between brackets.
    """
    for idx, (a, b, _alt) in enumerate(comparisons):
        if (a, b) in p_values:
            y_pos = start_y + idx * step
            add_stat_bracket(ax, tp_to_x[a], tp_to_x[b], y_pos, p_values[(a, b)])


# =============================================================================
# Provenance footer
# =============================================================================

def add_provenance_footer(fig, script_name, data_sources, timestamp=None):
    """Add a provenance line to the bottom of a figure.

    Parameters
    ----------
    fig : matplotlib Figure
    script_name : str
        Name of the script that generated this figure.
    data_sources : list of str
        Data files used (e.g., ["pellet_scores.csv", "reach_data.csv"]).
    timestamp : str, optional
        Override timestamp. Defaults to current time.
    """
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    sources = ", ".join(data_sources) if data_sources else "unknown"
    text = f"DATA  {sources} | Generated {ts} | Script: {script_name}"
    return text


# =============================================================================
# Phase definition formatter
# =============================================================================

def format_phase_definitions(phases_dict):
    """Format phase definitions for inclusion in a methodology panel.

    Parameters
    ----------
    phases_dict : dict of {phase_name: definition_string}
        e.g., {"Last 3": "Last 3 pre-injury pillar test sessions"}

    Returns
    -------
    str : Formatted multi-line string.
    """
    max_key_len = max(len(k) for k in phases_dict) if phases_dict else 0
    lines = []
    for name, definition in phases_dict.items():
        lines.append(f"  {name:<{max_key_len}}  {definition}")
    return "\n".join(lines)
