"""
Structured figure legend system for Connectome figures.

Every data figure must tell a complete story (Rule 25). This module
provides a structured legend builder with 7 required components:
question, method, finding, analysis, effect_sizes, confounds, follow_up.

A FigureLegend is NOT a color key -- it's a narrative text block
explaining the figure's purpose and interpretation.
"""

from dataclasses import dataclass, field
from typing import Optional

from .standards import get_theme


@dataclass
class FigureLegend:
    """Structured figure legend with 7 narrative components.

    Every field contributes to making the figure self-contained
    and interpretable without external context.

    Parameters
    ----------
    question : str
        What question does this figure answer?
        E.g., "Does pellet retrieval recover after CST injury?"
    method : str
        What was done? Inclusion criteria, filtering, tray types, phases.
        E.g., "N=11 learners (>=5% pre-injury), pillar tray sessions only"
    finding : str
        What does the data show? Main effect in plain language.
        E.g., "Retrieval drops to ~5% post-injury, recovers to ~25% after rehab"
    analysis : str
        How was significance determined? Test name, correction method.
        E.g., "Wilcoxon signed-rank (paired), Holm correction for 4 comparisons"
    effect_sizes : str
        Cohen's d (or equivalent) for every significant finding.
        E.g., "Pre vs Post-Injury: d=1.82; Post-Injury vs Rehab: d=0.94"
    confounds : str
        What alternative explanations exist?
        E.g., "Tray familiarity, hand preference not controlled"
    follow_up : str
        What questions does this raise?
        E.g., "Does kinematic quality also recover, or just success rate?"
    """
    question: str = ""
    method: str = ""
    finding: str = ""
    analysis: str = ""
    effect_sizes: str = ""
    confounds: str = ""
    follow_up: str = ""

    def format_text(self) -> str:
        """Format the legend as a multi-line text block.

        Uses KEY-VALUE format matching the methodology panel style.
        Only includes non-empty fields.
        """
        lines = []
        field_map = [
            ("QUESTION", self.question),
            ("METHOD", self.method),
            ("FINDING", self.finding),
            ("ANALYSIS", self.analysis),
            ("EFFECT SIZES", self.effect_sizes),
            ("CONFOUNDS", self.confounds),
            ("FOLLOW-UP", self.follow_up),
        ]

        max_key_len = max(len(k) for k, _ in field_map)
        for key, value in field_map:
            if value:
                lines.append(f"{key:<{max_key_len}}  {value}")

        return "\n".join(lines)

    def is_complete(self) -> bool:
        """Check if all 7 fields are populated."""
        return all([
            self.question, self.method, self.finding,
            self.analysis, self.effect_sizes, self.confounds,
            self.follow_up,
        ])

    def missing_fields(self) -> list:
        """Return names of empty fields."""
        missing = []
        for name in ["question", "method", "finding", "analysis",
                      "effect_sizes", "confounds", "follow_up"]:
            if not getattr(self, name):
                missing.append(name)
        return missing


def add_figure_legend(ax, legend: FigureLegend, theme=None, fontsize=7):
    """Render a structured figure legend as a text block on an axes.

    Parameters
    ----------
    ax : matplotlib Axes
        An invisible axes dedicated to the legend (call ax.axis("off") first).
    legend : FigureLegend
        The structured legend to render.
    theme : str, optional
        "light", "dark", or "print". If None, uses active theme.
    fontsize : float
        Font size for the legend text.
    """
    t = get_theme(theme)

    text = legend.format_text()
    if not text:
        return

    ax.text(
        0.02, 0.95, text,
        transform=ax.transAxes,
        fontsize=fontsize,
        fontfamily="monospace",
        color=t.get("methodology_text", "#333333"),
        verticalalignment="top",
        bbox=dict(
            boxstyle="round,pad=0.5",
            facecolor=t.get("methodology_bg", "#F8F8F8"),
            edgecolor=t.get("methodology_border", "#CCCCCC"),
            alpha=0.95,
        ),
    )

    # Warn about incomplete legends
    missing = legend.missing_fields()
    if missing:
        print(
            f"  [!] Figure legend incomplete -- missing: {', '.join(missing)}",
            flush=True,
        )
