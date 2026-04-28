"""Smoke test for the CFS analysis tool.

Does NOT replace a proper test suite. Just verifies that, on a freshly
installed environment with the bundled database in place:

- The package imports.
- The environment doctor reports no failures.
- ``data_loader.load_all()`` produces every dataframe we expect.
- ``00_setup.ipynb`` executes end-to-end without cell errors.

Run with:
    pytest tests/test_smoke.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"


# ---------------------------------------------------------------------------
# Package-level import checks
# ---------------------------------------------------------------------------


def test_package_imports():
    import endpoint_ck_analysis  # noqa: F401
    from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns  # noqa: F401
    from endpoint_ck_analysis import region_prior_source
    assert region_prior_source() in {"mousedb", "fallback"}


def test_helpers_import():
    from endpoint_ck_analysis.helpers import (  # noqa: F401
        kinematics, connectivity, filters, dimreduce, models, plotting,
    )


def test_prefer_calibrated_units_drops_pixels_when_mm_exists():
    from endpoint_ck_analysis.helpers.kinematics import prefer_calibrated_units
    kept = prefer_calibrated_units(["max_extent_mm", "max_extent_pixels", "trajectory_straightness"])
    assert "max_extent_mm" in kept
    assert "max_extent_pixels" not in kept
    assert "trajectory_straightness" in kept


# ---------------------------------------------------------------------------
# Doctor + data load
# ---------------------------------------------------------------------------


def test_doctor_all_green():
    """Every diagnostic check must pass."""
    from endpoint_ck_analysis.doctor import run_checks
    failed = [r for r in run_checks() if not r.ok]
    assert not failed, "\n".join(r.render() for r in failed)


def test_load_all_returns_all_dataframes():
    """load_all should produce the six base dataframes + wide pivots + aggregations."""
    from endpoint_ck_analysis.data_loader import load_all
    data = load_all(use_cache=True, write_cache=False, verbose=False)

    # Base six
    assert not data.AKDdf.empty
    assert not data.FKDdf.empty
    assert not data.ACDUdf.empty
    assert not data.ACDGdf.empty
    assert not data.FCDUdf.empty
    assert not data.FCDGdf.empty

    # Wide pivots
    assert data.FCDGdf_wide.shape[0] > 0
    assert data.FCDGdf_wide.shape[1] > 0

    # Aggregations
    assert data.AKDdf_agg_contact.shape[0] > 0

    # Matched subjects
    assert len(data.matched_subjects) >= 1


def test_synthetic_cohort_recovers_prototype_structure():
    """Synthetic data with default noise scale should cluster back onto prototypes.

    This is the pipeline validation test: if cluster_subjects can't recover
    the known ground-truth (each synthetic subject's prototype), the helper
    chain is broken somewhere.
    """
    from endpoint_ck_analysis.data_loader import load_all
    from endpoint_ck_analysis.helpers.clusters import cluster_subjects
    from endpoint_ck_analysis.helpers.synthetic import prototype_map

    synth = load_all(use_cache=True, write_cache=False, verbose=False,
                     use_synthetic=True, synthetic_n=20, synthetic_seed=0)
    assert len(synth.matched_subjects) == 20
    assert all(s.startswith("SYN_") for s in synth.matched_subjects)

    X = synth.FCDGdf_wide.fillna(0)
    for method in ("ward", "kmeans"):  # skip gmm/consensus to keep test fast
        result = cluster_subjects(X, method=method, k=4, random_state=0)
        known = prototype_map(X.index).reindex(X.index).values
        # Modal agreement across prototypes should be very high (>= 0.8) at default noise
        agree = (result.labels.groupby(known).transform(lambda s: s.mode().iloc[0]) == result.labels).mean()
        assert agree >= 0.8, f"{method}: modal agreement {agree:.2%} < 0.8"


# ---------------------------------------------------------------------------
# End-to-end notebook execution
# ---------------------------------------------------------------------------


def _execute_notebook(notebook_path: Path, timeout: int = 180):
    """Run a notebook in-process via nbclient and return the executed notebook."""
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError:
        pytest.skip("nbformat / nbclient not installed; install the 'dev' extras.")
    nb = nbformat.read(notebook_path, as_version=4)
    client = NotebookClient(nb, timeout=timeout, kernel_name="python3")
    client.execute(cwd=str(notebook_path.parent))
    return nb


def _cell_errors(nb) -> list[str]:
    errors = []
    for i, cell in enumerate(nb.cells):
        if cell.get("cell_type") != "code":
            continue
        for output in cell.get("outputs", []):
            if output.get("output_type") == "error":
                errors.append(f"cell {i}: {output.get('ename')}: {output.get('evalue')}")
    return errors


def test_00_setup_notebook_executes_cleanly():
    """``00_setup.ipynb`` must run every cell without error."""
    nb_path = NOTEBOOKS_DIR / "00_setup.ipynb"
    assert nb_path.exists(), f"Notebook missing at {nb_path}"
    nb = _execute_notebook(nb_path, timeout=180)
    errors = _cell_errors(nb)
    assert not errors, "\n".join(errors)
