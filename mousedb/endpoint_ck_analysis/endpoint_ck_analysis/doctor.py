"""Environment health check for the CFS analysis tool.

Used by notebook ``00_setup`` to show a green/red status table before the
user tries to run any analysis. The audience is a lab member who may not
know what a Python environment is, so error messages point at specific
files and steps rather than tracebacks.
"""
from __future__ import annotations  # postpone-annotation evaluation

import importlib  # importlib: standard library; lets us import modules by name string at runtime
import os  # os: environment-variable access (used indirectly via config)
import platform  # platform: OS info for human-readable diagnostics
import sys  # sys: Python interpreter info (version, exit codes)
from dataclasses import dataclass  # lightweight class with auto __init__/__repr__
from pathlib import Path  # pathlib: object-oriented filesystem paths
from typing import List  # type-hint primitives

from . import __version__, region_prior_source                                    # package version + helper that reports whether mousedb or the fallback is being used
from .config import BUNDLED_DB_PATH, CACHE_DIR, get_db_path                       # paths used by the cache and DB checks


REQUIRED_PACKAGES = (                                                              # tuple of import names; missing any of these is a blocking [FAIL]
    "pandas",
    "numpy",
    "sqlalchemy",
    "sklearn",                                                                     # imported as sklearn but the PyPI name is scikit-learn (handled in _check_package)
    "statsmodels",
    "scipy",
    "matplotlib",
    "seaborn",
    "plotly",
    "pyarrow",
    "ipykernel",                                                                   # so VS Code's kernel picker can see this env
)

# Optional packages: missing these produces an [INFO] line, not a [FAIL].
# jupyterlab is only needed for the run_analysis.bat / .command launcher path.
# VS Code users do not need it.
OPTIONAL_PACKAGES = (
    "jupyterlab",                                                                  # for the JupyterLab launcher (skip if using VS Code)
)


@dataclass
class CheckResult:
    """One row of the doctor's diagnostic table.

    ``severity`` controls how a not-ok result is treated:
        'fail'  - red, blocks doctor() from reporting all-green
        'info'  - yellow, surfaces the absence but does not block
    """
    name: str                                                                       # short label printed in the leftmost column
    ok: bool                                                                        # True if the check passed
    detail: str                                                                     # human-readable detail (or remediation hint on failure)
    severity: str = "fail"                                                          # 'fail' or 'info'; default 'fail'

    @property
    def is_blocker(self) -> bool:
        return (not self.ok) and self.severity == "fail"                            # blocker iff failed AND severity is fail (info-level failures don't block)

    def render(self) -> str:
        if self.ok:                                                                 # green check
            mark = "[OK]"
        elif self.severity == "info":                                               # yellow info (missing optional)
            mark = "[INFO]"
        else:                                                                       # red fail
            mark = "[FAIL]"
        return f"{mark:<7} {self.name:<28} {self.detail}"                          # format-spec :<7 left-aligns and pads to width 7


def _check_python_version() -> CheckResult:
    major, minor = sys.version_info[:2]                                             # tuple-unpack first two version components (e.g., 3, 10)
    ok = (major, minor) >= (3, 10)                                                  # tuple comparison: lexicographic, so this is "is at least 3.10"
    detail = f"{sys.version.split()[0]} on {platform.system()} {platform.release()}"  # version + OS name + OS release (e.g., '3.10.13 on Windows 11')
    if not ok:
        detail += " -- tool requires Python 3.10 or newer"                          # remediation hint appended to the detail
    return CheckResult("Python version", ok, detail)


def _check_package(name: str, severity: str = "fail") -> CheckResult:
    try:
        mod = importlib.import_module(name)                                         # dynamic import by string name
        version = getattr(mod, "__version__", "?")                                  # most packages expose __version__; default to '?' if not
        return CheckResult(f"import {name}", True, f"version {version}", severity=severity)
    except ImportError as e:                                                        # package not installed -> failure
        pypi_name = {"sklearn": "scikit-learn"}.get(name, name)                    # remap import-name -> install-name for the one common alias
        detail = f"missing -- run: pip install {pypi_name}"                        # actionable remediation hint
        if severity == "info":                                                      # for optional packages, soften the message
            detail += "  (optional; only needed for the JupyterLab launcher path)"
        return CheckResult(
            f"import {name}",
            False,
            detail,
            severity=severity,
        )


def _check_db() -> CheckResult:
    db_path = get_db_path()                                                         # resolves env-var override or bundled default
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)                            # .stat().st_size is bytes; divide by 1024^2 to get megabytes
        is_bundled = db_path.resolve() == BUNDLED_DB_PATH.resolve()                 # .resolve() canonicalizes the path so symlinks/relatives compare correctly
        location = "bundled" if is_bundled else "external"                          # ternary: short label for the detail string
        return CheckResult(
            "Database file",
            True,
            f"found at {db_path} ({size_mb:.1f} MB, {location})",                  # :.1f formats with one decimal place
        )
    return CheckResult(
        "Database file",
        False,
        f"not found at {db_path} -- copy connectome.db into _bundled_data/ or "
        "set ENDPOINT_CK_ANALYSIS_DB env var",                                     # implicit string concatenation across the two adjacent literals
    )


def _check_cache_dir() -> CheckResult:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)                                # ensure dir exists; parents=True creates intermediates
        test_file = CACHE_DIR / ".doctor_write_test"                                # write a small probe file to verify write permission
        test_file.write_bytes(b"ok")                                                # b"ok" is bytes literal; write_bytes does the I/O
        test_file.unlink()                                                          # unlink: delete the probe file (Path's analog of os.remove)
        return CheckResult("Cache directory", True, f"writable at {CACHE_DIR}")
    except OSError as e:                                                            # any filesystem error -> failure with the original message
        return CheckResult("Cache directory", False, f"not writable at {CACHE_DIR}: {e}")


def _check_region_priors() -> CheckResult:
    source = region_prior_source()                                                  # returns 'mousedb' (live import worked) or 'fallback' (frozen snapshot used)
    if source == "mousedb":
        return CheckResult("Region priors", True, "using live mousedb.region_priors")
    return CheckResult("Region priors", True, "using frozen fallback (mousedb not installed)")


def run_checks() -> List[CheckResult]:
    """Execute every diagnostic check and return the list of results."""
    results = [_check_python_version()]                                             # start the list with the Python-version check
    for pkg in REQUIRED_PACKAGES:                                                   # one row per required import; failures are blockers
        results.append(_check_package(pkg))
    for pkg in OPTIONAL_PACKAGES:                                                   # one row per optional import; failures are info-only
        results.append(_check_package(pkg, severity="info"))
    results.append(_check_db())                                                     # database presence
    results.append(_check_cache_dir())                                              # cache writability
    results.append(_check_region_priors())                                          # which region-priors source is in use
    return results


def doctor() -> bool:
    """Run all checks, print a status table, return True on no blocking failures."""
    print(f"endpoint_ck_analysis {__version__} -- environment check")              # header line with package version
    print("=" * 72)                                                                 # divider; "=" * 72 repeats the character
    results = run_checks()                                                          # gather all check results
    for r in results:                                                               # render each row to stdout
        print(r.render())
    print("=" * 72)
    blockers = [r for r in results if r.is_blocker]                                # list comprehension: failed-and-fail-severity rows
    info_only = [r for r in results if (not r.ok) and r.severity == "info"]        # info-level non-failures (informational, not blocking)
    if blockers:                                                                    # any blocker -> doctor reports failure
        print(f"{len(blockers)} blocking check(s) failed. See TROUBLESHOOTING.md for fixes.")
        return False
    if info_only:                                                                   # no blockers but some info flags
        print(f"All required checks passed. {len(info_only)} optional check(s) flagged (informational).")
    else:                                                                           # nothing flagged at all
        print("All green. Ready to run the analysis notebooks.")
    return True


if __name__ == "__main__":                                                          # allow running as `python -m endpoint_ck_analysis.doctor`
    sys.exit(0 if doctor() else 1)                                                  # 0 = success, 1 = failure (standard shell convention)
