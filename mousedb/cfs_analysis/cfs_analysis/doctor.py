"""Environment health check for the CFS analysis tool.

Used by notebook ``00_setup`` to show a green/red status table before the
user tries to run any analysis. The audience is a lab member who may not
know what a Python environment is, so error messages point at specific
files and steps rather than tracebacks.
"""
from __future__ import annotations

import importlib
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

from . import __version__, region_prior_source
from .config import BUNDLED_DB_PATH, CACHE_DIR, get_db_path


REQUIRED_PACKAGES = (
    "pandas",
    "numpy",
    "sqlalchemy",
    "sklearn",           # imported as sklearn but the PyPI name is scikit-learn
    "statsmodels",
    "scipy",
    "matplotlib",
    "seaborn",
    "plotly",
    "pyarrow",
    "jupyterlab",
)


@dataclass
class CheckResult:
    """One row of the doctor's diagnostic table."""
    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        mark = "[OK]" if self.ok else "[FAIL]"
        return f"{mark:<7} {self.name:<28} {self.detail}"


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 11)
    detail = f"{sys.version.split()[0]} on {platform.system()} {platform.release()}"
    if not ok:
        detail += " -- tool requires Python 3.11 or newer"
    return CheckResult("Python version", ok, detail)


def _check_package(name: str) -> CheckResult:
    try:
        mod = importlib.import_module(name)
        version = getattr(mod, "__version__", "?")
        return CheckResult(f"import {name}", True, f"version {version}")
    except ImportError as e:
        pypi_name = {"sklearn": "scikit-learn"}.get(name, name)
        return CheckResult(
            f"import {name}",
            False,
            f"missing -- run: pip install {pypi_name}",
        )


def _check_db() -> CheckResult:
    db_path = get_db_path()
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        is_bundled = db_path.resolve() == BUNDLED_DB_PATH.resolve()
        location = "bundled" if is_bundled else "external"
        return CheckResult(
            "Database file",
            True,
            f"found at {db_path} ({size_mb:.1f} MB, {location})",
        )
    return CheckResult(
        "Database file",
        False,
        f"not found at {db_path} -- copy connectome.db into _bundled_data/ or "
        "set CFS_ANALYSIS_DB env var",
    )


def _check_cache_dir() -> CheckResult:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        test_file = CACHE_DIR / ".doctor_write_test"
        test_file.write_bytes(b"ok")
        test_file.unlink()
        return CheckResult("Cache directory", True, f"writable at {CACHE_DIR}")
    except OSError as e:
        return CheckResult("Cache directory", False, f"not writable at {CACHE_DIR}: {e}")


def _check_region_priors() -> CheckResult:
    source = region_prior_source()
    if source == "mousedb":
        return CheckResult("Region priors", True, "using live mousedb.region_priors")
    return CheckResult("Region priors", True, "using frozen fallback (mousedb not installed)")


def run_checks() -> List[CheckResult]:
    """Execute every diagnostic check and return the list of results."""
    results = [_check_python_version()]
    for pkg in REQUIRED_PACKAGES:
        results.append(_check_package(pkg))
    results.append(_check_db())
    results.append(_check_cache_dir())
    results.append(_check_region_priors())
    return results


def doctor() -> bool:
    """Run all checks, print a status table, return True on all-green."""
    print(f"cfs_analysis {__version__} -- environment check")
    print("=" * 72)
    results = run_checks()
    for r in results:
        print(r.render())
    print("=" * 72)
    failures = [r for r in results if not r.ok]
    if failures:
        print(f"{len(failures)} check(s) failed. See TROUBLESHOOTING.md for fixes.")
        return False
    print("All green. Ready to run the analysis notebooks.")
    return True


if __name__ == "__main__":
    sys.exit(0 if doctor() else 1)
