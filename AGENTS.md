<!-- Parent: ../AGENTS.md -->
# mousedb - Tool Repository

> **Type**: CODE (git repo)
> **Purpose**: Cross-project data management package
> **Env**: `Y:\2_Connectome\envs\MouseDB`

## Key Files
- `mousedb/schema.py` - SQLAlchemy ORM (17 tables: subjects, cohorts, brain samples, calibration runs, pellet scores, etc.)
- `mousedb/importers.py` - Pull data from Pipeline CSVs into connectome.db (ExcelImporter, BrainGlobeImporter)
- `mousedb/exporters.py` - Export to Excel, ODC, Parquet formats
- `mousedb/validators.py` - ID format validation (subject, cohort, project codes)
- `mousedb/cli.py` - CLI entry points (mousedb, mousedb-status, etc.)
- `mousedb/gui/` - PyQt5 GUI for data entry and dashboard
- `mousedb/stats.py` - Summary statistics calculations
- `mousedb/lab_figures.py` - Automated figure generation

## For AI Agents
- This is a CODE directory. All new code goes in `mousedb/`.
- The data this tool manages lives in `Databases/` (connectome.db, exports/, figures/, logs/).
- Analysis outputs from other tools flow here via `mousebrain.analysis_registry.AnalysisRegistry`.
- mousedb consumes from `Databases/exports/` and `Databases/figures/` -- it does NOT reach into Pipeline directories directly (except for legacy BrainGlobeImporter which pulls from 3D Pipeline CSVs).
