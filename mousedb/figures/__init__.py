"""
mousedb.figures - Centralized figure generation standards for the Connectome project.

This module is the single source of truth for how figures are made across all
Connectome tools (MouseBrain, MouseReach, mousedb). Every figure generation
script should import from here rather than defining its own colors, styles,
or annotation helpers.

Core components:
    - standards: THE rules (DPI, fonts, required elements, validation)
    - palettes: All color palettes (cohort, phase, outcome, domain)
    - annotations: Methodology panels, stat brackets, provenance footers
    - protocol: FigureProtocol class that enforces standards at save time
    - export: Save figures with provenance metadata and JSON sidecars

Quick start:
    from mousedb.figures import FigureProtocol

    fp = FigureProtocol(
        title="Pellet Retrieval Recovery",
        script_name="make_presentation_figures.py",
        data_sources=["pellet_scores.csv"],
    )
    fig, ax, ax_info = fp.create_figure(
        figsize=(12, 9),
        methodology_text="EXPERIMENT  Skilled reaching...\\nSUBJECTS  N=11...",
    )
    # ... plot on ax ...
    fp.save(fig, "pellet_score_recovery.png")
"""

from .palettes import (
    COHORT_COLORS,
    PHASE_COLORS,
    PHASE_COLORS_LIGHT,
    OUTCOME_COLORS,
    DOMAIN_COLORS,
    PELLET_PHASE_COLORS,
    get_subject_colors,
)
from .standards import (
    DPI,
    FONT,
    REQUIRED_ELEMENTS,
    apply_style,
)
from .annotations import (
    add_methodology_panel,
    add_stat_bracket,
    add_provenance_footer,
    format_phase_definitions,
)
from .protocol import FigureProtocol
from .export import save_figure

__all__ = [
    # Palettes
    "COHORT_COLORS",
    "PHASE_COLORS",
    "PHASE_COLORS_LIGHT",
    "OUTCOME_COLORS",
    "DOMAIN_COLORS",
    "PELLET_PHASE_COLORS",
    "get_subject_colors",
    # Standards
    "DPI",
    "FONT",
    "REQUIRED_ELEMENTS",
    "apply_style",
    # Annotations
    "add_methodology_panel",
    "add_stat_bracket",
    "add_provenance_footer",
    "format_phase_definitions",
    # Protocol
    "FigureProtocol",
    # Export
    "save_figure",
]
