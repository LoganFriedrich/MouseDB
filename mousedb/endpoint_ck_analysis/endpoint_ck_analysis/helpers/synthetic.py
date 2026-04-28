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
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..config import METADATA_COLS


# Reasonable defaults for pipeline-testing. Loud enough that clusters
# are detectable; quiet enough that clusters are also clearly distinct.
DEFAULT_N_SYNTHETIC = 30
DEFAULT_CONN_NOISE_SCALE = 0.3    # fraction of cross-subject std
DEFAULT_KINE_NOISE_SCALE = 0.10   # fraction of per-feature std
DEFAULT_SEED = 42


def _synth_id(i: int, prototype: str) -> str:
    """Consistent synthetic subject ID encoding prototype lineage."""
    return f"SYN_{i:03d}_from_{prototype}"


def prototype_map(subject_ids) -> pd.Series:
    """Extract prototype (real) subject ID from each synthetic subject ID.

    Accepts any iterable of strings. Returns a Series indexed by subject
    ID with the prototype string as value. Non-synthetic IDs map to
    themselves (so real-data paths keep working).
    """
    out = {}
    for sid in subject_ids:
        if sid.startswith("SYN_") and "_from_" in sid:
            out[sid] = sid.split("_from_", 1)[1]
        else:
            out[sid] = sid
    return pd.Series(out, name="prototype")


def _clone_conn_row(proto_row: pd.Series, rng: np.random.Generator,
                    noise_scale: float, reference_std: pd.Series) -> pd.Series:
    """Perturb one prototype's connectivity vector."""
    noise = rng.normal(0, noise_scale * reference_std.values, size=len(proto_row))
    new = (proto_row.values + noise).clip(min=0)
    return pd.Series(new, index=proto_row.index)


def _clone_kine_rows(proto_rows: pd.DataFrame, rng: np.random.Generator,
                     noise_scale: float, kine_cols: List[str]) -> pd.DataFrame:
    """Copy a prototype's reach rows and perturb every numeric kinematic column."""
    clone = proto_rows.copy()
    for col in kine_cols:
        if col not in clone.columns:
            continue
        col_std = clone[col].std()
        if not np.isfinite(col_std) or col_std == 0:
            continue
        clone[col] = clone[col].astype(float) + rng.normal(
            0, noise_scale * col_std, size=len(clone)
        )
    return clone


def _numeric_kinematic_cols(df: pd.DataFrame) -> List[str]:
    """Return numeric, non-metadata column names suitable for perturbation."""
    numeric = df.select_dtypes(include="number").columns.tolist()
    return [c for c in numeric if c not in METADATA_COLS]


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
    from ..data_loader import LoadedData
    from .kinematics import (
        aggregate_kinematics, aggregate_kinematics_by_contact,
        compute_contact_proportions, compute_outcome_proportions,
    )
    from .connectivity import pivot_connectivity

    rng = np.random.default_rng(seed)
    real_subjects = list(real_data.matched_subjects)
    if not real_subjects:
        raise ValueError("Real data has no matched subjects; cannot synthesize.")

    # -------------------------------------------------------------------
    # Assign prototypes (round-robin) and mint synthetic subject IDs.
    # -------------------------------------------------------------------
    prototypes = [real_subjects[i % len(real_subjects)] for i in range(n_synthetic)]
    synth_ids = [_synth_id(i, proto) for i, proto in enumerate(prototypes)]

    # -------------------------------------------------------------------
    # Connectivity: clone wide-format rows with noise.
    # -------------------------------------------------------------------
    conn_wide_grouped = real_data.FCDGdf_wide
    conn_wide_ungrouped = real_data.FCDUdf_wide
    if conn_wide_grouped.empty:
        raise ValueError("FCDGdf_wide is empty in real_data; nothing to clone.")

    conn_grouped_std = conn_wide_grouped.std(axis=0).replace(0, conn_wide_grouped.std().median() or 1.0).fillna(1.0)
    conn_ungrouped_std = conn_wide_ungrouped.std(axis=0).replace(0, conn_wide_ungrouped.std().median() or 1.0).fillna(1.0) if not conn_wide_ungrouped.empty else None

    synth_FCDGdf_wide_rows = []
    synth_FCDUdf_wide_rows = []
    for sid, proto in zip(synth_ids, prototypes):
        synth_FCDGdf_wide_rows.append(
            _clone_conn_row(conn_wide_grouped.loc[proto], rng, conn_noise_scale, conn_grouped_std).rename(sid)
        )
        if conn_wide_ungrouped is not None and not conn_wide_ungrouped.empty:
            synth_FCDUdf_wide_rows.append(
                _clone_conn_row(conn_wide_ungrouped.loc[proto], rng, conn_noise_scale, conn_ungrouped_std).rename(sid)
            )
    synth_FCDGdf_wide = pd.DataFrame(synth_FCDGdf_wide_rows)
    synth_FCDGdf_wide.index.name = "subject_id"
    synth_FCDUdf_wide = (
        pd.DataFrame(synth_FCDUdf_wide_rows)
        if synth_FCDUdf_wide_rows else pd.DataFrame()
    )
    if not synth_FCDUdf_wide.empty:
        synth_FCDUdf_wide.index.name = "subject_id"

    # -------------------------------------------------------------------
    # Long-format connectomics (ACDGdf / FCDGdf etc.). Build by melting
    # the synthetic wide frames back to the long schema the notebooks
    # expect (subject_id, group_name/region_acronym, hemisphere, cell_count).
    # -------------------------------------------------------------------
    def _melt_wide(wide: pd.DataFrame, region_key: str) -> pd.DataFrame:
        if wide.empty:
            return pd.DataFrame(columns=["subject_id", region_key, "hemisphere", "cell_count"])
        long = wide.reset_index().melt(
            id_vars="subject_id", var_name="region_hemi", value_name="cell_count"
        )
        # Split the region_hemi column back into region and hemisphere pieces.
        parts = long["region_hemi"].str.rsplit("_", n=1, expand=True)
        long[region_key] = parts[0]
        long["hemisphere"] = parts[1]
        return long.drop(columns="region_hemi")

    synth_ACDGdf = _melt_wide(synth_FCDGdf_wide, "group_name")
    synth_ACDUdf = _melt_wide(synth_FCDUdf_wide, "region_acronym") if not synth_FCDUdf_wide.empty else pd.DataFrame()
    # In the synthetic world every subject has connectivity, so FCDGdf == ACDGdf.
    synth_FCDGdf = synth_ACDGdf.copy()
    synth_FCDUdf = synth_ACDUdf.copy()

    # -------------------------------------------------------------------
    # Kinematics: clone reach rows per synthetic subject, perturb numeric
    # kinematic columns in place.
    # -------------------------------------------------------------------
    kine_cols = _numeric_kinematic_cols(real_data.AKDdf)
    kine_pieces = []
    for sid, proto in zip(synth_ids, prototypes):
        proto_reaches = real_data.AKDdf[real_data.AKDdf["subject_id"] == proto]
        clone = _clone_kine_rows(proto_reaches, rng, kine_noise_scale, kine_cols)
        clone["subject_id"] = sid
        kine_pieces.append(clone)
    synth_AKDdf = pd.concat(kine_pieces, ignore_index=True) if kine_pieces else pd.DataFrame()
    # Every synthetic subject has connectivity by construction, so FKDdf == AKDdf.
    synth_FKDdf = synth_AKDdf.copy()

    # -------------------------------------------------------------------
    # Derived dataframes (pivots, aggregations, proportions).
    # -------------------------------------------------------------------
    synth_ACDUdf_wide = pivot_connectivity(synth_ACDUdf, "ACDUdf_wide_synth",
                                           value_col="cell_count", region_col="region_acronym") if not synth_ACDUdf.empty else pd.DataFrame()
    synth_ACDGdf_wide_rebuilt = pivot_connectivity(synth_ACDGdf, "ACDGdf_wide_synth",
                                                    value_col="cell_count", region_col="group_name")
    # Prefer the rebuilt pivot if the shapes match; fall back to our constructed one.
    if synth_ACDGdf_wide_rebuilt.shape == synth_FCDGdf_wide.shape:
        synth_FCDGdf_wide_final = synth_ACDGdf_wide_rebuilt
    else:
        synth_FCDGdf_wide_final = synth_FCDGdf_wide
    synth_FCDUdf_wide_final = synth_ACDUdf_wide if not synth_ACDUdf_wide.empty else synth_FCDUdf_wide

    AKDdf_agg = aggregate_kinematics(synth_AKDdf, "AKDdf_agg_synth")
    FKDdf_agg = aggregate_kinematics(synth_FKDdf, "FKDdf_agg_synth")
    AKDdf_agg_contact = aggregate_kinematics_by_contact(synth_AKDdf, "AKDdf_agg_contact_synth")
    FKDdf_agg_contact = aggregate_kinematics_by_contact(synth_FKDdf, "FKDdf_agg_contact_synth")

    AKDdf_prop = compute_outcome_proportions(synth_AKDdf, "AKDdf_prop_synth")
    FKDdf_prop = compute_outcome_proportions(synth_FKDdf, "FKDdf_prop_synth")
    AKDdf_contact_prop = compute_contact_proportions(synth_AKDdf, "AKDdf_contact_prop_synth", group_col="contact_group")
    FKDdf_contact_prop = compute_contact_proportions(synth_FKDdf, "FKDdf_contact_prop_synth", group_col="contact_group")
    AKDdf_segment_contact_prop = compute_contact_proportions(synth_AKDdf, "AKDdf_segment_contact_prop_synth", group_col="segment_contact_group")
    FKDdf_segment_contact_prop = compute_contact_proportions(synth_FKDdf, "FKDdf_segment_contact_prop_synth", group_col="segment_contact_group")

    # -------------------------------------------------------------------
    # Assemble the synthetic LoadedData. Raw tables that aren't used by
    # analytical code carry over from real (with subject IDs unchanged,
    # which is honest because we are not synthesizing those).
    # -------------------------------------------------------------------
    synth = LoadedData(
        # Raw tables: keep real ones so schema is intact for debug/reference
        subjectsdf=real_data.subjectsdf,
        kinematicsdf=synth_AKDdf,  # Replace with synthetic for consistency
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
        matched_subjects=tuple(synth_ids),
    )

    if verbose:
        print(
            f"Synthesized cohort: N={n_synthetic} subjects "
            f"(from {len(real_subjects)} prototypes, seed={seed}, "
            f"conn_noise={conn_noise_scale}, kine_noise={kine_noise_scale})"
        )
        print(f"  AKDdf:        {synth.AKDdf.shape}")
        print(f"  FCDGdf_wide:  {synth.FCDGdf_wide.shape}")

    return synth
