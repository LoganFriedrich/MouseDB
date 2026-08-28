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
# From MOUSEREACH_PROCESSING_ROOT, else mousedb config (mousereach_pipeline_root);
# None when neither is set -- the DLC file views then report "not configured".
from ..config import mousereach_pipeline_root as _mr_root
_env = os.environ.get("MOUSEREACH_PROCESSING_ROOT")
_pipeline_path = Path(_env) if _env else _mr_root()
if _pipeline_path is None:
    PROCESSING_ROOT = None
elif (_pipeline_path / "Processing").is_dir():
    PROCESSING_ROOT = _pipeline_path / "Processing"
else:
    PROCESSING_ROOT = _pipeline_path

# LDAP settings (Phase 4 - not yet implemented)
LDAP_SERVER = os.environ.get("MOUSEDB_LDAP_SERVER")    # e.g. ldaps://directory.example.edu:636
LDAP_BASE_DN = os.environ.get("MOUSEDB_LDAP_BASE_DN")  # e.g. DC=example,DC=edu
