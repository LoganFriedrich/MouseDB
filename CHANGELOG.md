# Changelog

Convention: most-recent first. Entries describe what changed in the
checked-in code, not what's planned.

## Unreleased

### Added
- `mousedb import-analyses` (`mousedb.import_analyses`, console script
  `mousedb-import-analyses`): mirrors MouseBrain's analysis registry
  (exports, figures, logs, `registry.json` provenance) into the mousedb
  folders, archives (never deletes) files withdrawn upstream, and writes
  `exports/ANALYSES_MANIFEST.json`. Files only -- no database writes.
- `mousedb-data-status` and the Where Is My Data tab show one line per
  mirrored analysis (current / stale vs approved / invalidated / imported).
- Console script `mousedb-import-brains` (the wrapper existed, the script
  entry did not).

### Fixed
- `mousedb import-brains` without `--summary-dir` raised NameError (a removed
  module constant). The default now comes from
  `mousedb config --set mousebrain_pipeline_root`.
- eLife group counts silently never imported when `mousebrain` was not
  importable. They are now read from MouseBrain's own
  `2_Data_Summary/elife_counts.csv`; when neither that file nor the
  recompute is possible the run prints an explicit error and exits 1.
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
