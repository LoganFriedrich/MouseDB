# Changelog

Convention: most-recent first. Entries describe what changed in the
checked-in code, not what's planned.

## Unreleased

### Added
- Top-level `README.md`, `CHANGELOG.md`, and `.github/workflows/test.yml` —
  human-facing project orientation and CI scaffolding that previously
  lived only in subpackages or AGENTS.md.
- `tests/test_validators.py` — first unit-test coverage for
  `mousedb.validators`. The module is the front line for every import path
  (ID format, weight ranges, pellet scores, surgery types, tray bounds);
  prior coverage was zero.
- `[tool.pytest.ini_options]` block in `pyproject.toml`: explicit
  `testpaths`, `addopts = "--strict-markers -ra"`, and `norecursedirs` so
  pytest from the repo root doesn't collect stray scripts or cache dirs.
- `[tool.coverage]` config (source + omit + exclude_lines) for opt-in
  `pytest --cov=mousedb` runs.
- `nbclient`, `nbformat`, `papermill`, `pytest-cov` added to the `dev`
  optional-dependency set so the endpoint_ck_analysis smoke test can run
  against a fresh dev install.

### Changed
- `requires-python` bumped to `>=3.10` to match the
  endpoint_ck_analysis sibling and drop the EOL'd 3.9.
- Author email placeholder removed from `pyproject.toml`.

### Renamed
- `test_setup.py` -> `manual_setup_check.py`. The original name made
  pytest auto-discover it during collection, but the script has
  interactive `input()` calls in its body and would hang any CI run.
  Renaming removes the landmine without losing the manual setup smoke.

### Removed
- Stale `unified_data.egg-info/` and `mousedb.egg-info/` directories from
  the working tree. Both were already gitignored; this just clears the
  on-disk clutter so contributors don't think `unified_data` is still a
  package name.

### Fixed (pre-cleanup, included for context)
- `helpers/models.py`: `wald_test_terms().table` column lookup updated for
  statsmodels 0.14+ (which renamed `"P>chi2"` -> `"pvalue"`). Affected
  every per-feature LMM in notebook 05; pre-fix output was all-NaN.
- `endpoint_ck_analysis` notebook 07's interaction-LMM cell received the
  same fix.
