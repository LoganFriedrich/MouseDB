"""Synthetic cohort generator for pipeline validation at realistic N.

At the time of release there are 4 real matched subjects. Every statistical
tool in the pipeline (clustering, PLS, LMM with interactions, permutation
validation) is designed for N well above that. Rather than wait for more
subjects before testing whether the tools actually work, we can generate
a synthetic cohort by cloning the real subjects with biologically-plausible
perturbation. The synthetic cohort is explicitly labeled (subject IDs
start with ``SYN_``) so a reader can never mistake synthetic results for
real results.

Design:

- **Prototype-and-perturb**. Each synthetic subject is assigned to one of
  the real subjects (round-robin, so each prototype produces a roughly
  equal number of clones). The synthetic subject's connectivity row is
  the prototype's row + Gaussian noise scaled to a fraction of the
  cross-subject std, clipped non-negative. The synthetic subject's
  reach-level kinematics are the prototype's reaches, column-perturbed
  with Gaussian noise scaled to a fraction of the per-feature std.
- **Known ground truth**. Because every synthetic subject is a clone of
  a known prototype, there is a ground-truth clustering assignment
  (prototype label). Clustering tools should recover it when the noise
  scale is moderate; that's a test of whether the pipeline works.
- **Reproducibility**. RNG is seeded by an argument so results are
  deterministic.
- **Traceability**. Synthetic IDs encode the prototype they came from:
  ``SYN_007_from_CNT_02_08`` etc. ``prototype_map()`` extracts the map.

Limitations:

- Within-prototype correlations between regions are preserved trivially
  (by cloning), but cross-subject correlation structure is NOT modeled.
  Good enough for pipeline testing, not good enough for scientific claims.
- Session / day structure is cloned verbatim from the prototype, so
  synthetic subjects all have the same session_date strings as their
  prototype. LMM with session random effects still works because
  session_date x subject_id uniquely identifies a group, but the dates
  are not realistic.
"""
from __future__ import annotations  # postpone-annotation evaluation; lets type hints reference forward names

import copy  # copy: standard library; deep/shallow copy primitives
from dataclasses import dataclass  # dataclass decorator (unused here but kept for future expansion)
from typing import Dict, List, Optional, Tuple  # type-hint primitives

import numpy as np  # numpy: arrays + RNG + math
import pandas as pd  # pandas: dataframe library

from ..config import METADATA_COLS  # set of metadata column names to skip when perturbing kinematic features


# Reasonable defaults for pipeline-testing. Loud enough that clusters
# are detectable; quiet enough that clusters are also clearly distinct.
DEFAULT_N_SYNTHETIC = 30                                                          # 30 synthetic subjects: enough for k-means/Ward to find 4 clusters comfortably
DEFAULT_CONN_NOISE_SCALE = 0.3                                                    # connectivity noise as a fraction of cross-subject std (30%)
DEFAULT_KINE_NOISE_SCALE = 0.10                                                   # kinematic noise as a fraction of per-feature std (10%); kinematics are noisier already so we add less
DEFAULT_SEED = 42                                                                 # arbitrary but stable RNG seed for reproducibility


def _synth_id(i: int, prototype: str) -> str:
    """Consistent synthetic subject ID encoding prototype lineage."""
    return f"SYN_{i:03d}_from_{prototype}"                                        # f-string with :03d zero-pads to 3 digits, e.g. "SYN_007_from_CNT_02_08"


def prototype_map(subject_ids) -> pd.Series:
    """Extract prototype (real) subject ID from each synthetic subject ID.

    Accepts any iterable of strings. Returns a Series indexed by subject
    ID with the prototype string as value. Non-synthetic IDs map to
    themselves (so real-data paths keep working).
    """
    out = {}                                                                       # accumulator: {subject_id: prototype_id}
    for sid in subject_ids:                                                        # iterate caller-provided IDs
        if sid.startswith("SYN_") and "_from_" in sid:                             # synthetic ID convention check
            out[sid] = sid.split("_from_", 1)[1]                                   # split once on '_from_'; keep the right side -> prototype name
        else:                                                                      # real subject (or anything not matching the convention)
            out[sid] = sid                                                         # map to itself so downstream code uniformly treats it
    return pd.Series(out, name="prototype")                                        # name='prototype' so column survives reset_index/merge


def _clone_conn_row(proto_row: pd.Series, rng: np.random.Generator,
                    noise_scale: float, reference_std: pd.Series) -> pd.Series:
    """Perturb one prototype's connectivity vector."""
    noise = rng.normal(0, noise_scale * reference_std.values, size=len(proto_row))  # Gaussian noise per region; std scales with the cross-subject std for that region
    new = (proto_row.values + noise).clip(min=0)                                   # add noise then clamp negatives to 0 (cell counts can't be negative)
    return pd.Series(new, index=proto_row.index)                                   # rebuild as Series so column labels are preserved


def _clone_kine_rows(proto_rows: pd.DataFrame, rng: np.random.Generator,
                     noise_scale: float, kine_cols: List[str]) -> pd.DataFrame:
    """Copy a prototype's reach rows and perturb every numeric kinematic column."""
    clone = proto_rows.copy()                                                      # copy so we don't mutate the original real dataframe
    for col in kine_cols:                                                          # iterate kinematic columns
        if col not in clone.columns:                                               # column not present in this dataframe; skip
            continue
        col_std = clone[col].std()                                                 # per-column std; basis for noise scale
        if not np.isfinite(col_std) or col_std == 0:                               # NaN / inf / zero std -> no noise to scale by; skip
            continue
        clone[col] = clone[col].astype(float) + rng.normal(                        # in-place add Gaussian noise; astype(float) avoids int-truncation surprises
            0, noise_scale * col_std, size=len(clone)                              # per-row independent noise; std scales with this column's std
        )
    return clone


def _numeric_kinematic_cols(df: pd.DataFrame) -> List[str]:
    """Return numeric, non-metadata column names suitable for perturbation."""
    numeric = df.select_dtypes(include="number").columns.tolist()                  # all numeric columns
    return [c for c in numeric if c not in METADATA_COLS]                          # filter out bookkeeping (subject_id IDs, session_id, etc.)


def synthesize_cohort(
    real_data,
    n_synthetic: int = DEFAULT_N_SYNTHETIC,
    conn_noise_scale: float = DEFAULT_CONN_NOISE_SCALE,
    kine_noise_scale: float = DEFAULT_KINE_NOISE_SCALE,
    seed: int = DEFAULT_SEED,
    verbose: bool = True,
):
    """Return a LoadedData populated with synthetic subjects cloned from real ones.

    Args:
        real_data: Populated :class:`LoadedData` from ``load_all()``.
        n_synthetic: Number of synthetic subjects to generate.
        conn_noise_scale: Connectivity noise as a fraction of cross-subject std.
        kine_noise_scale: Kinematic noise as a fraction of per-feature std.
        seed: RNG seed for reproducibility.
        verbose: Print a short status summary.

    Returns:
        A fresh :class:`LoadedData` where every dataframe is synthetic. Raw
        tables (subjectsdf, surgeriesdf, etc.) are carried over unchanged
        because they are only used for reference, not analysis.
    """
    from ..data_loader import LoadedData                                           # local import to break a circular dependency (data_loader imports from helpers)
    from .kinematics import (                                                      # local import to avoid loading these at module import time
        aggregate_kinematics, aggregate_kinematics_by_contact,
        compute_contact_proportions, compute_outcome_proportions,
    )
    from .connectivity import pivot_connectivity                                   # local import for the same reason

    rng = np.random.default_rng(seed)                                              # numpy's modern RNG; deterministic given seed
    real_subjects = list(real_data.matched_subjects)                               # list of real matched subject IDs to use as prototypes
    if not real_subjects:                                                          # no real subjects -> nothing to clone
        raise ValueError("Real data has no matched subjects; cannot synthesize.")

    # -------------------------------------------------------------------
    # Assign prototypes (round-robin) and mint synthetic subject IDs.
    # -------------------------------------------------------------------
    prototypes = [real_subjects[i % len(real_subjects)] for i in range(n_synthetic)]  # round-robin: i=0->proto0, i=1->proto1, ..., i=N->proto0; balances clones per prototype
    synth_ids = [_synth_id(i, proto) for i, proto in enumerate(prototypes)]        # mint synthetic IDs encoding lineage

    # -------------------------------------------------------------------
    # Connectivity: clone wide-format rows with noise.
    # -------------------------------------------------------------------
    conn_wide_grouped = real_data.FCDGdf_wide                                      # filtered grouped (eLife) connectivity, wide format
    conn_wide_ungrouped = real_data.FCDUdf_wide                                    # filtered ungrouped (atomic-region) connectivity
    if conn_wide_grouped.empty:                                                    # required input
        raise ValueError("FCDGdf_wide is empty in real_data; nothing to clone.")

    conn_grouped_std = conn_wide_grouped.std(axis=0).replace(0, conn_wide_grouped.std().median() or 1.0).fillna(1.0)  # per-column cross-subject std; replace 0-std columns with the median std (or 1.0 fallback) so noise is non-zero
    conn_ungrouped_std = conn_wide_ungrouped.std(axis=0).replace(0, conn_wide_ungrouped.std().median() or 1.0).fillna(1.0) if not conn_wide_ungrouped.empty else None  # same for ungrouped, if available

    synth_FCDGdf_wide_rows = []                                                    # accumulator for grouped synthetic rows
    synth_FCDUdf_wide_rows = []                                                    # accumulator for ungrouped synthetic rows
    for sid, proto in zip(synth_ids, prototypes):                                  # parallel iterate synthetic_id and its prototype
        synth_FCDGdf_wide_rows.append(
            _clone_conn_row(conn_wide_grouped.loc[proto], rng, conn_noise_scale, conn_grouped_std).rename(sid)  # clone grouped row; .rename(sid) gives the Series the synthetic ID as its name (becomes index after DataFrame construction)
        )
        if conn_wide_ungrouped is not None and not conn_wide_ungrouped.empty:      # only clone ungrouped if it exists in the real data
            synth_FCDUdf_wide_rows.append(
                _clone_conn_row(conn_wide_ungrouped.loc[proto], rng, conn_noise_scale, conn_ungrouped_std).rename(sid)
            )
    synth_FCDGdf_wide = pd.DataFrame(synth_FCDGdf_wide_rows)                       # stack rows into a DataFrame; row labels come from each Series' .name
    synth_FCDGdf_wide.index.name = "subject_id"                                    # make index name explicit so reset_index produces a 'subject_id' column
    synth_FCDUdf_wide = (                                                           # ternary: build DataFrame if rows accumulated, else empty
        pd.DataFrame(synth_FCDUdf_wide_rows)
        if synth_FCDUdf_wide_rows else pd.DataFrame()
    )
    if not synth_FCDUdf_wide.empty:                                                # only label the index if the dataframe is non-empty (avoids spurious column name on empty)
        synth_FCDUdf_wide.index.name = "subject_id"

    # -------------------------------------------------------------------
    # Long-format connectomics (ACDGdf / FCDGdf etc.). Build by melting
    # the synthetic wide frames back to the long schema the notebooks
    # expect (subject_id, group_name/region_acronym, hemisphere, cell_count).
    # -------------------------------------------------------------------
    def _melt_wide(wide: pd.DataFrame, region_key: str) -> pd.DataFrame:
        if wide.empty:                                                             # nothing to melt
            return pd.DataFrame(columns=["subject_id", region_key, "hemisphere", "cell_count"])
        long = wide.reset_index().melt(                                            # reset_index brings subject_id back as a column; melt converts wide -> long
            id_vars="subject_id", var_name="region_hemi", value_name="cell_count"  # id_vars: keep these as-is; var_name: name for the variable column; value_name: name for the value column
        )
        # Split the region_hemi column back into region and hemisphere pieces.
        parts = long["region_hemi"].str.rsplit("_", n=1, expand=True)              # vectorized rsplit; n=1 splits at most once; expand=True returns a DataFrame
        long[region_key] = parts[0]                                                # left side: region acronym (or group name)
        long["hemisphere"] = parts[1]                                              # right side: hemisphere
        return long.drop(columns="region_hemi")                                    # drop the now-redundant combined column

    synth_ACDGdf = _melt_wide(synth_FCDGdf_wide, "group_name")                     # all-counts grouped: melt the synthetic wide frame to long
    synth_ACDUdf = _melt_wide(synth_FCDUdf_wide, "region_acronym") if not synth_FCDUdf_wide.empty else pd.DataFrame()  # all-counts ungrouped, only if we built one
    # In the synthetic world every subject has connectivity, so FCDGdf == ACDGdf.
    synth_FCDGdf = synth_ACDGdf.copy()                                             # filtered = all in synthetic world (every synthetic subject has connectivity by construction)
    synth_FCDUdf = synth_ACDUdf.copy()                                             # same for ungrouped

    # -------------------------------------------------------------------
    # Kinematics: clone reach rows per synthetic subject, perturb numeric
    # kinematic columns in place.
    # -------------------------------------------------------------------
    kine_cols = _numeric_kinematic_cols(real_data.AKDdf)                           # which columns count as kinematic for perturbation
    kine_pieces = []                                                               # accumulator for per-synthetic-subject reach DataFrames
    for sid, proto in zip(synth_ids, prototypes):                                  # parallel iterate
        proto_reaches = real_data.AKDdf[real_data.AKDdf["subject_id"] == proto]    # all reaches for this prototype
        clone = _clone_kine_rows(proto_reaches, rng, kine_noise_scale, kine_cols)  # noisy clone of those reaches
        clone["subject_id"] = sid                                                  # overwrite subject_id with the synthetic ID
        kine_pieces.append(clone)
    synth_AKDdf = pd.concat(kine_pieces, ignore_index=True) if kine_pieces else pd.DataFrame()  # stack all per-subject pieces; ignore_index resets the row labels
    # Every synthetic subject has connectivity by construction, so FKDdf == AKDdf.
    synth_FKDdf = synth_AKDdf.copy()                                               # filtered kinematics = all kinematics in synthetic world

    # -------------------------------------------------------------------
    # Derived dataframes (pivots, aggregations, proportions).
    # -------------------------------------------------------------------
    synth_ACDUdf_wide = pivot_connectivity(synth_ACDUdf, "ACDUdf_wide_synth",     # pivot long-format ungrouped -> wide; same helper used for real data
                                           value_col="cell_count", region_col="region_acronym") if not synth_ACDUdf.empty else pd.DataFrame()
    synth_ACDGdf_wide_rebuilt = pivot_connectivity(synth_ACDGdf, "ACDGdf_wide_synth",  # rebuild grouped wide via melt-then-pivot (consistency check)
                                                    value_col="cell_count", region_col="group_name")
    # Prefer the rebuilt pivot if the shapes match; fall back to our constructed one.
    if synth_ACDGdf_wide_rebuilt.shape == synth_FCDGdf_wide.shape:                 # if pivot reproduces the original shape, prefer the canonical version
        synth_FCDGdf_wide_final = synth_ACDGdf_wide_rebuilt
    else:                                                                           # shape mismatch (rare) -> use the directly-constructed one
        synth_FCDGdf_wide_final = synth_FCDGdf_wide
    synth_FCDUdf_wide_final = synth_ACDUdf_wide if not synth_ACDUdf_wide.empty else synth_FCDUdf_wide  # ungrouped: prefer the pivot if available

    AKDdf_agg = aggregate_kinematics(synth_AKDdf, "AKDdf_agg_synth")               # outcome-grouped aggregation on synthetic reaches
    FKDdf_agg = aggregate_kinematics(synth_FKDdf, "FKDdf_agg_synth")               # same on filtered set (identical here by construction)
    AKDdf_agg_contact = aggregate_kinematics_by_contact(synth_AKDdf, "AKDdf_agg_contact_synth")  # contact-grouped aggregation
    FKDdf_agg_contact = aggregate_kinematics_by_contact(synth_FKDdf, "FKDdf_agg_contact_synth")

    AKDdf_prop = compute_outcome_proportions(synth_AKDdf, "AKDdf_prop_synth")      # per-subject outcome proportions (missed/displaced/retrieved)
    FKDdf_prop = compute_outcome_proportions(synth_FKDdf, "FKDdf_prop_synth")
    AKDdf_contact_prop = compute_contact_proportions(synth_AKDdf, "AKDdf_contact_prop_synth", group_col="contact_group")  # per-reach contact rollup
    FKDdf_contact_prop = compute_contact_proportions(synth_FKDdf, "FKDdf_contact_prop_synth", group_col="contact_group")
    AKDdf_segment_contact_prop = compute_contact_proportions(synth_AKDdf, "AKDdf_segment_contact_prop_synth", group_col="segment_contact_group")  # per-segment contact rollup
    FKDdf_segment_contact_prop = compute_contact_proportions(synth_FKDdf, "FKDdf_segment_contact_prop_synth", group_col="segment_contact_group")

    # -------------------------------------------------------------------
    # Assemble the synthetic LoadedData. Raw tables that aren't used by
    # analytical code carry over from real (with subject IDs unchanged,
    # which is honest because we are not synthesizing those).
    # -------------------------------------------------------------------
    synth = LoadedData(                                                            # constructor uses keyword args matching the LoadedData dataclass fields
        # Raw tables: keep real ones so schema is intact for debug/reference
        subjectsdf=real_data.subjectsdf,
        kinematicsdf=synth_AKDdf,                                                  # replace with synthetic so any downstream code reading kinematicsdf sees synthetic data
        manual_pelletdf=real_data.manual_pelletdf,
        weightsdf=real_data.weightsdf,
        surgeriesdf=real_data.surgeriesdf,
        brainsdf=real_data.brainsdf,
        countsdf=real_data.countsdf,
        counts_groupeddf=real_data.counts_groupeddf,
        # Base six
        AKDdf=synth_AKDdf,
        FKDdf=synth_FKDdf,
        ACDUdf=synth_ACDUdf,
        ACDGdf=synth_ACDGdf,
        FCDUdf=synth_FCDUdf,
        FCDGdf=synth_FCDGdf,
        # Wide
        ACDUdf_wide=synth_ACDUdf_wide,
        ACDGdf_wide=synth_ACDGdf_wide_rebuilt,
        FCDUdf_wide=synth_FCDUdf_wide_final,
        FCDGdf_wide=synth_FCDGdf_wide_final,
        # Aggregated
        AKDdf_agg=AKDdf_agg,
        FKDdf_agg=FKDdf_agg,
        AKDdf_agg_contact=AKDdf_agg_contact,
        FKDdf_agg_contact=FKDdf_agg_contact,
        # Proportions
        AKDdf_prop=AKDdf_prop,
        FKDdf_prop=FKDdf_prop,
        AKDdf_contact_prop=AKDdf_contact_prop,
        FKDdf_contact_prop=FKDdf_contact_prop,
        AKDdf_segment_contact_prop=AKDdf_segment_contact_prop,
        FKDdf_segment_contact_prop=FKDdf_segment_contact_prop,
        # Matched
        matched_subjects=tuple(synth_ids),                                         # tuple is the canonical immutable container expected by LoadedData
    )

    if verbose:                                                                    # caller wants a short summary
        print(
            f"Synthesized cohort: N={n_synthetic} subjects "
            f"(from {len(real_subjects)} prototypes, seed={seed}, "
            f"conn_noise={conn_noise_scale}, kine_noise={kine_noise_scale})"
        )
        print(f"  AKDdf:        {synth.AKDdf.shape}")                               # shape tuple: (rows, cols)
        print(f"  FCDGdf_wide:  {synth.FCDGdf_wide.shape}")

    return synth
