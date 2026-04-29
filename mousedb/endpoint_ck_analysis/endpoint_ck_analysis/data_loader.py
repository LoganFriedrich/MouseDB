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
    >>> from endpoint_ck_analysis.data_loader import load_all
    >>> data = load_all()
    >>> data.AKDdf.shape           # raw reaches in analyzable phases
    >>> data.FCDGdf_wide.shape     # eLife-grouped connectivity, matched subjects only
"""
from __future__ import annotations  # postpone-annotation evaluation

from dataclasses import dataclass, field  # dataclass: auto-generated __init__/__repr__; field: per-attribute config (default_factory etc.)
from pathlib import Path  # pathlib: object-oriented filesystem paths
from typing import Dict, Optional, Tuple  # type-hint primitives

import numpy as np  # numpy: arrays + math (used downstream by helpers)
import pandas as pd  # pandas: dataframe library
from sqlalchemy import create_engine  # SQLAlchemy: lower-level DB engine; pandas uses it under the hood for read_sql_query

from .config import (                                                              # central config constants pulled in below
    ANALYZABLE_PHASES,                                                             # tuple of phase labels we keep
    CACHE_DIR,                                                                     # default parquet cache directory
    COHORTS_TO_EXCLUDE,                                                            # cohort prefixes to drop
    IMAGING_PARAMS_MATCH,                                                          # imaging-protocol substring required for valid brains
    get_db_path,                                                                   # function that resolves the DB location
)
from .helpers.connectivity import pivot_connectivity                                # long->wide pivot for connectomics
from .helpers.filters import filter_to_shared                                       # subject-intersection filter
from .helpers.kinematics import (                                                   # kinematic aggregations + proportion calculators
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
    out = {                                                                        # build a dict of {table_name: dataframe} so callers can lookup tables by name
        "subjectsdf": pd.read_sql_query("SELECT * FROM subjects", engine),         # pandas read_sql_query: runs SQL and returns the result as a DataFrame
        "kinematicsdf": pd.read_sql_query("SELECT * FROM reach_data", engine),     # raw per-reach kinematic data
        "manual_pelletdf": pd.read_sql_query("SELECT * FROM pellet_scores", engine),  # human-scored pellet outcomes (validation truth)
        "weightsdf": pd.read_sql_query("SELECT * FROM weights", engine),           # body-weight measurements over time
        "surgeriesdf": pd.read_sql_query("SELECT * FROM surgeries", engine),       # injury / surgery records
        "brainsdf": pd.read_sql_query("SELECT * FROM brain_samples", engine),      # brain-imaging metadata (used to filter to compatible imaging params)
    }
    # Join subject_id into the region_counts and elife_region_counts tables (notebook Section 5 trick)
    out["countsdf"] = pd.read_sql_query("""
        SELECT bs.subject_id, rc.*
        FROM region_counts rc
        JOIN brain_samples bs ON rc.brain_sample_id = bs.id
    """, engine)                                                                    # ungrouped region counts joined with subject_id from brain_samples; bs.* would be huge so we only grab subject_id
    out["counts_groupeddf"] = pd.read_sql_query("""
        SELECT bs.subject_id, ec.*
        FROM elife_region_counts ec
        JOIN brain_samples bs ON ec.brain_sample_id = bs.id
    """, engine)                                                                    # eLife-grouped region counts with subject_id attached
    return out


def _add_outcome_group_column(df: pd.DataFrame) -> pd.DataFrame:
    """Classify every reach into missed / displaced / retrieved per notebook Section 6.

    Does not modify ``df`` in place; returns a copy with the new column.
    """
    df = df.copy()                                                                  # copy so we don't mutate the caller's dataframe
    df["outcome_group"] = "missed"                                                  # default everyone to 'missed'; the next two lines overwrite for the other classes
    df.loc[df["outcome"].isin(["displaced_sa", "displaced_outside"]), "outcome_group"] = "displaced"  # rows whose outcome is one of the displaced variants -> 'displaced'
    df.loc[df["outcome"] == "retrieved", "outcome_group"] = "retrieved"             # successful retrievals
    return df


def _excluded_cohort_mask(subject_ids: pd.Series) -> pd.Series:
    """True for rows whose subject_id belongs to any excluded cohort.

    Matches both short-prefix (CNT_00) and full-prefix (CNT_00_*) forms.
    """
    prefixes = tuple(list(COHORTS_TO_EXCLUDE) + [c + "_" for c in COHORTS_TO_EXCLUDE])  # build a flat tuple of all prefix variants ('CNT_00', 'CNT_00_', ...) so str.startswith covers both
    return subject_ids.str.startswith(prefixes)                                     # vectorized: returns boolean Series, True where subject_id starts with any prefix


def _pick_modes(df: pd.DataFrame, version_cols) -> Dict[str, object]:
    """Return the most common value for each pipeline-version column.

    Used by the notebook to filter to the dominant pipeline run so kinematics
    from different algorithm versions don't get mixed.
    """
    return {col: df[col].mode().iloc[0] for col in version_cols}                   # dict comprehension: for each version column, .mode() returns the most-common value(s) Series; .iloc[0] grabs the first (in case of ties)


def build_base_dataframes(raw: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Produce the six base dataframes from raw-table input. Mirrors notebook cells 11 + 13 + 15."""
    brainsdf = raw["brainsdf"]                                                     # unpack each raw table by name for readable downstream code
    countsdf = raw["countsdf"]
    counts_groupeddf = raw["counts_groupeddf"]
    kinematicsdf = raw["kinematicsdf"]

    # --- Connectomics side ---
    valid_brains = brainsdf[brainsdf["brain_id"].str.contains(IMAGING_PARAMS_MATCH)]["id"]  # boolean filter brain_id contains the imaging-protocol substring; pull the 'id' column for joining
    ACDUdf = countsdf[countsdf["brain_sample_id"].isin(valid_brains)]              # restrict ungrouped counts to brains with valid imaging params
    ACDGdf = counts_groupeddf[counts_groupeddf["brain_sample_id"].isin(valid_brains)]  # restrict grouped counts the same way

    # --- Kinematics: filter to dominant pipeline versions (most common across data) ---
    version_cols = [                                                                # list of the four pipeline-version columns; keep only rows where ALL match the mode
        "mousereach_version", "segmenter_version",
        "reach_detector_version", "outcome_detector_version",
    ]
    modes = _pick_modes(kinematicsdf, version_cols)                                # {column: most-common-value} per version column
    AKDdf = kinematicsdf[                                                          # boolean indexing: AND together one equality check per version column
        (kinematicsdf["mousereach_version"] == modes["mousereach_version"])
        & (kinematicsdf["segmenter_version"] == modes["segmenter_version"])
        & (kinematicsdf["reach_detector_version"] == modes["reach_detector_version"])
        & (kinematicsdf["outcome_detector_version"] == modes["outcome_detector_version"])
    ]

    # --- Matched subjects: those with BOTH kinematics AND connectomics ---
    matched_subjects = ACDUdf["subject_id"][                                       # take subjects from the connectomics side that also appear in kinematics
        ACDUdf["subject_id"].isin(AKDdf["subject_id"].unique())                    # .unique() returns ndarray of distinct kinematic subjects; .isin returns boolean Series
    ].unique()                                                                      # collapse to distinct matched subject IDs

    # Filter the "All" dataframes into the "Filtered" (matched) dataframes
    FCDUdf = filter_to_shared(ACDUdf, matched_subjects, "FCDUdf", verbose=False)   # Filtered Connectivity Data Ungrouped: matched subjects only
    FCDGdf = filter_to_shared(ACDGdf, matched_subjects, "FCDGdf", verbose=False)   # Filtered Connectivity Data Grouped (eLife)
    FKDdf = filter_to_shared(AKDdf, matched_subjects, "FKDdf", verbose=False)      # Filtered Kinematic Data: matched subjects only

    # --- Cohort exclusion + phase restriction on the kinematic dataframes ---
    AKDdf = AKDdf[~_excluded_cohort_mask(AKDdf["subject_id"])]                     # ~ negates the boolean mask: keep rows NOT in excluded cohorts
    FKDdf = FKDdf[~_excluded_cohort_mask(FKDdf["subject_id"])]                     # same for filtered kinematics
    AKDdf = AKDdf[AKDdf["phase_group"].isin(ANALYZABLE_PHASES)]                    # keep only rows whose phase is one of the analyzable phases
    FKDdf = FKDdf[FKDdf["phase_group"].isin(ANALYZABLE_PHASES)]                    # same for filtered

    # --- Outcome grouping ---
    AKDdf = _add_outcome_group_column(AKDdf)                                       # add 'outcome_group' (missed/displaced/retrieved) computed from raw 'outcome'
    FKDdf = _add_outcome_group_column(FKDdf)

    return {                                                                        # bundle results into a dict for the load_all caller to spread into LoadedData
        "AKDdf": AKDdf, "FKDdf": FKDdf,
        "ACDUdf": ACDUdf, "ACDGdf": ACDGdf,
        "FCDUdf": FCDUdf, "FCDGdf": FCDGdf,
        "matched_subjects": tuple(matched_subjects),                               # tuple is the canonical immutable container for the LoadedData field
    }


def build_derived_dataframes(base: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """Pivot connectomics to wide, aggregate kinematics, compute proportions."""
    AKDdf, FKDdf = base["AKDdf"], base["FKDdf"]                                    # tuple-unpack: each name binds to the corresponding dict value
    ACDUdf, ACDGdf = base["ACDUdf"], base["ACDGdf"]
    FCDUdf, FCDGdf = base["FCDUdf"], base["FCDGdf"]

    # Wide-format connectomics: one row per subject, one column per region_hemi
    ACDUdf_wide = pivot_connectivity(ACDUdf, "ACDUdf_wide", value_col="cell_count", region_col="region_acronym")  # all-subjects ungrouped pivot; columns are region_hemi (e.g., 'GRN_left')
    ACDGdf_wide = pivot_connectivity(ACDGdf, "ACDGdf_wide", value_col="cell_count", region_col="group_name")      # all-subjects grouped pivot; columns are eLife group_hemi (e.g., 'Corticospinal_both')
    FCDUdf_wide = pivot_connectivity(FCDUdf, "FCDUdf_wide", value_col="cell_count", region_col="region_acronym")  # filtered (matched-subjects) ungrouped pivot
    FCDGdf_wide = pivot_connectivity(FCDGdf, "FCDGdf_wide", value_col="cell_count", region_col="group_name")      # filtered (matched-subjects) grouped pivot

    # Aggregated kinematics, both outcome-grouped and contact-grouped variants
    AKDdf_agg = aggregate_kinematics(AKDdf, "AKDdf_agg")                            # mean/std/median/q25/q75 per (subject, phase, outcome_group)
    FKDdf_agg = aggregate_kinematics(FKDdf, "FKDdf_agg")                            # same for filtered subjects
    AKDdf_agg_contact = aggregate_kinematics_by_contact(AKDdf, "AKDdf_agg_contact")  # same stats but grouped by (subject, phase, contact_group)
    FKDdf_agg_contact = aggregate_kinematics_by_contact(FKDdf, "FKDdf_agg_contact")  # filtered version

    # Proportions
    AKDdf_prop = compute_outcome_proportions(AKDdf, "AKDdf_prop")                  # share of reaches per outcome bucket per (subject, phase)
    FKDdf_prop = compute_outcome_proportions(FKDdf, "FKDdf_prop")
    AKDdf_contact_prop = compute_contact_proportions(AKDdf, "AKDdf_contact_prop", group_col="contact_group")  # per-reach contact proportions
    FKDdf_contact_prop = compute_contact_proportions(FKDdf, "FKDdf_contact_prop", group_col="contact_group")
    AKDdf_segment_contact_prop = compute_contact_proportions(AKDdf, "AKDdf_segment_contact_prop", group_col="segment_contact_group")  # per-segment contact proportions
    FKDdf_segment_contact_prop = compute_contact_proportions(FKDdf, "FKDdf_segment_contact_prop", group_col="segment_contact_group")

    return {                                                                        # bundle outputs into a single dict for unpacking into LoadedData
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
    cache_dir.mkdir(parents=True, exist_ok=True)                                   # create cache dir if missing; parents=True creates intermediates; exist_ok=True is a no-op if already there
    for name in _CACHE_TABLES:                                                     # iterate the canonical list of table names
        df = getattr(data, name)                                                   # getattr: dynamic attribute access on LoadedData by string name
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:             # skip None / non-dataframe / empty
            continue
        # reset_index then mark the original index names so read-back can restore them.
        df_to_save = df.reset_index() if df.index.names != [None] else df          # if there's a real index (MultiIndex or named), promote it to columns; else save as-is
        index_names = df.index.names if df.index.names != [None] else []           # remember the index names so _read_cache can re-set them
        df_to_save.to_parquet(cache_dir / f"{name}.parquet", index=False)          # write parquet; index=False because we already promoted any index to columns
        # Sidecar json with the original index names so _read_cache can restore the MultiIndex.
        (cache_dir / f"{name}.index.txt").write_text(",".join("" if n is None else n for n in index_names))  # comma-joined names; empty string represents an unnamed level
    # Store matched_subjects as a one-column parquet so it survives roundtrip
    pd.DataFrame({"subject_id": list(data.matched_subjects)}).to_parquet(           # wrap the tuple in a 1-col DataFrame because parquet can't store bare lists
        cache_dir / "matched_subjects.parquet", index=False
    )


def _read_cache(cache_dir: Path) -> LoadedData:
    """Read every cached parquet back into a LoadedData."""
    data = LoadedData()                                                            # construct empty LoadedData; we'll fill fields one by one via setattr
    for name in _CACHE_TABLES:                                                     # iterate the canonical list of table names
        pq_path = cache_dir / f"{name}.parquet"                                    # parquet file for this table
        idx_path = cache_dir / f"{name}.index.txt"                                 # sidecar with original index names
        if not pq_path.exists():                                                   # missing parquet -> skip (table wasn't cached)
            continue
        df = pd.read_parquet(pq_path)                                              # read the parquet; returns a flat DataFrame
        if idx_path.exists():                                                      # if we wrote an index sidecar, restore the index from it
            idx_names = [n for n in idx_path.read_text().split(",") if n]          # split comma-joined names; filter out empties
            if idx_names:                                                          # only set_index if there are real names to set
                df = df.set_index(idx_names)                                       # promote those columns back into a (possibly multi) index
        setattr(data, name, df)                                                    # store on LoadedData by attribute name
    matched_path = cache_dir / "matched_subjects.parquet"                          # special-case: matched_subjects is a tuple, not a DataFrame
    if matched_path.exists():
        data.matched_subjects = tuple(pd.read_parquet(matched_path)["subject_id"].tolist())  # read 1-col DataFrame; pull the column; convert to tuple
    return data


def cache_exists(cache_dir: Optional[Path] = None) -> bool:
    """True if a populated cache directory exists at ``cache_dir`` (or the default)."""
    cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR                        # ternary: caller-supplied path or default
    return cache_dir.exists() and any(cache_dir.glob("*.parquet"))                 # both: the directory exists AND it contains at least one parquet file


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
    cache_dir = Path(cache_dir) if cache_dir else CACHE_DIR                        # caller-supplied or default cache directory

    if use_cache and cache_exists(cache_dir):                                      # cache hit: skip the DB entirely
        if verbose:                                                                # caller wants console output
            print(f"Loading from cache: {cache_dir}")
        data = _read_cache(cache_dir)                                              # reconstruct LoadedData from parquet files
    else:                                                                           # cache miss: rebuild from the DB
        db_path = Path(db_path) if db_path else get_db_path()                      # caller-supplied DB path or the resolved default (env var or bundled copy)
        if not db_path.exists():                                                   # DB missing -> fail fast with an actionable error
            raise FileNotFoundError(
                f"connectome.db not found at {db_path}. Either place it there, "
                "set the ENDPOINT_CK_ANALYSIS_DB environment variable, or run with "
                "use_cache=True if a cache exists."
            )

        if verbose:
            print(f"Loading from database: {db_path}")
        engine = create_engine(f"sqlite:///{db_path}")                             # SQLAlchemy engine for sqlite; URL form is "sqlite:///<path>" (3 slashes for relative, 4 for absolute on Windows)

        raw = _load_raw_tables(engine)                                             # dict of {table_name: DataFrame} for the raw tables
        base = build_base_dataframes(raw)                                          # filter/tag step -> the six base dataframes + matched_subjects
        derived = build_derived_dataframes({                                       # pivot/aggregate step -> wide connectomics + aggregated kinematics + proportions
            "AKDdf": base["AKDdf"], "FKDdf": base["FKDdf"],
            "ACDUdf": base["ACDUdf"], "ACDGdf": base["ACDGdf"],
            "FCDUdf": base["FCDUdf"], "FCDGdf": base["FCDGdf"],
        })

        data = LoadedData(                                                         # build the dataclass; ** spreads each dict into keyword args
            **raw,
            AKDdf=base["AKDdf"], FKDdf=base["FKDdf"],
            ACDUdf=base["ACDUdf"], ACDGdf=base["ACDGdf"],
            FCDUdf=base["FCDUdf"], FCDGdf=base["FCDGdf"],
            matched_subjects=base["matched_subjects"],
            **derived,
        )

        if write_cache:                                                            # opt-in: write parquet cache so subsequent runs hit cache instead of re-building
            if verbose:
                print(f"Writing cache: {cache_dir}")
            _write_cache(data, cache_dir)

    # Synthetic-cohort augmentation happens AFTER real data is fully built,
    # so the synthesizer can draw from the real prototypes. The synthetic
    # LoadedData replaces the real one in-place here; callers never see
    # both simultaneously via this entry point.
    if use_synthetic:                                                              # opt-in: replace real data with synthetic cohort built from real prototypes
        from .helpers.synthetic import synthesize_cohort                           # local import to avoid circular import at module load
        if verbose:
            print("Synthetic-cohort mode requested; cloning real data.")
        data = synthesize_cohort(                                                  # rebind 'data' so the rest of the pipeline uses synthetic
            data,
            n_synthetic=synthetic_n,
            conn_noise_scale=synthetic_conn_noise,
            kine_noise_scale=synthetic_kine_noise,
            seed=synthetic_seed,
            verbose=verbose,
        )

    return data
