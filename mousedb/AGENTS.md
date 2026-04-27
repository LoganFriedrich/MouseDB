<!-- Parent: ../AGENTS.md -->
# mousedb Package Source

> **Type**: CODE
> **Purpose**: Package implementation modules

## Key Modules
| Module | Purpose |
|--------|---------|
| `schema.py` | SQLAlchemy ORM with 17 tables |
| `importers.py` | Data importers (Excel, BrainGlobe) |
| `exporters.py` | Data exporters (Excel, ODC, Parquet) |
| `validators.py` | ID format and data validation |
| `cli.py` | CLI commands |
| `database.py` | Database connection management |
| `visualizations.py` | Matplotlib cohort plots (learning curves, phase comparison, heatmaps) |
| `lab_figures.py` | Lab meeting multi-domain figure generator (15 figures) |
| `figures/` | **Figure generation standards module** (palettes, annotations, protocol, export) |
| `gui/` | PyQt5 GUI widgets |
| `web/` | FastAPI web dashboard with Plotly visualizations |
| `cohort_tools/` | Cohort sheet management |

## Figure Generation Standards (`figures/`)
The `figures/` module is the **single source of truth** for how figures are made
across ALL Connectome tools. Every figure script must import from here:

- `figures.palettes` — All color palettes (cohort, phase, outcome, domain, tray types). No inline colors.
- `figures.standards` — DPI, fonts, required elements, rcParams presets.
- `figures.annotations` — Methodology panels, stat brackets, provenance footers.
- `figures.validation` — Post-render layout checks (overlap, readability, bounds).
- `figures.legends` — `FigureLegend` dataclass (7-component structured narrative).
- `figures.stats` — Cohen's d, stat formatting, test justifications, baseline normalization.
- `figures.kinematic_filters` — Plausible range filtering, outcome-based filtering.
- `figures.axes` — Phase/DPI/session axis setup, gap handling.
- `figures.export` — Save with provenance JSON sidecar + registry.
- `recipes/base.py` — `FigureRecipe` ABC: load_data, analyze, plot, methodology_text, figure_legend.

**Rules (enforced — see FIGURE_REVIEW_LESSONS.md for full 39-rule set):**

### Content Rules
1. DATA FIGURES ONLY — no architecture diagrams, no schema diagrams. If it looks the same regardless of data, it's not a data figure.
2. No pie/donut charts. Use bars.
3. Every figure answers ONE clear question (Rule 29).
4. Every figure must tell a complete story: question, method, finding, analysis, effect sizes, confounds, follow-up (Rule 25). Use `FigureLegend` dataclass.

### Statistical Rules
5. Effect sizes (Cohen's d) MANDATORY for every significant result (Rule 26). Use `figures.stats.cohens_d_paired()`.
6. Justify the statistical test choice in the methodology panel (Rule 34). Use `figures.stats.stat_justification()`.
7. P-values alone are never enough. Always report: test name, statistic, p, d, N, alternative.
8. Within-subject effects > between-group averages for longitudinal data (Rule 32).

### Data Filtering Rules
9. ONLY Pillar tray sessions for performance/kinematic analysis. Flat/Easy = engagement only (Rule 22, 24). Use `palettes.validate_tray_filter()`.
10. Kinematic data must be filtered to plausible ranges before plotting (Rule 30). Use `kinematic_filters.filter_plausible_reaches()`.
11. Filter by reach outcome — successful retrievals are primary interest (Rule 31). Use `kinematic_filters.filter_by_outcome()`.
12. State ALL inclusion criteria on the figure: tray type, outcome filter, N, exclusions (Rule 22).
13. Baseline normalization needs a floor — exclude low-baseline animals (Rule 28). Use `stats.normalize_to_baseline()`.

### Visual Rules
14. Colors encode meaning, never decoration. Use `figures.palettes` only (Rule 4).
15. Individual data points must be distinguishable with per-subject colors (Rule 6). Use `palettes.get_persistent_subject_colors()`.
16. Subject IDs include cohort context: "03 (CNT_01)" not "Subject 3" (Rule 7). Use `palettes.get_subject_label()`.
17. X-axis: experimental timepoints (DPI, session #), never calendar dates (Rule 13). Use `axes.setup_dpi_axis()` or `axes.setup_session_axis()`.
18. Layout validation after every render — no overlapping text, no out-of-bounds elements (Rule 3). Automatic in `FigureRecipe.generate()`.
19. Never display empty data columns — remove conditions with no data (Rule 38).
20. Recovery direction must be indicated per metric (Rule 36).
21. Group thresholds must be stated and justified on the figure (Rule 37).

### Workflow Rule
22. **RECURSIVE QA**: After generating any figure, READ the output image, check it against all rules above, fix the script if needed, regenerate, and check again. A figure is not done until visual inspection confirms compliance.

## For AI Agents
- All new features go here as Python modules.
- Follow existing patterns: use SQLAlchemy ORM, validate IDs, log imports.
- ASCII only in print/logging output (Windows console compatibility).
- **All new figure scripts must use `mousedb.figures.FigureProtocol`.**
