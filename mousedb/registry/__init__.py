"""
Figure Registry - tracking, lineage, and staleness detection for all generated figures.
"""

from .figure_registry import FigureRegistry
from .accession import generate_accession
from .version_capture import capture_versions
from .models import FigureRecord, FigureDataSource, FigureToolVersion, FigureParameter

__all__ = [
    "FigureRegistry",
    "generate_accession",
    "capture_versions",
    "FigureRecord",
    "FigureDataSource",
    "FigureToolVersion",
    "FigureParameter",
]
