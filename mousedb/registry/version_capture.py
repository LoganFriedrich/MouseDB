"""
Version capture for the Figure Registry.

Snapshots all relevant tool/library versions at figure generation time
so that every figure's software environment is fully reproducible.
"""

import json
import platform
import sys
from pathlib import Path


def capture_versions() -> dict:
    """Capture all relevant tool/library versions.

    Returns
    -------
    dict
        Mapping of tool_name -> version_string.
        Missing packages report "not_installed".
    """
    versions = {}

    # Python and platform
    versions["python"] = sys.version
    versions["platform"] = platform.platform()

    # Internal tools
    _try_version(versions, "mousedb", "mousedb")
    _try_version(versions, "mousereach", "mousereach")
    _try_version(versions, "mousebrain", "mousebrain")
    _try_version(versions, "mousecam", "mousecam")

    # Core scientific stack
    _try_version(versions, "matplotlib", "matplotlib")
    _try_version(versions, "numpy", "numpy")
    _try_version(versions, "scipy", "scipy")
    _try_version(versions, "pandas", "pandas")

    # DLC scorer from pipeline_versions.json
    try:
        from mousedb import MOUSEDB_ROOT

        pv_path = (
            MOUSEDB_ROOT.parent
            / "Behavior"
            / "MouseReach_Pipeline"
            / "pipeline_versions.json"
        )
        if pv_path.exists():
            with open(pv_path, "r") as f:
                pv = json.load(f)
            versions["dlc_scorer"] = pv.get("versions", {}).get(
                "dlc_scorer", "unknown"
            )
    except Exception:
        pass

    return versions


def _try_version(versions: dict, key: str, module_name: str) -> None:
    """Try to import a module and capture its __version__.

    Parameters
    ----------
    versions : dict
        Dict to update in-place.
    key : str
        Key to store the version under.
    module_name : str
        Python module name to import.
    """
    try:
        mod = __import__(module_name)
        versions[key] = getattr(mod, "__version__", "unknown")
    except ImportError:
        versions[key] = "not_installed"
    except Exception:
        versions[key] = "error"
