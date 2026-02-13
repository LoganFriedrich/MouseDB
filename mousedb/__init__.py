"""
MouseDB - Mouse Colony and Experiment Data Management

A validated data entry system for mouse experiment data.
Replaces Excel tracking sheets with SQLite database and PyQt GUI.

Usage:
    mousedb-entry           # Launch GUI
    mousedb-new-cohort      # Create new cohort
    mousedb-import          # Import Excel files
    mousedb-export          # Export to Excel/Parquet
    mousedb-status          # Show database stats
    mousedb-browse          # Browse database tables
"""

__version__ = "0.1.0"
__author__ = "Logan Friedrich"

import os
from pathlib import Path

# Default paths - use environment variable or fallback to default
MOUSEDB_ROOT = Path(os.environ.get("MOUSEDB_ROOT", "Y:/LAB_ROOT/Databases"))
DEFAULT_DB_PATH = MOUSEDB_ROOT / "connectome.db"
DEFAULT_EXPORT_PATH = MOUSEDB_ROOT / "exports"
DEFAULT_LOG_PATH = MOUSEDB_ROOT / "logs"
