"""
Unified Data Entry System

A validated data entry system for the Connectomics Grant research project.
Replaces Excel tracking sheets with SQLite database and PyQt GUI.

Usage:
    unified-data-entry           # Launch GUI
    unified-data-new-cohort      # Create new cohort
    unified-data-import          # Import Excel files
    unified-data-export          # Export to Excel/Parquet
    unified-data-status          # Show database stats
    unified-data-browse          # Browse database tables
"""

__version__ = "0.1.0"
__author__ = "Logan Friedrich"

import os
from pathlib import Path

# Default paths - use environment variable or fallback to default
UNIFIED_DATA_ROOT = Path(os.environ.get("UNIFIED_DATA_ROOT", "Y:/2_Connectome/Unified_Data"))
DEFAULT_DB_PATH = UNIFIED_DATA_ROOT / "connectome.db"
DEFAULT_EXPORT_PATH = UNIFIED_DATA_ROOT / "exports"
DEFAULT_LOG_PATH = UNIFIED_DATA_ROOT / "logs"
