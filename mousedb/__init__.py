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

# Where things are on THIS machine comes from mousedb.config (environment
# variable or ~/.mousedb/config.json; `mousedb config --show`). There is no
# built-in default: these are None until configured, and anything that needs
# one calls mousedb.config.require(), which raises a message saying exactly
# what to set. Why: this is a public tool; a lab's drive letters are not part
# of it, and a silent wrong default is worse than a clear error.
from . import config as _config
MOUSEDB_ROOT = _config.mousedb_root()
DEFAULT_DB_PATH = _config.db_path()
DEFAULT_EXPORT_PATH = _config.export_path()
DEFAULT_LOG_PATH = _config.log_path()
