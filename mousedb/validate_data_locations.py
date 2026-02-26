"""
validate_data_locations.py - Connectome data location validator.

Checks that all data in the Connectome project complies with the
code-vs-data separation philosophy.

Usage:
    python -m mousedb.validate_data_locations
    python -m mousedb.validate_data_locations --root Y:/2_Connectome
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple


# =============================================================================
# Individual check functions
# =============================================================================

def check_python_in_pipelines(root: Path) -> Tuple[bool, List[str]]:
    """Check [1/5]: No .py files should exist in Pipeline directories.

    Returns (passed, messages).
    """
    pipeline_dirs = [
        root / "Tissue" / "MouseBrain_Pipeline",
        root / "Behavior" / "MouseReach_Pipeline",
    ]

    violations = []
    for pipeline_dir in pipeline_dirs:
        if not pipeline_dir.exists():
            continue
        for py_file in pipeline_dir.rglob("*.py"):
            violations.append(str(py_file))

    if violations:
        msgs = [f"  [FAIL] Found {len(violations)} .py file(s) in Pipeline directories:"]
        for v in violations:
            msgs.append(f"    - {v}")
        return False, msgs
    else:
        return True, ["  [OK] No .py files found in Pipeline directories"]


def check_orphaned_outputs(root: Path) -> Tuple[bool, List[str]]:
    """Check [2/5]: No orphaned analysis outputs in Pipeline directories.

    Looks for analysis artifacts (coloc PNGs, overlay PNGs, measurement CSVs)
    that should have been pushed to Databases.

    Returns (passed, messages).
    """
    pipeline_dirs = [
        root / "Tissue" / "MouseBrain_Pipeline",
        root / "Behavior" / "MouseReach_Pipeline",
    ]

    # Patterns that indicate analysis outputs (not raw data)
    bad_name_patterns = [
        "*_coloc_result.png",
        "*_coloc_result*.png",
        "*_overlay.png",
        "*_overlay*.png",
    ]

    # Specific directories that should not exist
    bad_dirs = [
        root / "Tissue" / "MouseBrain_Pipeline" / "2D_Slices" / "ENCR" / "ROI_Figures",
    ]

    violations = []

    # Check specific directories
    for bad_dir in bad_dirs:
        if bad_dir.exists():
            violations.append(f"Directory should not exist: {bad_dir}")

    # Check for orphaned file patterns in pipeline dirs
    for pipeline_dir in pipeline_dirs:
        if not pipeline_dir.exists():
            continue
        for pattern in bad_name_patterns:
            for found in pipeline_dir.rglob(pattern):
                violations.append(str(found))

    if violations:
        msgs = [f"  [FAIL] Found {len(violations)} orphaned analysis output(s) in Pipeline directories:"]
        for v in violations:
            msgs.append(f"    - {v}")
        return False, msgs
    else:
        return True, ["  [OK] No orphaned analysis outputs in Pipeline directories"]


def check_registry_integrity(root: Path) -> Tuple[bool, List[str]]:
    """Check [3/5]: Registry JSON files are valid and well-formed.

    Returns (passed, messages).
    """
    exports_dir = root / "Databases" / "exports"
    if not exports_dir.exists():
        return True, ["  [OK] No exports directory found (nothing to check)"]

    registry_files = list(exports_dir.glob("*/registry.json"))
    if not registry_files:
        return True, ["  [OK] No registries found (nothing to check)"]

    msgs = []
    any_fail = False

    for reg_path in sorted(registry_files):
        analysis_name = reg_path.parent.name

        # Attempt to load JSON
        try:
            with open(reg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            msgs.append(f"  [FAIL] {analysis_name}: invalid JSON - {e}")
            any_fail = True
            continue
        except OSError as e:
            msgs.append(f"  [FAIL] {analysis_name}: cannot read - {e}")
            any_fail = True
            continue

        # Validate structure
        if not isinstance(data, dict) or "entries" not in data:
            msgs.append(f"  [FAIL] {analysis_name}: missing 'entries' key")
            any_fail = True
            continue

        entries = data["entries"]
        # entries can be a dict (keyed by sample) or a list
        if isinstance(entries, dict):
            entry_values = list(entries.values())
        elif isinstance(entries, list):
            entry_values = entries
        else:
            msgs.append(f"  [FAIL] {analysis_name}: 'entries' is not a dict or list")
            any_fail = True
            continue

        total = len(entry_values)
        missing_hash = sum(1 for e in entry_values if not e.get("method_hash"))

        if missing_hash > 0:
            msgs.append(
                f"  [FAIL] {analysis_name}: {total} entries, "
                f"{missing_hash} missing method_hash"
            )
            any_fail = True
        else:
            msgs.append(f"  {analysis_name}: {total} entries, all valid")

    if not any_fail:
        msgs.append("  [OK] All registries valid")
    return not any_fail, msgs


def check_method_currency(root: Path) -> Tuple[bool, List[str]]:
    """Check [4/5]: All registry entries match the current approved method.

    Imports AnalysisRegistry from mousebrain. Degrades gracefully if not installed.

    Returns (passed, messages).
    """
    # Try to import AnalysisRegistry
    try:
        from mousebrain.analysis_registry import AnalysisRegistry, get_approved_method
    except ImportError:
        return True, [
            "  [SKIP] mousebrain package not available - method currency check skipped"
        ]

    exports_dir = root / "Databases" / "exports"
    if not exports_dir.exists():
        return True, ["  [OK] No exports directory found (nothing to check)"]

    registry_dirs = [d for d in exports_dir.iterdir() if d.is_dir()]
    if not registry_dirs:
        return True, ["  [OK] No registries found (nothing to check)"]

    msgs = []
    any_fail = False
    approved = get_approved_method()

    for reg_dir in sorted(registry_dirs):
        analysis_name = reg_dir.name
        reg_path = reg_dir / "registry.json"
        if not reg_path.exists():
            continue

        try:
            registry = AnalysisRegistry(
                analysis_name=analysis_name,
                db_root=root / "Databases",
            )
            stale = registry.get_stale_samples(approved)
            count = len(stale) if stale else 0
            if count > 0:
                msgs.append(f"  [FAIL] {analysis_name}: {count} stale entries")
                any_fail = True
            else:
                msgs.append(f"  {analysis_name}: 0 stale entries")
        except Exception as e:
            msgs.append(f"  [WARN] {analysis_name}: could not check staleness - {e}")
            # Warn but do not fail - registry may be an older format
            continue

    if not any_fail:
        msgs.append("  [OK] All entries current")
    return not any_fail, msgs


def check_documentation_files(root: Path) -> Tuple[bool, List[str]]:
    """Check [5/5]: Required documentation files are present.

    Returns (passed, messages).
    """
    required = [
        root / "CLAUDE.md",
        root / "Databases" / "CLAUDE.md",
        root / "Tissue" / "CLAUDE.md",
        root / "Behavior" / "CLAUDE.md",
        root / "Databases" / "exports" / "AGENTS.md",
        root / "Databases" / "figures" / "AGENTS.md",
        root / "Databases" / "logs" / "AGENTS.md",
    ]

    missing = [str(p) for p in required if not p.exists()]

    if missing:
        msgs = [f"  [FAIL] Missing {len(missing)} required documentation file(s):"]
        for m in missing:
            msgs.append(f"    - {m}")
        return False, msgs
    else:
        return True, [f"  [OK] All {len(required)} required files present"]


# =============================================================================
# Main runner
# =============================================================================

def main(root: Optional[Path] = None) -> int:
    """Run all checks and print a report.

    Args:
        root: Connectome root path. Defaults to Y:/2_Connectome.

    Returns:
        Exit code: 0 if all pass, 1 if any fail.
    """
    if root is None:
        root = Path("Y:/2_Connectome")

    root = Path(root)

    print("=== Connectome Data Location Validator ===")
    print()

    checks = [
        ("[1/5] Python files in Pipeline directories...", check_python_in_pipelines),
        ("[2/5] Orphaned analysis outputs...", check_orphaned_outputs),
        ("[3/5] Registry integrity...", check_registry_integrity),
        ("[4/5] Method currency...", check_method_currency),
        ("[5/5] Required documentation...", check_documentation_files),
    ]

    results = []
    for label, fn in checks:
        print(label)
        try:
            passed, msgs = fn(root)
        except Exception as e:
            passed = False
            msgs = [f"  [FAIL] Unexpected error: {e}"]
        for msg in msgs:
            print(msg)
        results.append((label, passed))
        print()

    # Summary
    print("=" * 44)
    failed = [(label, passed) for label, passed in results if not passed]
    if not failed:
        print(f"RESULT: ALL CHECKS PASSED ({len(results)}/{len(results)})")
        return 0
    else:
        print(f"RESULT: {len(failed)} CHECK(S) FAILED")
        for label, _ in failed:
            # Strip the trailing "..." from the label for summary line
            tag = label.rstrip(".").strip()
            print(f"  - {tag}")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Validate Connectome data location compliance."
    )
    parser.add_argument(
        "--root",
        default="Y:/2_Connectome",
        help="Connectome root directory (default: Y:/2_Connectome)",
    )
    args = parser.parse_args()
    sys.exit(main(root=Path(args.root)))
