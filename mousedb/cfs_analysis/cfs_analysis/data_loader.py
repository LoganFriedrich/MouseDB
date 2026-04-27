"""Load and prepare the six base dataframes from the bundled connectome.db.

Mirrors the data-loading sequence from the original notebook's Sections 3-6
exactly. The only difference is that paths come from ``config.get_db_path()``
rather than being hardcoded to Y:\\, and the side-effect CSV writes are
optional (they were debug artifacts in the notebook).

The public entry point is :func:`load_all`, which returns a :class:`LoadedData`
with every dataframe downstream notebooks need. By default it caches the
result to parquet under ``_bundled_data/cache/`` so subsequent notebook runs
skip the SQL + join + aggregation work.

Example
-------
    >>> from cfs_analysis.data_loader import load_all
    >>> data = load_all()
    >>> data.AKDdf.shape           # raw reaches in analyzable phases
    >>> data.FCDGdf_wide.shape     # eLife-grouped connectivity, matched subjects only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine

from .config import (
    ANALYZABLE_PHASES,
    CACHE_DIR,
    COHORTS_TO_EXCLUDE,
    IMAGING_PARAMS_MATCH,
    get_db_path,
)
from .helpers.connectivity import pivot_connectivity
from .helpers.filters import filter_to_shared
from .helpers.kinematics import (
    aggregate_kinematics,
    aggregate_kinematics_by_contact,
    compute_contact_proportions,
    compute_outcome_proportions,
)


# Tables that the cache stores. Everything else is cheap to recompute
# from these in load_all().
_CACHE_TABLES = (
    "subjectsdf", "kinematicsdf", "manual_pelletdf", "weightsdf", "surgeriesdf",
    "brainsdf", "countsdf", "counts_groupeddf",
    "AKDdf", "FKDdf", "ACDUdf", "ACDGdf", "FCDUdf", "FCDGdf",
    "ACDUdf_wide", "ACDGdf_wide", "FCDUdf_wide", "FCDGdf_wide",
    "AKDdf_agg", "FKDdf_agg", "AKDdf_agg_contact", "FKDdf_agg_contact",
    "AKDdf_prop", "FKDdf_prop",
    "AKDdf_contact_prop", "FKDdf_contact_prop",
    "AKDdf_segment_contact_prop", "FKDdf_segment_contact_prop",
)


@dataclass
class LoadedData:
    """Container for every dataframe the analysis notebooks read.

    Raw tables (straight from the DB):
        subjectsdf, kinematicsdf, manual_pelletdf, weightsdf, surgeriesdf,
        brainsdf, countsdf, counts_groupeddf

    Base dataframes (filtered/tagged):
        AKDdf  - All Kinematic Data
        FKDdf  - Filtered Kinematic Data (matched to connectomics subjects)
        ACDUdf - All Connectomics Data Ungrouped (region-level, imaging-filtered)
        ACDGdf - All Connectomics Data Grouped (eLife functional groups)
        FCDUdf - Filtered Connectomics Data Ungrouped (matched subjects)
        FCDGdf - Filtered Connectomics Data Grouped (matched subjects)

    Connectomics pivoted wide (subject x region_hemi):
        ACDUdf_wide, ACDGdf_wide, FCDUdf_wide, FCDGdf_wide

    Kinematics aggregated to (subject, phase, outcome_group) or (subject, phase, contact):
        AKDdf_agg, FKDdf_agg
        AKDdf_agg_contact, FKDdf_agg_contact

    Outcome / contact proportions:
        AKDdf_prop, FKDdf_prop
        AKDdf_contact_prop, FKDdf_contact_prop
        AKDdf_segment_contact_prop, FKDdf_segment_contact_prop

    Derived single-subject-indexed list:
        matched_subjects - the subjects that appear in both kinematics and connectomics
    """
    # Raw tables
    subjectsdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    kinematicsdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    manual_pelletdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    weightsdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    surgeriesdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    brainsdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    countsdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    counts_groupeddf: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Six base dataframes
    AKDdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    ACDUdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    ACDGdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    FCDUdf: pd.DataFrame = field(default_factory=pd.DataFrame)
    FCDGdf: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Wide connectomics
    ACDUdf_wide: pd.DataFrame = field(default_factory=pd.DataFrame)
    ACDGdf_wide: pd.DataFrame = field(default_factory=pd.DataFrame)
    FCDUdf_wide: pd.DataFrame = field(default_factory=pd.DataFrame)
    FCDGdf_wide: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Aggregated kinematics
    AKDdf_agg: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf_agg: pd.DataFrame = field(default_factory=pd.DataFrame)
    AKDdf_agg_contact: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf_agg_contact: pd.DataFrame = field(default_factory=pd.DataFrame)

    # Proportions
    AKDdf_prop: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf_prop: pd.DataFrame = field(default_factory=pd.DataFrame)
    AKDdf_contact_prop: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf_contact_prop: pd.DataFrame = field(default_factory=pd.DataFrame)
    AKDdf_segment_contact_prop: pd.DataFrame = field(default_factory=pd.DataFrame)
    FKDdf_segment_contact_prop: pd.DataFrame = field(default_factory=pd.DataFrame)

    # List of subjects with both kinematics AND connectomics
    matched_subjects: Tuple[str, ...] = ()

    def AKDdf_agg_contact_flat(self) -> pd.DataFrame:
        """Flatten the MultiIndex on AKDdf_agg_contact so notebooks can filter normally."""
        return self.AKDdf_agg_contact.reset_index()


def _load_raw_tables(engine) -> Dict[str, pd.DataFrame]:
    """Execute the Section 4 + 5 queries and return each resulting DataFrame."""
    out = {
        "subjectsdf": pd.read_sql_query("SELECT * FROM subjects", engine),
        "kinematicsdf": pd.read_sql_query("SELECT * FROM reach_data", engine),
        "manual_pelletdf": pd.read_sql_query("SELECT * FROM pellet_scores", engine),
        "weightsdf": pd.read_sql_query("SELECT * FROM weights", engine),
        "surgeriesdf": pd.read_sql_query("SELECT * FROM surgeries", engine),
        "brainsdf": pd.read_sql_query("SELECT * FROM brain_samples", engine),
    }
    # Join subject_id into the region_counts and elife_region_counts tables (notebook Section 5 trick)
    out["countsdf"] = pd.read_sql_query("""
        SELECT bs.subject_id, rc.*
        FROM region_counts rc
        JOIN brain_samples bs ON rc.brain_sample_id = bs.id
    """, engine)
    out["counts_groupeddf"] = pd.read_sql_query("""
        SELECT bs.subject_id, ec.*
        FROM elife_region_counts ec
        JOIN brain_samples bs ON ec.brain_sample_id = bs.id
    """, engine)
    return out


def _add_outcome_group_column(df: pd.DataFrame) -> pd.DataFrame:
    """Classify every reach into missed / displaced / retrieved per notebook Section 6.

    Does not modify ``df`` in place; returns a copy with the new column.
    """
    df = df.copy()
    df["outcome_group"] = "missed"
    df.loc[df["outcome"].isin(["displaced_sa", "displaced_outside"]), "outcome_group"] = "displaced"
    df.loc[df["outcome"] == "retrieved", "outcome_group"] = "retrieved"
    return df


def _excluded_cohort_mask(subject_ids: pd.Series) -> pd.Series:
    """True for rows whose subject_id belongs to any excluded cohort.

    Matches both short-prefix (CNT_00) and full-prefix (CNT_00_*) forms.
    """
    prefixes = tuple(list(COHORTS_TO_EXCLUDE) + [c + "_" for c in COHORTS_TO_EXCLUDE])
    return subject_ids.str.startswith(prefixes)


def _pick_modes(df: pd.DataFrame, version_cols) -> Dict[str, object]:
    """Return the most common value for each pipeline-version column.

    Used by the notebook to filter to the dominant pipeline run so kinematics
    from different algorithm versions don't get mixed.
    """
    return {col: df[col].mode().iloc[0] for col in version_cols}


def build_base_dataframes(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Produce the six base dataframes from raw-table input. Mirrors notebook cells 11 + 13 + 15."""
    brainsdf = raw["brainsdf"]
    countsdf = raw["countsdf"]
    counts_groupeddf = raw["counts_groupeddf"]
    kinematicsdf = raw["kinematicsdf"]

    # --- Connectomics side ---
    valid_brains = brainsdf[brainsdf["brain_id"].str.contains(IMAGING_PARAMS_MATCH)]["id"]
    ACDUdf = countsdf[countsdf["brain_sample_id"].isin(valid_brains)]
    ACDGdf = counts_groupeddf[counts_groupeddf["brain_sample_id"].isin(valid_brains)]

    # --- Kinematics: filter to dominant pipeline versions (most common across data) ---
    version_cols = [
        "mousereach_version", "segmenter_version",
        "reach_detector_version", "outcome_detector_version",
    ]
    modes = _pick_modes(kinematicsdf, version_cols)
    AKDdf = kinematicsdf[
        (kinematicsdf["mousereach_version"] == modes["mousereach_version"])
        & (kinematicsdf["segmenter_version"] == modes["segmenter_version"])
        & (kinematicsdf["reach_detector_version"] == modes["reach_detector_version"])
        & (kinematicsdf["outcome_detector_version"] == modes["outcome_detector_version"])
    ]

    # --- Matched subjects: those with BOTH kinematics AND connectomics ---
    matched_subjects = ACDUdf["subject_id"][
        ACDUdf["subject_id"].isin(AKDdf["subject_id"].unique())
    ].unique()

    # Filter the "All" dataframes into the "Filtered" (matched) dataframes
    FCDUdf = filter_to_shared(ACDUdf, matched_subjects, "FCDUdf", verbose=False)
    FCDGdf = filter_to_shared(ACDGdf, matched_subjects, "FCDGdf", verbose=False)
    FKDdf = filter_to_shared(AKDdf, matched_subjects, "FKDdf", verbose=False)

    # --- Cohort exclusion + phase restriction on the kinematic dataframes ---
    AKDdf = AKDdf[~_excluded_cohort_mask(AKDdf["subject_id"])]
    FKDdf = FKDdf[~_excluded_cohort_mask(FKDdf["subject_id"])]
    AKDdf = AKDdf[AKDdf["phase_group"].isin(ANALYZABLE_PHASES)]
    FKDdf = FKDdf[FKDdf["phase_group"].isin(ANALYZABLE_PHASES)]

    # --- Outcome grouping ---
    AKDdf = _add_outcome_group_column(AKDdf)
    FKDdf = _add_outcome_group_column(FKDdf)

    return {
        "AKDdf": AKDdf, "FKDdf": FKDdf,
        "ACDUdf": ACDUdf, "ACDGdf": ACDGdf,
        "FCDUdf": FCDUdf, "FCDGdf": FCDGdf,
        "matched_subjects": tuple(matched_subjects),
    }


def build_derived_dataframes(base: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Pivot connectomics to wide, aggregate kinematics, compute proportions."""
    AKDdf, FKDdf = base["AKDdf"], base["FKDdf"]
    ACDUdf, ACDGdf = base["ACDUdf"], base["ACDGdf"]
    FCDUdf, FCDGdf = base["FCDUdf"], base["FCDGdf"]

    # Wide-format connectomics: one row per subject, one column per region_hemi
    ACDUdf_wide = pivot_connectivity(ACDUdf, "ACDUdf_wide", value_col="cell_count", region_col="region_acronym")
    ACDGdf_wide = pivot_connectivity(ACDGdf, "ACDGdf_wide", value_col="cell_count", region_col="group_name")
    FCDUdf_wide = pivot_connectivity(FCDUdf, "FCDUdf_wide", value_col="cell_count", region_col="region_acronym")
    FCDGdf_wide = pivot_connectivity(FCDGdf, "FCDGdf_wide", value_col="cell_count", region_col="group_name")

    # Aggregated kinematics, both outcome-grouped and contact-grouped variants
    AKDdf_agg = aggregate_kinematics(AKDdf, "AKDdf_agg")
    FKDdf_agg = aggregate_kinematics(FKDdf, "FKDdf_agg")
    AKDdf_agg_contact = aggregate_kinematics_by_contact(AKDdf, "AKDdf_agg_contact")
    FKDdf_agg_contact = aggregate_kinematics_by_contact(FKDdf, "FKDdf_agg_contact")

    # Proportions
    AKDdf_prop = compute_outcome_proportions(AKDdf, "AKDdf_prop")
    FKDdf_prop = compute_outcome_proportions(FKDdf, "FKDdf_prop")
    AKDdf_contact_prop = compute_contact_proportions(AKDdf, "AKDdf_contact_prop", group_col="contact_group")
    FKDdf_contact_prop = compute_contact_proportions(FKDdf, "FKDdf_contact_prop", group_col="contact_group")
    AKDdf_segment_contact_prop = compute_contact_proportions(AKDdf, "AKDdf_segment_contact_prop", group_col="segment_contact_group")
    FKDdf_segment_contact_prop = compute_contact_proportions(FKDdf, "FKDdf_segment_contact_prop", group_col="segment_contact_group")

    return {
        "ACDUdf_wide": ACDUdf_wide, "ACDGdf_wide": ACDGdf_wide,
        "FCDUdf_wide": FCDUdf_wide, "FCDGdf_wide": FCDGdf_wide,
        "AKDdf_agg": AKDdf_agg, "FKDdf_agg": FKDdf_agg,
        "AKDdf_agg_contact": AKDdf_agg_contact, "FKDdf_agg_contact": FKDdf_agg_contact,
        "AKDdf_prop": AKDdf_prop, "FKDdf_prop": FKDdf_prop,
        "AKDdf_contact_prop": AKDdf_contact_prop, "FKDdf_contact_prop": FKDdf_contact_prop,
        "AKDdf_segment_contact_prop": AKDdf_segment_contact_prop,
        "FKDdf_segment_contact_prop": FKDdf_segment_contact_prop,
    }


def _write_cache(data: LoadedData, cache_dir: Path) -> None:
    """Write every table in ``data`` to a parquet file in ``cache_dir``.

    Parquet preserves dtypes and is ~10x smaller than CSV. Indexes are
    preserved so MultiIndex aggregations round-trip.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    for name in _CACHE_TABLES:
        df = getattr(data, name)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            continue
        # reset_index then mark the original index names so read-back can restore them.
        df_to_save = df.reset_index() if df.index.names != [None] else df
        index_names = df.index.names if df.index.names != [None] else []
        df_to_save.to_parquet(cache_dir / f"{name}.parquet", index=False)
        # Sidecar json with the original index names so _read_cache can restore the MultiIndex.
        (cache_dir / f"{name}.index.txt").write_text(",".join("" if n is None else n for n in index_names))
    # Store matched_subjects as a one-column parquet so it survives roundtrip
    pd.DataFrame({"subject_id": list(data.matched_subjects)}).to_parquet(
        cache_dir / "matched_subjects.parquet", index=False
    )


def _read_cache(cache_dir: Path) -> LoadedData:
    """Read every cached parquet back into a LoadedData."""
    data = LoadedData()
    for name in _CACHE_TABLES:
        pq_path = cache_dir / f"{name}.parquet"
        idx_path = cache_dir / f"{name}.index.txt"
        if not pq_path.exists():
            continue
        df = pd.read_parquet(pq_path)
        if idx_path.exists():
            idx_names = [n for n in idx_path.read_text().split(",") if n]
            if idx_names:
                df = df.set_index(idx_names)
        setattr(data, name, df)
    matched_path = cache_dir / "matched_subjects.parquet"
    if matched_path.exists():
        data.matched_subjects = tuple(pd.read_parquet(matched_path)["subject_id"].tolist())
    return data


def cache_exists(cache_dir: Optional[Path] = None) -> bool:
    """True if a populated cache directory exists at ``cache_dir`` (or the default)."""
    cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR
    return cache_dir.exists() and any(cache_dir.glob("*.parquet"))


def load_all(
    db_path: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
    use_cache: bool = True,
    write_cache: bool = True,
    verbose: bool = True,
    use_synthetic: bool = False,
    synthetic_n: int = 30,
    synthetic_seed: int = 42,
    synthetic_conn_noise: float = 0.3,
    synthetic_kine_noise: float = 0.10,
) -> LoadedData:
    """Build every dataframe the analysis notebooks need.

    Args:
        db_path: Override the DB location. Defaults to ``config.get_db_path()``.
        cache_dir: Override the cache directory. Defaults to ``config.CACHE_DIR``.
        use_cache: If True and the cache is populated, skip the DB entirely and
            read from cache.
        write_cache: If True, write the cache after rebuilding from the DB.
        verbose: Print one-line status messages (ASCII-safe per mousedb).
        use_synthetic: If True, load real data then pass through
            :func:`helpers.synthetic.synthesize_cohort` to produce a
            larger synthetic cohort with known ground-truth cluster
            structure. For pipeline validation while real N is still
            small. Subject IDs are prefixed ``SYN_``.
        synthetic_n: Number of synthetic subjects when ``use_synthetic``.
        synthetic_seed: RNG seed when ``use_synthetic``.
        synthetic_conn_noise: Connectivity perturbation as fraction of
            cross-subject std.
        synthetic_kine_noise: Kinematic perturbation as fraction of
            per-feature std.

    Returns:
        A populated :class:`LoadedData`.
    """
    cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR

    if use_cache and cache_exists(cache_dir):
        if verbose:
            print(f"Loading from cache: {cache_dir}")
        data = _read_cache(cache_dir)
    else:
        db_path = Path(db_path) if db_path else get_db_path()
        if not db_path.exists():
            raise FileNotFoundError(
                f"connectome.db not found at {db_path}. Either place it there, "
                "set the CFS_ANALYSIS_DB environment variable, or run with "
                "use_cache=True if a cache exists."
            )

        if verbose:
            print(f"Loading from database: {db_path}")
        engine = create_engine(f"sqlite:///{db_path}")

        raw = _load_raw_tables(engine)
        base = build_base_dataframes(raw)
        derived = build_derived_dataframes({
            "AKDdf": base["AKDdf"], "FKDdf": base["FKDdf"],
            "ACDUdf": base["ACDUdf"], "ACDGdf": base["ACDGdf"],
            "FCDUdf": base["FCDUdf"], "FCDGdf": base["FCDGdf"],
        })

        data = LoadedData(
            **raw,
            AKDdf=base["AKDdf"], FKDdf=base["FKDdf"],
            ACDUdf=base["ACDUdf"], ACDGdf=base["ACDGdf"],
            FCDUdf=base["FCDUdf"], FCDGdf=base["FCDGdf"],
            matched_subjects=base["matched_subjects"],
            **derived,
        )

        if write_cache:
            if verbose:
                print(f"Writing cache: {cache_dir}")
            _write_cache(data, cache_dir)

    # Synthetic-cohort augmentation happens AFTER real data is fully built,
    # so the synthesizer can draw from the real prototypes. The synthetic
    # LoadedData replaces the real one in-place here; callers never see
    # both simultaneously via this entry point.
    if use_synthetic:
        from .helpers.synthetic import synthesize_cohort
        if verbose:
            print("Synthetic-cohort mode requested; cloning real data.")
        data = synthesize_cohort(
            data,
            n_synthetic=synthetic_n,
            conn_noise_scale=synthetic_conn_noise,
            kine_noise_scale=synthetic_kine_noise,
            seed=synthetic_seed,
            verbose=verbose,
        )

    return data
