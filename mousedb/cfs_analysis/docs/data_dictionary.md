# Data dictionary

What's in each dataframe that `data_loader.load_all()` returns. See
[`../cfs_analysis/data_loader.py`](../cfs_analysis/data_loader.py) for
the code that builds these.

---

## Raw tables (straight from connectome.db)

| Name | Shape (approx) | What it is |
|------|-----------|-----|
| `subjectsdf` | ~130 rows | One row per mouse. Identity columns (subject_id, cohort), genotype, DOB. |
| `kinematicsdf` | ~355K rows | One row per reach. Every kinematic feature (many duplicated in different units), plus metadata columns (run_number, session_date, tray_type, etc.) and pipeline-version columns (mousereach_version, segmenter_version, ...). |
| `manual_pelletdf` | ~110K rows | One row per manually-scored pellet: subject_id, session_date, tray_type, tray_number, pellet_number, score (0/1/2), phase_group. |
| `weightsdf` | varies | Body-weight measurements per subject per date. Not used in current analysis but available. |
| `surgeriesdf` | ~135 rows | Surgery records: contusion, tracer injection, perfusion, each with date and parameters. |
| `brainsdf` | ~5 rows | One row per imaged brain sample. `brain_id` contains the imaging parameter signature; we filter on this to include only comparable brains. |
| `countsdf` | ~1.5K rows | One row per brain x region: cell_count for each atomic region (not yet eLife-grouped). Joined with brain_samples to attach subject_id. |
| `counts_groupeddf` | ~300 rows | Same but aggregated into eLife functional groups. |

---

## Base dataframes (filtered and tagged)

| Name | Shape | What it is |
|------|-----------|-----|
| `AKDdf` | ~120K rows | **A**ll **K**inematic **D**ata: reaches filtered to the dominant pipeline versions (mode of each `*_version` column), then restricted to the analyzable phase set (`ANALYZABLE_PHASES` in config) and the non-excluded cohorts. `outcome_group` column added: missed / displaced / retrieved. |
| `FKDdf` | ~11K rows | **F**iltered **K**inematic **D**ata: `AKDdf` further restricted to subjects that ALSO have connectomics (`matched_subjects`). |
| `ACDUdf` | ~1.5K rows | **A**ll **C**onnectomics **D**ata **U**ngrouped: per-subject per-atomic-region cell counts, brains filtered to the target imaging parameter string (`IMAGING_PARAMS_MATCH` in config). |
| `ACDGdf` | ~300 rows | **A**ll **C**onnectomics **D**ata **G**rouped: same but at eLife group resolution. |
| `FCDUdf` / `FCDGdf` | same as A* but | **F**iltered Connectomics variants: `ACDUdf` / `ACDGdf` restricted to matched subjects only. |
| `matched_subjects` | tuple of IDs | The subjects that appear in BOTH kinematics and connectomics. Currently 4: CNT_01_02, CNT_02_08, CNT_03_07, CNT_03_08. |

---

## Wide-format connectomics (one row per subject)

Produced by `helpers.connectivity.pivot_connectivity`. Columns are
`{region}_{hemisphere}` (e.g. `Red Nucleus_both`, `Red Nucleus_left`).

| Name | Shape | What it is |
|------|-----------|-----|
| `ACDUdf_wide` | (4, ~700) | Ungrouped atomic regions, all matched subjects. |
| `ACDGdf_wide` | (4, ~80) | eLife groups, all matched subjects. |
| `FCDUdf_wide` | (4, ~700) | Same as ACDUdf_wide but via the filtered path. |
| `FCDGdf_wide` | (4, ~80) | Same as ACDGdf_wide but via the filtered path. Main input to notebook 01 (connectivity PCA). |

---

## Aggregated kinematics (one row per subject x phase x group)

Produced by `helpers.kinematics.aggregate_kinematics` and
`aggregate_kinematics_by_contact`. MultiIndexed; call `.reset_index()`
(or the convenience `AKDdf_agg_contact_flat()` method) to flatten.

For each kinematic feature, five summary statistics are computed:
`_mean`, `_std`, `_median`, `_q25`, `_q75`.

| Name | Index levels | Row count |
|------|----|-----|
| `AKDdf_agg` | subject_id x phase_group x outcome_group | ~150 |
| `FKDdf_agg` | subject_id x phase_group x outcome_group (matched subjects) | ~40 |
| `AKDdf_agg_contact` | subject_id x phase_group x contact_group | ~340 |
| `FKDdf_agg_contact` | subject_id x phase_group x contact_group (matched subjects) | ~30 |

---

## Proportions (one row per subject x phase)

| Name | Columns | What it is |
|------|----|-----|
| `AKDdf_prop` / `FKDdf_prop` | missed, displaced, retrieved | Outcome proportions per subject per phase. |
| `AKDdf_contact_prop` / `FKDdf_contact_prop` | missed, contacted | Binary per-reach contact rate. |
| `AKDdf_segment_contact_prop` / `FKDdf_segment_contact_prop` | uncontacted, contacted | Binary per-segment contact rate (was the pellet ever touched in this segment?). |

---

## Kinematic feature naming convention

Most feature names end with a unit suffix:
`_mm`, `_mm_per_sec`, `_px_per_frame`, `_pixels`, `_ruler`. Features without
a suffix are unit-agnostic (straightness ratios, angle-in-degrees, etc.).

`helpers.kinematics.prefer_calibrated_units` drops less-preferred
duplicates (per `UNIT_SUFFIX_PREFERENCE` in config). `get_kinematic_cols`
returns the deduplicated list so hypothesis tests don't double-count the
same measurement in two units.

---

## Key columns shared across dataframes

| Column | Where |
|--------|-------|
| `subject_id` | Every dataframe. String identifier like `CNT_02_08`. |
| `session_date` | kinematicsdf, manual_pelletdf. |
| `tray_type` | kinematicsdf, manual_pelletdf. Single-char code; 'P' = pillar. |
| `phase_group` | kinematicsdf. One of `ANALYZABLE_PHASES` or the excluded set. |
| `contact_group` | kinematicsdf. 'missed' / 'contacted' per-reach. |
| `segment_contact_group` | kinematicsdf. Same but at segment granularity. |
| `outcome` | kinematicsdf. Fine-grained: retrieved / displaced_sa / displaced_outside / untouched / uncertain. |
| `outcome_group` | AKDdf/FKDdf (added by loader). Coarse: missed / displaced / retrieved. |
