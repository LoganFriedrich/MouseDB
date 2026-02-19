"""Web application configuration."""

import os
from pathlib import Path

# Server settings
HOST = os.environ.get("MOUSEDB_WEB_HOST", "0.0.0.0")
PORT = int(os.environ.get("MOUSEDB_WEB_PORT", "8000"))

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# MouseReach pipeline paths (for DLC .h5 file access)
# Env var may point to pipeline root or Processing/ subdir
_pipeline_path = Path(os.environ.get(
    "MOUSEREACH_PROCESSING_ROOT",
    "Y:/2_Connectome/Behavior/MouseReach_Pipeline/Processing"
))
# Auto-detect: if path has a Processing/ subdir, use that
if (_pipeline_path / "Processing").is_dir():
    PROCESSING_ROOT = _pipeline_path / "Processing"
else:
    PROCESSING_ROOT = _pipeline_path

# LDAP settings (Phase 4 - not yet implemented)
LDAP_SERVER = os.environ.get("MOUSEDB_LDAP_SERVER", "ldaps://marqnet.mu.edu:636")
LDAP_BASE_DN = os.environ.get("MOUSEDB_LDAP_BASE_DN", "DC=marqnet,DC=mu,DC=edu")
