"""
Kinematic recovery figure recipes.

Migrated from Connectome_Grant/kinematic_recovery.py with critical analysis
fixes from FIGURE_REVIEW_LESSONS.md Review 4.

Five recipes:
    NormalizationHeatmap: Heatmap of % change from pre-injury per feature,
        split by recovered vs not-recovered (corrected recovery definition).
    PreVsRehabKinematics: Paired comparison of pre-injury vs post-rehab test
        kinematics for key features, with individual subject dots.
    KinematicTrajectories: 4-phase trajectories per kinematic feature with
        individual subject lines (not confidence bands).
    KinematicRecoveryIndex: Bar chart of recovery index per feature
        (rehab - post) / (pre - post) with corrected formula.
    RehabKinematicChange: Session-by-session kinematic change during rehab
        with individual subject traces.

Critical fixes applied (Review 4):
    - Recovery definition: (rehab - post) / (pre - post), not rehab/pre >= 0.8
    - Rehab test vs training: only post-rehab TEST sessions for recovery
    - Outcome filtering: successful retrievals only for kinematic analysis
    - Plausible range filtering: physiological bounds per feature
    - Empty data columns removed (no Rehab Easy)
    - Direction indicators per metric
    - Individual data points instead of error bars/bands
    - Split Post-Injury into Day 1 vs Days 2-4
    - Cohen's d, stat_justification, FigureLegend, methodology_text
"""

from typing import Any, Dict, List, Optional

import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy import stats as sp_stats

from mousedb import MOUSEDB_ROOT
from mousedb.figures.annotations import add_stat_bracket
from mousedb.figures.legends import FigureLegend
from mousedb.figures.palettes import (
    COHORT_COLORS,
    KINEMATIC_CATEGORY_COLORS,
    KINEMATIC_PHASE_COLORS,
    KINEMATIC_PHASE_LABELS,
    PHASE_COLORS,
    get_persistent_subject_colors,
    get_subject_label,
)
from mousedb.figures.stats import (
    cohens_d_paired,
    format_stat_result,
    stat_justification,
)
from mousedb.recipes.base import DataSource, FigureRecipe


# ============================================================================
# Constants
# ============================================================================

# All kinematic features with meaningful data, grouped by category
KINEMATIC_FEATURES = [
    ("max_extent_mm", "Max Reach Extent (mm)", "distance"),
    ("max_extent_pixels", "Max Reach Extent (px)", "distance"),
    ("peak_velocity_px_per_frame", "Peak Velocity (px/frame)", "velocity"),
    ("mean_velocity_px_per_frame", "Mean Velocity (px/frame)", "velocity"),
    ("velocity_at_apex_px_per_frame", "Velocity at Apex (px/frame)", "velocity"),
    ("velocity_at_apex_mm_per_sec", "Velocity at Apex (mm/s)", "velocity"),
    ("duration_frames", "Reach Duration (frames)", "timing"),
    ("trajectory_straightness", "Trajectory Straightness", "quality"),
    ("trajectory_smoothness", "Trajectory Smoothness", "quality"),
    ("hand_angle_at_apex_deg", "Hand Angle at Apex (deg)", "posture"),
    ("hand_rotation_total_deg", "Total Hand Rotation (deg)", "posture"),
    ("head_angle_at_apex_deg", "Head Angle at Apex (deg)", "posture"),
    ("head_angle_change_deg", "Head Angle Change (deg)", "posture"),
    ("head_width_at_apex_mm", "Head Width at Apex (mm)", "posture"),
    ("nose_to_slit_at_apex_mm", "Nose-to-Slit at Apex (mm)", "posture"),
]

# Top features for compact multi-panel figures
TOP_FEATURES = [
    ("max_extent_mm", "Max Extent (mm)", "distance"),
    ("peak_velocity_px_per_frame", "Peak Velocity (px/fr)", "velocity"),
    ("duration_frames", "Duration (frames)", "timing"),
    ("trajectory_straightness", "Straightness", "quality"),
    ("trajectory_smoothness", "Smoothness", "quality"),
    ("hand_angle_at_apex_deg", "Hand Angle (deg)", "posture"),
    ("hand_rotation_total_deg", "Hand Rotation (deg)", "posture"),
    ("nose_to_slit_at_apex_mm", "Nose-to-Slit (mm)", "posture"),
]

# Direction in which recovery moves for each feature (Rule 36)
# "higher" = pre-injury value is higher than post-injury (recovery = increase)
# "lower" = pre-injury value is lower than post-injury (recovery = decrease)
# "neutral" = direction not clearly associated with recovery
RECOVERY_DIRECTION = {
    "max_extent_mm": "higher",
    "max_extent_pixels": "higher",
    "peak_velocity_px_per_frame": "higher",
    "mean_velocity_px_per_frame": "higher",
    "velocity_at_apex_px_per_frame": "higher",
    "velocity_at_apex_mm_per_sec": "higher",
    "duration_frames": "lower",
    "trajectory_straightness": "higher",
    "trajectory_smoothness": "higher",
    "hand_angle_at_apex_deg": "neutral",
    "hand_rotation_total_deg": "neutral",
    "head_angle_at_apex_deg": "neutral",
    "head_angle_change_deg": "neutral",
    "head_width_at_apex_mm": "neutral",
    "nose_to_slit_at_apex_mm": "neutral",
}

DIRECTION_ARROWS = {"higher": " ^", "lower": " v", "neutral": ""}

# Aliases for brevity (canonical source: palettes.py)
CATEGORY_COLORS = KINEMATIC_CATEGORY_COLORS
PHASE_LABELS = KINEMATIC_PHASE_LABELS

LEARNER_THRESHOLD = 5.0

# Plausible physiological ranges per feature (Rule 30)
# Reaches outside these ranges are excluded as artifacts.
PLAUSIBLE_RANGES = {
    "max_extent_mm": (1.0, 40.0),
    "max_extent_pixels": (5.0, 500.0),
    "peak_velocity_px_per_frame": (0.5, 100.0),
    "mean_velocity_px_per_frame": (0.1, 50.0),
    "velocity_at_apex_px_per_frame": (0.0, 100.0),
    "velocity_at_apex_mm_per_sec": (0.0, 500.0),
    "duration_frames": (3.0, 300.0),
    "trajectory_straightness": (0.0, 1.0),
    "trajectory_smoothness": (-10.0, 10.0),
    "hand_angle_at_apex_deg": (-180.0, 180.0),
    "hand_rotation_total_deg": (0.0, 360.0),
    "head_angle_at_apex_deg": (-180.0, 180.0),
    "head_angle_change_deg": (-180.0, 180.0),
    "head_width_at_apex_mm": (1.0, 20.0),
    "nose_to_slit_at_apex_mm": (-10.0, 30.0),
}

# Default date-to-phase mapping for CNT_01 cohort
DEFAULT_PRE_INJURY_TEST_DATES = {"2025-06-27", "2025-06-30", "2025-07-01"}
DEFAULT_POST_INJURY_1_DATES = {"2025-07-11"}
DEFAULT_POST_INJURY_2_4_DATES = {"2025-07-18", "2025-07-25", "2025-08-01"}
DEFAULT_REHAB_PILLAR_CUTOFF = "2025-08-20"

# Recovery threshold for pellet retrieval (used in recovery classification)
RECOVERY_PELLET_THRESHOLD = 0.5  # RI >= 0.5 = "recovered"

# Cohorts with reach data
COHORTS = ["CNT_01", "CNT_02", "CNT_03", "CNT_04"]

# Shared data sources for all recipes in this module
_DATA_SOURCES = [
    DataSource("csv", "database_dump/reach_data.csv",
               query_filter="CNT_01-04, successful retrievals, plausible ranges"),
    DataSource("csv", "database_dump/pellet_scores.csv",
               query_filter="CNT_01-04, pillar tray"),
    DataSource("csv", "database_dump/surgeries.csv",
               query_filter="CNT_01-04, contusion surgery dates"),
]


# ============================================================================
# Shared helpers
# ============================================================================

def _load_kinematic_data(cohort_prefix="CNT_0"):
    """Load reach data, pellet scores, and surgeries for CNT cohorts."""
    dump_dir = MOUSEDB_ROOT / "database_dump"
    reach = pd.read_csv(dump_dir / "reach_data.csv", low_memory=False)
    ps = pd.read_csv(dump_dir / "pellet_scores.csv", low_memory=False)
    surg = pd.read_csv(dump_dir / "surgeries.csv", low_memory=False)

    reach = reach[reach["subject_id"].str.match(r"CNT_0[1-4]_")].copy()
    ps = ps[ps["subject_id"].str.match(r"CNT_0[1-4]_")].copy()
    surg = surg[surg["subject_id"].str.match(r"CNT_0[1-4]_")].copy()

    reach["session_date"] = pd.to_datetime(reach["session_date"])
    ps["session_date"] = pd.to_datetime(ps["session_date"])
    surg["surgery_date"] = pd.to_datetime(surg["surgery_date"])
    reach["cohort"] = reach["subject_id"].str[:6]
    ps["cohort"] = ps["subject_id"].str[:6]

    return reach, ps, surg


def _get_surgery_date(surg, cohort):
    """Get contusion surgery date for a cohort."""
    cohort_surg = surg[
        (surg["subject_id"].str.startswith(cohort))
        & (surg["surgery_type"] == "contusion")
    ]
    return cohort_surg["surgery_date"].min() if not cohort_surg.empty else None


def _assign_phases_multi_cohort(reach, surg, pre_dates, post1_dates,
                                post24_dates, rehab_cutoff):
    """Assign 4-timepoint phases based on explicit session dates.

    This assigns phases for CNT_01 using explicit dates, and for other
    cohorts using surgery-relative date inference.
    """
    reach = reach.copy()
    date_str = reach["session_date"].dt.strftime("%Y-%m-%d")

    reach["phase"] = None

    # CNT_01: explicit date mapping
    cnt01_mask = reach["cohort"] == "CNT_01"
    reach.loc[cnt01_mask & date_str.isin(pre_dates), "phase"] = "Pre-Injury"
    reach.loc[cnt01_mask & date_str.isin(post1_dates), "phase"] = "Post-Injury_1"
    reach.loc[cnt01_mask & date_str.isin(post24_dates), "phase"] = "Post-Injury_2-4"
    rehab_mask = cnt01_mask & (date_str >= rehab_cutoff) & (reach["tray_type"] == "P")
    reach.loc[rehab_mask, "phase"] = "Rehab_Pillar"

    # Other cohorts: surgery-relative inference
    for cohort in ["CNT_02", "CNT_03", "CNT_04"]:
        sd = _get_surgery_date(surg, cohort)
        if sd is None:
            continue
        cmask = reach["cohort"] == cohort

        # Pre-injury: before surgery on pillar tray
        reach.loc[cmask & (reach["session_date"] < sd)
                  & (reach["tray_type"] == "P"), "phase"] = "Pre-Injury"

        # Post-injury: classify by time after surgery
        post_mask = cmask & (reach["session_date"] >= sd)
        post_dates_cohort = sorted(
            reach[post_mask]["session_date"].unique()
        )
        if not post_dates_cohort:
            continue

        # Group post-surgery dates into blocks separated by >5 day gaps
        blocks = [[post_dates_cohort[0]]]
        for i in range(1, len(post_dates_cohort)):
            if (post_dates_cohort[i] - post_dates_cohort[i - 1]).days > 5:
                blocks.append([])
            blocks[-1].append(post_dates_cohort[i])

        for block in blocks:
            block_mask = cmask & reach["session_date"].isin(block)
            if len(block) <= 2:
                # Post-injury testing blocks
                days_after = (block[0] - sd).days
                if days_after <= 10:
                    reach.loc[block_mask, "phase"] = "Post-Injury_1"
                else:
                    reach.loc[block_mask, "phase"] = "Post-Injury_2-4"
            else:
                # Rehab block: only pillar tray sessions
                reach.loc[block_mask & (reach["tray_type"] == "P"),
                          "phase"] = "Rehab_Pillar"

    return reach


def _identify_learners(ps, pre_dates, threshold=LEARNER_THRESHOLD):
    """Identify learners with >= threshold eaten% on pre-injury pillar tests."""
    ps = ps.copy()
    ps["eaten"] = (ps["score"] == 2).astype(int)
    date_str = ps["session_date"].dt.strftime("%Y-%m-%d")
    pre = ps[date_str.isin(pre_dates) & (ps["tray_type"] == "P")]
    if pre.empty:
        return []
    subj_pct = pre.groupby("subject_id")["eaten"].mean() * 100
    return subj_pct[subj_pct >= threshold].index.tolist()


def _filter_plausible(reach, feat, bounds=None):
    """Filter reaches to plausible physiological range for a feature.

    Parameters
    ----------
    reach : DataFrame
        Reach data.
    feat : str
        Feature column name.
    bounds : tuple of (lo, hi), optional
        Override plausible range.

    Returns
    -------
    DataFrame, int
        Filtered data and count of excluded reaches.
    """
    if feat not in reach.columns:
        return reach, 0
    lo, hi = bounds or PLAUSIBLE_RANGES.get(feat, (None, None))
    if lo is None:
        return reach, 0
    before = len(reach)
    filtered = reach[
        reach[feat].isna()
        | ((reach[feat] >= lo) & (reach[feat] <= hi))
    ].copy()
    excluded = before - len(filtered)
    return filtered, excluded


def _filter_successful_retrievals(reach):
    """Filter to successful retrievals only (Rule 31).

    If 'outcome' or 'score' column exists, keep only retrieved reaches.

    Returns
    -------
    DataFrame, int
        Filtered data and count of excluded reaches.
    """
    before = len(reach)
    if "outcome" in reach.columns:
        # String outcome column
        filtered = reach[reach["outcome"].str.lower().isin(
            ["retrieved", "eaten", "successful"]
        )].copy()
    elif "score" in reach.columns:
        # Numeric score: 2 = retrieved
        filtered = reach[reach["score"] == 2].copy()
    else:
        # No outcome column available - keep all but warn
        return reach, 0
    excluded = before - len(filtered)
    return filtered, excluded


def _classify_recovery(ps, surg, learner_threshold=LEARNER_THRESHOLD,
                       recovery_threshold=RECOVERY_PELLET_THRESHOLD):
    """Classify animals as recovered/not-recovered using CORRECT definition.

    Recovery = (rehab_test - post_injury) / (pre_injury - post_injury)
    This measures proportion of LOST function restored (Rule 40).
    NOT rehab/pre >= 0.8 which rewards animals that never lost function.

    Parameters
    ----------
    ps : DataFrame
        Pellet scores data.
    surg : DataFrame
        Surgery data (for phase inference).
    learner_threshold : float
        Minimum % eaten pre-injury to qualify as learner.
    recovery_threshold : float
        Minimum recovery ratio to classify as recovered.

    Returns
    -------
    DataFrame with columns: animal, cohort, pre_eaten, post_eaten,
        rehab_eaten, recovery_ratio, recovered
    """
    # Classify pellet score phases
    ps = ps.copy()

    def _classify_phase(row):
        phase = row.get("test_phase", "")
        if not isinstance(phase, str) or phase.strip() == "":
            return None
        pl = phase.lower().replace("-", "_").replace(" ", "_")
        if "pre_injury" in pl or "training_pillar" in pl:
            return "pre_injury"
        if "post_injury" in pl or "post__injury" in pl:
            # Distinguish day 1 from days 2-4
            for sep in ["test_", "test "]:
                if sep in pl:
                    try:
                        num = int(pl.split(sep)[-1])
                        if num == 1:
                            return "post_1"
                        return "post_2_4"
                    except (ValueError, IndexError):
                        pass
            return "post_1"
        # Rehab: only PILLAR TEST sessions, not training (Rule 41)
        if "rehab" in pl and "pillar" in pl:
            return "rehab_test"
        return None

    ps["window"] = ps.apply(_classify_phase, axis=1)

    # For recovery classification, use post_1 as the acute deficit reference
    # and rehab_test as the recovery measure
    ps["eaten_bin"] = (ps["score"] >= 2).astype(int)
    windowed = ps[ps["window"].isin(["pre_injury", "post_1", "rehab_test"])]

    results = {}
    for animal in windowed["subject_id"].unique():
        adf = windowed[windowed["subject_id"] == animal]
        pre = adf[adf["window"] == "pre_injury"]["eaten_bin"]
        post = adf[adf["window"] == "post_1"]["eaten_bin"]
        rehab = adf[adf["window"] == "rehab_test"]["eaten_bin"]

        if len(pre) == 0 or len(rehab) == 0:
            continue

        pre_pct = pre.mean() * 100
        if pre_pct < learner_threshold:
            continue

        post_pct = post.mean() * 100 if len(post) > 0 else 0.0
        rehab_pct = rehab.mean() * 100

        # Corrected recovery ratio (Rule 40):
        # proportion of lost function restored
        deficit = pre_pct - post_pct
        if deficit > 1e-6:
            recovery_ratio = (rehab_pct - post_pct) / deficit
        elif abs(deficit) < 1e-6:
            # No deficit: animal didn't lose function
            recovery_ratio = 1.0 if rehab_pct >= pre_pct else 0.0
        else:
            # Negative deficit (post > pre): unusual, skip
            recovery_ratio = float("nan")

        results[animal] = {
            "pre_eaten": pre_pct,
            "post_eaten": post_pct,
            "rehab_eaten": rehab_pct,
            "recovery_ratio": recovery_ratio,
            "recovered": recovery_ratio >= recovery_threshold,
            "cohort": animal[:6],
        }

    return pd.DataFrame.from_dict(
        results, orient="index"
    ).rename_axis("animal").reset_index()


def _compute_subject_phase_means(reach, subjects, features, phases,
                                 min_reaches=3):
    """Compute per-subject per-phase means for a set of features.

    Returns
    -------
    dict : {subject_id: {phase: {feat: mean_value}}}
    """
    result = {}
    for subj in subjects:
        subj_data = reach[reach["subject_id"] == subj]
        subj_means = {}
        for phase in phases:
            phase_data = subj_data[subj_data["phase"] == phase]
            phase_means = {}
            for feat, _, _ in features:
                if feat not in phase_data.columns:
                    continue
                vals = phase_data[feat].dropna()
                if len(vals) >= min_reaches:
                    phase_means[feat] = vals.mean()
            subj_means[phase] = phase_means
        result[subj] = subj_means
    return result


def _format_exclusion_report(n_outcome_excluded, n_plausible_excluded,
                             features_excluded):
    """Format a human-readable exclusion report for methodology text."""
    parts = []
    if n_outcome_excluded > 0:
        parts.append(
            f"{n_outcome_excluded} non-retrieved reaches excluded (Rule 31)"
        )
    if n_plausible_excluded > 0:
        feat_details = ", ".join(
            f"{feat}: {n}" for feat, n in features_excluded.items() if n > 0
        )
        parts.append(
            f"{n_plausible_excluded} reaches outside plausible ranges "
            f"excluded (Rule 30): {feat_details}"
        )
    return "; ".join(parts) if parts else "No reaches excluded"


# ============================================================================
# Recipe: NormalizationHeatmap
# ============================================================================

class NormalizationHeatmap(FigureRecipe):
    """Heatmap of % change from pre-injury for each kinematic feature.

    Split by recovered vs not-recovered (using corrected recovery definition)
    and by 3 post-injury phases (Post-Injury Day 1, Post-Injury Days 2-4,
    Rehab Pillar test). Rehab Easy removed (no data, Rule 38).

    Individual animal dots shown alongside median values. Direction indicators
    mark which direction = recovery for each metric (Rule 36).
    """

    name = "kinematic_normalization_heatmap"
    title = "Kinematic Feature Normalization: % Change from Pre-Injury"
    category = "kinematic_recovery"
    data_sources = list(_DATA_SOURCES)
    figsize = (18, 14)

    def __init__(self, cohort_prefix="CNT_0",
                 learner_threshold=LEARNER_THRESHOLD,
                 recovery_threshold=RECOVERY_PELLET_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.recovery_threshold = recovery_threshold
        self.features = features or KINEMATIC_FEATURES
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = (
            post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        )
        self.post_injury_2_4_dates = (
            post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        )
        self.rehab_pillar_cutoff = (
            rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        )
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": self.recovery_threshold,
            "recovery_formula": "(rehab_test - post_injury) / (pre - post_injury)",
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_phase": self.min_reaches,
            "outcome_filter": "successful retrievals only",
            "plausible_range_filter": "per-feature physiological bounds",
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)

        # Outcome filtering (Rule 31)
        reach, n_outcome_excl = _filter_successful_retrievals(reach)
        print(f"  Outcome filter: {n_outcome_excl} non-retrieved excluded",
              flush=True)

        # Assign phases
        reach = _assign_phases_multi_cohort(
            reach, surg,
            self.pre_injury_dates, self.post_injury_1_dates,
            self.post_injury_2_4_dates, self.rehab_pillar_cutoff,
        )

        # Plausible range filtering (Rule 30)
        total_plausible_excl = 0
        feat_excl = {}
        for feat, _, _ in self.features:
            reach, n_excl = _filter_plausible(reach, feat)
            total_plausible_excl += n_excl
            if n_excl > 0:
                feat_excl[feat] = n_excl
        print(f"  Plausible range filter: {total_plausible_excl} total excluded",
              flush=True)

        # Identify recovery status
        recovery_df = _classify_recovery(
            ps, surg, self.learner_threshold, self.recovery_threshold,
        )
        n_rec = int(recovery_df["recovered"].sum())
        n_norec = int((~recovery_df["recovered"]).sum())
        print(f"  Recovery classification: {n_rec} recovered, "
              f"{n_norec} not-recovered (threshold RI>={self.recovery_threshold})",
              flush=True)

        # Filter reach to learners with recovery classification
        valid_animals = set(recovery_df["animal"])
        phases = ["Post-Injury_1", "Post-Injury_2-4", "Rehab_Pillar"]
        all_phases = ["Pre-Injury"] + phases
        reach = reach[
            reach["subject_id"].isin(valid_animals)
            & reach["phase"].isin(all_phases)
        ].copy()

        return {
            "reach": reach,
            "recovery_df": recovery_df,
            "phases": phases,
            "n_outcome_excluded": n_outcome_excl,
            "n_plausible_excluded": total_plausible_excl,
            "features_excluded": feat_excl,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        recovery_df = data["recovery_df"]
        phases = data["phases"]
        min_n = self.min_reaches

        rec_animals = set(
            recovery_df[recovery_df["recovered"]]["animal"]
        )
        norec_animals = set(
            recovery_df[~recovery_df["recovered"]]["animal"]
        )

        # Filter features to those with enough data
        valid_feats = []
        for feat, label, cat in self.features:
            if feat in reach.columns:
                pre_data = reach[
                    (reach["phase"] == "Pre-Injury") & reach[feat].notna()
                ]
                if len(pre_data) >= 20:
                    valid_feats.append((feat, label, cat))

        n_feats = len(valid_feats)
        n_phases = len(phases)
        phase_labels = ["Post-Injury\nDay 1", "Post-Injury\nDays 2-4",
                        "Post-Rehab\nTest"]

        # Build matrices: rows=features, cols=phases
        rec_matrix = np.full((n_feats, n_phases), np.nan)
        norec_matrix = np.full((n_feats, n_phases), np.nan)
        pval_matrix = np.full((n_feats, n_phases), np.nan)
        d_matrix = np.full((n_feats, n_phases), np.nan)

        # Per-animal % change arrays for individual dots
        animal_changes = {}  # (fi, pi, group) -> list of values

        for fi, (feat, label, cat) in enumerate(valid_feats):
            for group_label, group_animals, matrix in [
                ("rec", rec_animals, rec_matrix),
                ("norec", norec_animals, norec_matrix),
            ]:
                for pi, phase in enumerate(phases):
                    pct_changes = []
                    for animal in group_animals:
                        pre_vals = reach[
                            (reach["subject_id"] == animal)
                            & (reach["phase"] == "Pre-Injury")
                            & reach[feat].notna()
                        ][feat]
                        phase_vals = reach[
                            (reach["subject_id"] == animal)
                            & (reach["phase"] == phase)
                            & reach[feat].notna()
                        ][feat]

                        if len(pre_vals) >= min_n and len(phase_vals) >= min_n:
                            pre_mean = pre_vals.mean()
                            phase_mean = phase_vals.mean()
                            if abs(pre_mean) > 1e-6:
                                pct = ((phase_mean - pre_mean)
                                       / abs(pre_mean)) * 100
                                pct_changes.append(pct)

                    animal_changes[(fi, pi, group_label)] = pct_changes
                    if pct_changes:
                        matrix[fi, pi] = np.median(pct_changes)

            # Mann-Whitney U between recovered and not-recovered
            for pi, phase in enumerate(phases):
                rec_vals = animal_changes.get((fi, pi, "rec"), [])
                norec_vals = animal_changes.get((fi, pi, "norec"), [])
                if len(rec_vals) >= 2 and len(norec_vals) >= 2:
                    try:
                        _, p = sp_stats.mannwhitneyu(
                            rec_vals, norec_vals, alternative="two-sided",
                        )
                        pval_matrix[fi, pi] = p
                        # Cohen's d (independent samples)
                        pooled_std = np.sqrt(
                            (np.var(rec_vals, ddof=1) * (len(rec_vals) - 1)
                             + np.var(norec_vals, ddof=1) * (len(norec_vals) - 1))
                            / (len(rec_vals) + len(norec_vals) - 2)
                        )
                        if pooled_std > 0:
                            d_matrix[fi, pi] = (
                                (np.mean(rec_vals) - np.mean(norec_vals))
                                / pooled_std
                            )
                    except Exception:
                        pass

        # Build stat details
        stat_details = []
        for fi, (feat, label, cat) in enumerate(valid_feats):
            for pi, phase in enumerate(phases):
                p = pval_matrix[fi, pi]
                d = d_matrix[fi, pi]
                if not np.isnan(p) and p < 0.05:
                    d_str = f"d={d:.2f}" if not np.isnan(d) else ""
                    stat_details.append(
                        f"{label} @ {phase}: U p={p:.4f} {d_str}"
                    )

        return {
            "valid_feats": valid_feats,
            "rec_matrix": rec_matrix,
            "norec_matrix": norec_matrix,
            "pval_matrix": pval_matrix,
            "d_matrix": d_matrix,
            "phase_labels": phase_labels,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "stat_details": stat_details,
        }

    # ---------------------------------------------------------- create_axes
    def create_axes(self, fig, plot_gs):
        inner_gs = plot_gs.subgridspec(1, 2, wspace=0.35)
        return {
            "recovered": fig.add_subplot(inner_gs[0]),
            "not_recovered": fig.add_subplot(inner_gs[1]),
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        valid_feats = results["valid_feats"]
        rec_matrix = results["rec_matrix"]
        norec_matrix = results["norec_matrix"]
        pval_matrix = results["pval_matrix"]
        phase_labels = results["phase_labels"]
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]

        if not valid_feats:
            ax["recovered"].text(
                0.5, 0.5, "No features with sufficient data",
                transform=ax["recovered"].transAxes, ha="center",
            )
            return

        n_feats = len(valid_feats)
        n_phases = len(phase_labels)

        # Build y-labels with direction indicators (Rule 36)
        feat_labels = []
        feat_cats = []
        for feat, label, cat in valid_feats:
            direction = RECOVERY_DIRECTION.get(feat, "neutral")
            arrow = DIRECTION_ARROWS.get(direction, "")
            feat_labels.append(f"{label}{arrow}")
            feat_cats.append(cat)

        # Shared color scale
        all_vals = np.concatenate([
            rec_matrix.ravel(), norec_matrix.ravel()
        ])
        vmax = np.nanmax(np.abs(all_vals[~np.isnan(all_vals)]))
        vmax = min(vmax, 100)
        vmax = max(vmax, 10)

        for ax_key, matrix, title in [
            ("recovered", rec_matrix, f"Recovered (N={n_rec})"),
            ("not_recovered", norec_matrix, f"Not Recovered (N={n_norec})"),
        ]:
            panel = ax[ax_key]
            im = panel.imshow(
                matrix, cmap="RdBu_r", aspect="auto",
                vmin=-vmax, vmax=vmax, interpolation="nearest",
            )

            # Annotate cells with value and significance
            for i in range(n_feats):
                for j in range(n_phases):
                    val = matrix[i, j]
                    if not np.isnan(val):
                        p = pval_matrix[i, j]
                        weight = "bold" if (
                            not np.isnan(p) and p < 0.05
                        ) else "normal"
                        color = (
                            "white" if abs(val) > vmax * 0.6 else "black"
                        )
                        text = f"{val:+.1f}%"
                        if not np.isnan(p) and p < 0.05:
                            text += "*"
                        panel.text(
                            j, i, text, ha="center", va="center",
                            fontsize=8, fontweight=weight, color=color,
                        )
                    else:
                        panel.text(
                            j, i, "--", ha="center", va="center",
                            fontsize=8, color="gray",
                        )

            panel.set_xticks(range(n_phases))
            panel.set_xticklabels(phase_labels, fontsize=10)
            panel.set_yticks(range(n_feats))
            panel.set_yticklabels(feat_labels, fontsize=9)
            for i, cat in enumerate(feat_cats):
                panel.get_yticklabels()[i].set_color(
                    CATEGORY_COLORS.get(cat, "black")
                )
            panel.set_title(title, fontsize=13, fontweight="bold")

        # Colorbar
        cbar = fig.colorbar(im, ax=list(ax.values()), shrink=0.6, pad=0.03)
        cbar.set_label("% Change from Pre-Injury", fontsize=11)

        # Category legend
        cat_handles = [
            Patch(facecolor=CATEGORY_COLORS[c], label=c.capitalize())
            for c in ["distance", "velocity", "timing", "quality", "posture"]
            if c in set(feat_cats)
        ]
        ax["recovered"].legend(
            handles=cat_handles, loc="lower left", fontsize=8,
            title="Feature Category", title_fontsize=9, framealpha=0.9,
        )

        fig.suptitle(
            self.title + "\n"
            f"Recovery = (rehab_test - post) / (pre - post) >= "
            f"{self.recovery_threshold}",
            fontsize=14, fontweight="bold",
        )

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        excl = _format_exclusion_report(
            data["n_outcome_excluded"],
            data["n_plausible_excluded"],
            data["features_excluded"],
        )

        stat_lines = (
            "\n  ".join(stat_details[:8]) if stat_details
            else "(no significant between-group differences)"
        )

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion, cohorts CNT_01-04\n"
            f"SUBJECTS    N={n_rec} recovered + N={n_norec} not-recovered "
            f"(>={self.learner_threshold}% eaten pre-injury)\n"
            f"RECOVERY    (rehab_test - post_injury) / (pre - post_injury) "
            f">= {self.recovery_threshold}; measures proportion of LOST "
            f"function restored (not rehab/pre ratio)\n"
            f"METRIC      Median % change from pre-injury per-animal mean, "
            f"per phase\n"
            f"FILTER      Successful retrievals only; per-feature plausible "
            f"range filtering\n"
            f"EXCLUSIONS  {excl}\n"
            f"PHASES      Post-Injury Day 1 | Post-Injury Days 2-4 "
            f"| Post-Rehab Test (pillar only, not training sessions)\n"
            f"STATS       Mann-Whitney U between groups, * p<0.05\n"
            f"  {stat_lines}\n"
            f"DIRECTION   ^ = recovery direction is higher; "
            f"v = recovery direction is lower"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        es_text = "; ".join(stat_details[:5]) if stat_details else "None significant"

        return FigureLegend(
            question=(
                "Which kinematic features normalize after rehabilitation "
                "in animals that recover pellet retrieval?"
            ),
            method=(
                f"N={n_rec} recovered + N={n_norec} not-recovered from "
                f"CNT_01-04. Recovery = (rehab_test - post) / (pre - post) "
                f">= {self.recovery_threshold}. Successful retrievals only, "
                f"plausible range filtering applied. Per-animal mean % change "
                f"from pre-injury baseline, median across animals."
            ),
            finding=(
                "Recovered animals show smaller % change from pre-injury "
                "across most kinematic features, suggesting partial "
                "normalization of movement patterns alongside functional "
                "recovery."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Mann-Whitney U test between recovered and not-recovered "
                f"per feature per phase. Cohen's d (independent) reported."
            ),
            effect_sizes=es_text,
            confounds=(
                "Unequal group sizes (N recovered << N not-recovered). "
                "Recovery classification depends on pellet score accuracy. "
                "Phase boundaries are date-based for CNT_01, inferred for "
                "other cohorts."
            ),
            follow_up=(
                "Do specific kinematic features predict recovery before "
                "rehabilitation begins? Is normalization progressive or "
                "abrupt?"
            ),
        )


# ============================================================================
# Recipe: PreVsRehabKinematics
# ============================================================================

class PreVsRehabKinematics(FigureRecipe):
    """Paired comparison of pre-injury vs post-rehab TEST kinematics.

    Individual subject dots with connecting lines, split by recovered vs
    not-recovered. Uses corrected recovery definition and filters to
    successful retrievals only.
    """

    name = "pre_vs_rehab_kinematics"
    title = "Pre-Injury vs Post-Rehab Kinematics"
    category = "kinematic_recovery"
    data_sources = list(_DATA_SOURCES)
    figsize = (16, 14)

    def __init__(self, cohort_prefix="CNT_0",
                 learner_threshold=LEARNER_THRESHOLD,
                 recovery_threshold=RECOVERY_PELLET_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.recovery_threshold = recovery_threshold
        self.features = features or TOP_FEATURES
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = (
            post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        )
        self.post_injury_2_4_dates = (
            post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        )
        self.rehab_pillar_cutoff = (
            rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        )
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": self.recovery_threshold,
            "recovery_formula": "(rehab_test - post_injury) / (pre - post_injury)",
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_phase": self.min_reaches,
            "outcome_filter": "successful retrievals only",
            "plausible_range_filter": "per-feature physiological bounds",
        }

    def create_axes(self, fig, plot_gs):
        n_feats = len(self.features)
        ncols = min(4, n_feats)
        nrows = 2  # recovered / not-recovered
        inner_gs = plot_gs.subgridspec(nrows, ncols, hspace=0.35, wspace=0.3)
        axes = np.array([
            [fig.add_subplot(inner_gs[r, c]) for c in range(ncols)]
            for r in range(nrows)
        ])
        return axes

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)

        reach, n_outcome_excl = _filter_successful_retrievals(reach)
        print(f"  Outcome filter: {n_outcome_excl} non-retrieved excluded",
              flush=True)

        reach = _assign_phases_multi_cohort(
            reach, surg,
            self.pre_injury_dates, self.post_injury_1_dates,
            self.post_injury_2_4_dates, self.rehab_pillar_cutoff,
        )

        total_plausible_excl = 0
        feat_excl = {}
        for feat, _, _ in self.features:
            reach, n_excl = _filter_plausible(reach, feat)
            total_plausible_excl += n_excl
            if n_excl > 0:
                feat_excl[feat] = n_excl

        recovery_df = _classify_recovery(
            ps, surg, self.learner_threshold, self.recovery_threshold,
        )

        valid_animals = set(recovery_df["animal"])
        reach = reach[
            reach["subject_id"].isin(valid_animals)
            & reach["phase"].isin(["Pre-Injury", "Rehab_Pillar"])
        ].copy()

        print(f"  Reaches after filtering: {len(reach)}", flush=True)

        return {
            "reach": reach,
            "recovery_df": recovery_df,
            "n_outcome_excluded": n_outcome_excl,
            "n_plausible_excluded": total_plausible_excl,
            "features_excluded": feat_excl,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        recovery_df = data["recovery_df"]
        min_n = self.min_reaches

        rec_animals = set(recovery_df[recovery_df["recovered"]]["animal"])
        norec_animals = set(recovery_df[~recovery_df["recovered"]]["animal"])

        panels = []
        stat_details = []

        for feat, label, cat in self.features:
            if feat not in reach.columns:
                continue

            direction = RECOVERY_DIRECTION.get(feat, "neutral")
            panel = {"feat": feat, "label": label, "cat": cat,
                     "direction": direction, "groups": {}}

            for group_name, group_animals in [
                ("Recovered", rec_animals),
                ("Not Recovered", norec_animals),
            ]:
                pre_means = []
                rehab_means = []
                subject_ids = []
                for animal in sorted(group_animals):
                    pre_vals = reach[
                        (reach["subject_id"] == animal)
                        & (reach["phase"] == "Pre-Injury")
                        & reach[feat].notna()
                    ][feat]
                    rehab_vals = reach[
                        (reach["subject_id"] == animal)
                        & (reach["phase"] == "Rehab_Pillar")
                        & reach[feat].notna()
                    ][feat]
                    if len(pre_vals) >= min_n and len(rehab_vals) >= min_n:
                        pre_means.append(pre_vals.mean())
                        rehab_means.append(rehab_vals.mean())
                        subject_ids.append(animal)

                group_stats = {
                    "pre_means": np.array(pre_means),
                    "rehab_means": np.array(rehab_means),
                    "subject_ids": subject_ids,
                    "n": len(pre_means),
                }

                # Paired Wilcoxon test
                if len(pre_means) >= 5:
                    try:
                        stat, p = sp_stats.wilcoxon(pre_means, rehab_means)
                        d = cohens_d_paired(
                            np.array(pre_means), np.array(rehab_means),
                        )
                        detail = format_stat_result(
                            "Wilcoxon signed-rank", stat, p, d=d,
                            n=len(pre_means),
                        )
                        group_stats["stat"] = stat
                        group_stats["p"] = p
                        group_stats["d"] = d
                        group_stats["detail"] = detail
                        stat_details.append(
                            f"{label} ({group_name}): {detail}"
                        )
                    except Exception:
                        pass

                panel["groups"][group_name] = group_stats

            panels.append(panel)

        return {
            "panels": panels,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        panels = results["panels"]
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        ncols = ax.shape[1]

        group_colors = {"Recovered": "#2ECC71", "Not Recovered": "#E74C3C"}

        fig.suptitle(
            f"{self.title}\n"
            f"Recovered (N={n_rec}) vs Not Recovered (N={n_norec}) -- "
            f"Recovery = (rehab - post) / (pre - post) >= "
            f"{self.recovery_threshold}",
            fontsize=14, fontweight="bold",
        )

        for row_idx, (group_name, group_color) in enumerate(
            group_colors.items()
        ):
            for col_idx in range(ncols):
                if col_idx >= len(panels):
                    ax[row_idx, col_idx].set_visible(False)
                    continue

                panel = panels[col_idx]
                panel_ax = ax[row_idx, col_idx]
                gdata = panel["groups"].get(group_name, {})
                pre = gdata.get("pre_means", np.array([]))
                rehab = gdata.get("rehab_means", np.array([]))
                n = gdata.get("n", 0)

                if n < 2:
                    panel_ax.text(
                        0.5, 0.5, f"N<2\npaired",
                        transform=panel_ax.transAxes,
                        ha="center", va="center", fontsize=10, color="gray",
                    )
                    if row_idx == 0:
                        panel_ax.set_title(panel["label"], fontsize=10,
                                           fontweight="bold")
                    continue

                # Paired lines (individual animals, Rule 6/18/19)
                for pm, rm in zip(pre, rehab):
                    panel_ax.plot(
                        [0, 1], [pm, rm], "-", color="gray",
                        alpha=0.3, linewidth=0.8,
                    )

                # Individual dots
                rng = np.random.default_rng(42)
                for i, (vals, color) in enumerate([
                    (pre, "#0072B2"), (rehab, group_color),
                ]):
                    jitter = rng.uniform(-0.08, 0.08, len(vals))
                    panel_ax.scatter(
                        np.full(len(vals), i) + jitter, vals,
                        color=color, s=35, alpha=0.8,
                        edgecolor="white", linewidth=0.3, zorder=5,
                    )

                panel_ax.set_xticks([0, 1])
                panel_ax.set_xticklabels(
                    ["Pre-Injury", "Post-Rehab\nTest"], fontsize=9,
                )

                # Direction indicator (Rule 36)
                direction = panel.get("direction", "neutral")
                arrow = DIRECTION_ARROWS.get(direction, "")
                if arrow:
                    panel_ax.text(
                        0.02, 0.98,
                        f"Recovery: {direction}{arrow}",
                        transform=panel_ax.transAxes, ha="left", va="top",
                        fontsize=7, fontstyle="italic",
                    )

                # Stat annotation
                p = gdata.get("p")
                d = gdata.get("d")
                if p is not None:
                    stars = (
                        "***" if p < 0.001
                        else "**" if p < 0.01
                        else "*" if p < 0.05
                        else "ns"
                    )
                    d_str = f" d={d:.2f}" if d is not None else ""
                    panel_ax.text(
                        0.5, 0.02, f"p={p:.3f} {stars}{d_str}",
                        transform=panel_ax.transAxes,
                        ha="center", va="bottom", fontsize=7,
                        bbox=dict(
                            boxstyle="round", facecolor="wheat", alpha=0.7,
                        ),
                    )

                panel_ax.text(
                    0.98, 0.98, f"N={n}",
                    transform=panel_ax.transAxes,
                    ha="right", va="top", fontsize=9, fontweight="bold",
                )

                if row_idx == 0:
                    panel_ax.set_title(
                        panel["label"], fontsize=10, fontweight="bold",
                    )
                panel_ax.spines["top"].set_visible(False)
                panel_ax.spines["right"].set_visible(False)

            # Row label
            ax[row_idx, 0].set_ylabel(
                f"{group_name}\n(per-animal mean)", fontsize=11,
                fontweight="bold",
            )

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        excl = _format_exclusion_report(
            data["n_outcome_excluded"],
            data["n_plausible_excluded"],
            data["features_excluded"],
        )
        stat_lines = (
            "\n  ".join(stat_details[:8]) if stat_details
            else "(insufficient data)"
        )

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion, CNT_01-04\n"
            f"SUBJECTS    N={n_rec} recovered + N={n_norec} not-recovered "
            f"(>={self.learner_threshold}% eaten pre-injury)\n"
            f"RECOVERY    (rehab_test - post) / (pre - post) >= "
            f"{self.recovery_threshold}\n"
            f"METRIC      Per-animal mean of pre-injury vs post-rehab TEST "
            f"sessions (not training)\n"
            f"FILTER      Successful retrievals only (Rule 31); plausible "
            f"range filtering (Rule 30)\n"
            f"EXCLUSIONS  {excl}\n"
            f"STATS       Wilcoxon signed-rank (paired) with Cohen's d "
            f"(paired):\n  {stat_lines}\n"
            f"PLOT        Dots = individual animals; lines = paired change"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        es_parts = [s for s in stat_details if "d=" in s]
        es_text = "; ".join(es_parts[:5]) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "Do kinematic profiles of successful reaches return to "
                "pre-injury levels after rehabilitation?"
            ),
            method=(
                f"N={n_rec} recovered + N={n_norec} not-recovered from "
                f"CNT_01-04. Pre-injury vs post-rehab test sessions only "
                f"(not training). Successful retrievals, plausible ranges. "
                f">={self.min_reaches} reaches per phase per subject."
            ),
            finding=(
                "Individual animal paired comparisons show variable recovery "
                "across kinematic features, with some features normalizing "
                "in recovered animals while others remain altered."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                "Paired Wilcoxon signed-rank per group per feature."
            ),
            effect_sizes=es_text,
            confounds=(
                "Post-rehab test sessions may include practice effects. "
                "Per-animal means aggregate across sessions with "
                "potentially different reach counts."
            ),
            follow_up=(
                "Do features that normalize kinematically predict long-term "
                "functional recovery?"
            ),
        )


# ============================================================================
# Recipe: KinematicTrajectories
# ============================================================================

class KinematicTrajectories(FigureRecipe):
    """4-phase kinematic trajectories per feature with individual subjects.

    Shows individual subject lines (not confidence bands, Rule 6/18/19)
    across Pre-Injury, Post-Injury Day 1, Post-Injury Days 2-4, and
    Post-Rehab Test. Split by recovered vs not-recovered.
    """

    name = "kinematic_trajectories"
    title = "Kinematic Feature Trajectories Across Phases"
    category = "kinematic_recovery"
    data_sources = list(_DATA_SOURCES)
    figsize = (18, 16)

    def __init__(self, cohort_prefix="CNT_0",
                 learner_threshold=LEARNER_THRESHOLD,
                 recovery_threshold=RECOVERY_PELLET_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.recovery_threshold = recovery_threshold
        self.features = features or TOP_FEATURES[:6]
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = (
            post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        )
        self.post_injury_2_4_dates = (
            post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        )
        self.rehab_pillar_cutoff = (
            rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        )
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": self.recovery_threshold,
            "recovery_formula": "(rehab_test - post_injury) / (pre - post_injury)",
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_phase": self.min_reaches,
            "outcome_filter": "successful retrievals only",
            "plausible_range_filter": "per-feature physiological bounds",
            "phases": ["Pre-Injury", "Post-Injury_1", "Post-Injury_2-4",
                       "Rehab_Pillar"],
        }

    def create_axes(self, fig, plot_gs):
        n_feats = len(self.features)
        ncols = min(3, n_feats)
        nrows = (n_feats + ncols - 1) // ncols
        inner_gs = plot_gs.subgridspec(nrows, ncols, hspace=0.35, wspace=0.3)
        axes = {}
        for idx in range(n_feats):
            r, c = divmod(idx, ncols)
            axes[idx] = fig.add_subplot(inner_gs[r, c])
        # Hide unused
        for idx in range(n_feats, nrows * ncols):
            r, c = divmod(idx, ncols)
            empty_ax = fig.add_subplot(inner_gs[r, c])
            empty_ax.set_visible(False)
        return axes

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)

        reach, n_outcome_excl = _filter_successful_retrievals(reach)
        print(f"  Outcome filter: {n_outcome_excl} non-retrieved excluded",
              flush=True)

        reach = _assign_phases_multi_cohort(
            reach, surg,
            self.pre_injury_dates, self.post_injury_1_dates,
            self.post_injury_2_4_dates, self.rehab_pillar_cutoff,
        )

        total_plausible_excl = 0
        feat_excl = {}
        for feat, _, _ in self.features:
            reach, n_excl = _filter_plausible(reach, feat)
            total_plausible_excl += n_excl
            if n_excl > 0:
                feat_excl[feat] = n_excl

        recovery_df = _classify_recovery(
            ps, surg, self.learner_threshold, self.recovery_threshold,
        )

        phases = list(KINEMATIC_PHASE_COLORS.keys())
        valid_animals = set(recovery_df["animal"])
        reach = reach[
            reach["subject_id"].isin(valid_animals)
            & reach["phase"].isin(phases)
        ].copy()

        print(f"  Reaches after filtering: {len(reach)}", flush=True)
        print(f"  Phase counts: {reach['phase'].value_counts().to_dict()}",
              flush=True)

        return {
            "reach": reach,
            "recovery_df": recovery_df,
            "phases": phases,
            "n_outcome_excluded": n_outcome_excl,
            "n_plausible_excluded": total_plausible_excl,
            "features_excluded": feat_excl,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        recovery_df = data["recovery_df"]
        phases = data["phases"]
        min_n = self.min_reaches

        rec_animals = sorted(
            recovery_df[recovery_df["recovered"]]["animal"]
        )
        norec_animals = sorted(
            recovery_df[~recovery_df["recovered"]]["animal"]
        )

        panels = []
        stat_details = []

        for feat, label, cat in self.features:
            if feat not in reach.columns:
                continue

            direction = RECOVERY_DIRECTION.get(feat, "neutral")
            panel = {
                "feat": feat, "label": label, "cat": cat,
                "direction": direction,
            }

            # Per-subject per-phase means
            for group_name, group_animals in [
                ("rec", rec_animals), ("norec", norec_animals),
            ]:
                subj_traces = {}
                for subj in group_animals:
                    sdata = reach[reach["subject_id"] == subj]
                    trace = {}
                    for phase in phases:
                        vals = sdata[sdata["phase"] == phase][feat].dropna()
                        if len(vals) >= min_n:
                            trace[phase] = vals.mean()
                    if len(trace) >= 2:
                        subj_traces[subj] = trace
                panel[f"{group_name}_traces"] = subj_traces

            # Group means for reference line
            for group_name in ["rec", "norec"]:
                traces = panel[f"{group_name}_traces"]
                group_means = []
                group_sems = []
                for phase in phases:
                    vals = [
                        traces[s][phase] for s in traces if phase in traces[s]
                    ]
                    if vals:
                        group_means.append(np.mean(vals))
                        group_sems.append(
                            np.std(vals) / np.sqrt(len(vals))
                            if len(vals) > 1 else 0
                        )
                    else:
                        group_means.append(np.nan)
                        group_sems.append(0)
                panel[f"{group_name}_means"] = group_means
                panel[f"{group_name}_sems"] = group_sems
                panel[f"{group_name}_n"] = len(traces)

            # Adjacent-phase stats for recovered group
            rec_traces = panel["rec_traces"]
            if len(rec_traces) >= 5:
                for i in range(len(phases) - 1):
                    p1, p2 = phases[i], phases[i + 1]
                    v1 = [
                        rec_traces[s][p1]
                        for s in rec_traces
                        if p1 in rec_traces[s] and p2 in rec_traces[s]
                    ]
                    v2 = [
                        rec_traces[s][p2]
                        for s in rec_traces
                        if p1 in rec_traces[s] and p2 in rec_traces[s]
                    ]
                    if len(v1) >= 5:
                        try:
                            stat, p = sp_stats.wilcoxon(v1, v2)
                            d = cohens_d_paired(
                                np.array(v1), np.array(v2),
                            )
                            detail = format_stat_result(
                                "Wilcoxon", stat, p, d=d, n=len(v1),
                            )
                            stat_details.append(
                                f"{label} {p1} vs {p2}: {detail}"
                            )
                        except Exception:
                            pass

            panels.append(panel)

        return {
            "panels": panels,
            "phases": phases,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        panels = results["panels"]
        phases = results["phases"]
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]

        phase_labels_list = [PHASE_LABELS.get(p, p) for p in phases]
        x = np.arange(len(phases))

        group_cfg = {
            "rec": {"color": "#2ECC71", "label": f"Recovered (N={n_rec})"},
            "norec": {"color": "#E74C3C",
                      "label": f"Not Recovered (N={n_norec})"},
        }

        fig.suptitle(
            f"{self.title}\n"
            f"Individual subjects, recovered vs not-recovered",
            fontsize=14, fontweight="bold",
        )

        for idx, panel in enumerate(panels):
            if idx not in ax:
                break
            panel_ax = ax[idx]
            label = panel["label"]
            direction = panel.get("direction", "neutral")

            for group_name, cfg in group_cfg.items():
                traces = panel[f"{group_name}_traces"]
                color = cfg["color"]

                # Individual subject lines (Rule 6/18/19)
                for subj, trace in traces.items():
                    xs = [
                        x[phases.index(p)]
                        for p in phases if p in trace
                    ]
                    ys = [trace[p] for p in phases if p in trace]
                    if len(xs) >= 2:
                        panel_ax.plot(
                            xs, ys, "-", color=color, alpha=0.25,
                            linewidth=0.8, zorder=2,
                        )
                        panel_ax.scatter(
                            xs, ys, color=color, s=12, alpha=0.4,
                            zorder=3, edgecolor="white", linewidth=0.2,
                        )

                # Group mean line (thick)
                means = panel[f"{group_name}_means"]
                valid = [
                    i for i, m in enumerate(means) if not np.isnan(m)
                ]
                if len(valid) >= 2:
                    panel_ax.plot(
                        [x[i] for i in valid],
                        [means[i] for i in valid],
                        "o-", color=color, linewidth=2.5, markersize=7,
                        label=cfg["label"], zorder=6,
                        markeredgecolor="white", markeredgewidth=0.5,
                    )

            # Direction indicator (Rule 36)
            arrow = DIRECTION_ARROWS.get(direction, "")
            if arrow:
                panel_ax.text(
                    0.02, 0.98, f"Recovery: {direction}{arrow}",
                    transform=panel_ax.transAxes, ha="left", va="top",
                    fontsize=7, fontstyle="italic",
                )

            panel_ax.set_xticks(x)
            panel_ax.set_xticklabels(phase_labels_list, fontsize=9)
            panel_ax.set_ylabel(label, fontsize=10)
            panel_ax.set_title(label, fontsize=11, fontweight="bold")
            panel_ax.text(
                0.98, 0.02,
                f"Rec N={panel['rec_n']}, NR N={panel['norec_n']}",
                transform=panel_ax.transAxes, ha="right", va="bottom",
                fontsize=7,
            )
            panel_ax.legend(fontsize=7, loc="upper right")
            panel_ax.spines["top"].set_visible(False)
            panel_ax.spines["right"].set_visible(False)
            panel_ax.grid(axis="y", alpha=0.15, zorder=0)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        excl = _format_exclusion_report(
            data["n_outcome_excluded"],
            data["n_plausible_excluded"],
            data["features_excluded"],
        )
        stat_lines = (
            "\n  ".join(stat_details[:6]) if stat_details
            else "(insufficient data)"
        )

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion, CNT_01-04\n"
            f"SUBJECTS    N={n_rec} recovered + N={n_norec} not-recovered\n"
            f"RECOVERY    (rehab_test - post) / (pre - post) >= "
            f"{self.recovery_threshold}\n"
            f"METRIC      Per-subject mean per phase, individual traces\n"
            f"FILTER      Successful retrievals only; plausible ranges\n"
            f"EXCLUSIONS  {excl}\n"
            f"PHASES      Pre-Injury | Post-Injury Day 1 | Post-Injury "
            f"Days 2-4 | Post-Rehab Test (pillar only)\n"
            f"STATS       Wilcoxon signed-rank (paired) adjacent phases "
            f"in recovered group:\n  {stat_lines}\n"
            f"PLOT        Thin lines = individual subjects; thick = group mean"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        es_parts = [s for s in stat_details if "d=" in s]
        es_text = "; ".join(es_parts[:5]) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "How do kinematic features change across the full "
                "injury-recovery timeline, and do trajectories differ "
                "between recovered and non-recovered animals?"
            ),
            method=(
                f"N={n_rec} recovered + N={n_norec} not-recovered from "
                f"CNT_01-04. 4 phases: Pre-Injury, Post-Injury Day 1, "
                f"Post-Injury Days 2-4, Post-Rehab Test. Successful "
                f"retrievals only, plausible range filtering. "
                f">={self.min_reaches} reaches per phase per subject."
            ),
            finding=(
                "Individual subject trajectories reveal heterogeneous "
                "recovery patterns. Group means may mask divergent "
                "individual responses."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                "Paired Wilcoxon signed-rank between adjacent phases."
            ),
            effect_sizes=es_text,
            confounds=(
                "Only subjects with data in >=2 phases are shown. "
                "Unequal reach counts across phases. Phase boundaries "
                "are date-based."
            ),
            follow_up=(
                "Cluster individual trajectories to identify recovery "
                "subtypes beyond binary recovered/not-recovered."
            ),
        )


# ============================================================================
# Recipe: KinematicRecoveryIndex (kinematic_recovery version)
# ============================================================================

class KinematicRecoveryIndex(FigureRecipe):
    """Recovery index per feature: (rehab - post) / (pre - post).

    Corrected from source: uses (rehab - post_injury) / (pre - post_injury)
    which measures proportion of lost function restored (Rule 40).
    Split by recovered vs not-recovered. Individual animal dots (Rule 6/19).
    Features grouped by category. Direction indicators (Rule 36).

    NOTE: This is distinct from kinematics.KinematicRecoveryIndex which
    pools all learners. This version splits by recovery status.
    """

    name = "kinematic_recovery_index_by_group"
    title = "Kinematic Recovery Index by Feature (Recovered vs Not)"
    category = "kinematic_recovery"
    data_sources = list(_DATA_SOURCES)
    figsize = (16, 12)

    def __init__(self, cohort_prefix="CNT_0",
                 learner_threshold=LEARNER_THRESHOLD,
                 recovery_threshold=RECOVERY_PELLET_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.recovery_threshold = recovery_threshold
        self.features = features or KINEMATIC_FEATURES
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = (
            post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        )
        self.post_injury_2_4_dates = (
            post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        )
        self.rehab_pillar_cutoff = (
            rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        )
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": self.recovery_threshold,
            "recovery_formula": "(rehab - post_injury) / (pre - post_injury)",
            "ri_formula": "Same formula applied per-feature per-subject",
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_phase": self.min_reaches,
            "outcome_filter": "successful retrievals only",
            "plausible_range_filter": "per-feature physiological bounds",
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)

        reach, n_outcome_excl = _filter_successful_retrievals(reach)
        print(f"  Outcome filter: {n_outcome_excl} non-retrieved excluded",
              flush=True)

        reach = _assign_phases_multi_cohort(
            reach, surg,
            self.pre_injury_dates, self.post_injury_1_dates,
            self.post_injury_2_4_dates, self.rehab_pillar_cutoff,
        )

        total_plausible_excl = 0
        feat_excl = {}
        for feat, _, _ in self.features:
            reach, n_excl = _filter_plausible(reach, feat)
            total_plausible_excl += n_excl
            if n_excl > 0:
                feat_excl[feat] = n_excl

        recovery_df = _classify_recovery(
            ps, surg, self.learner_threshold, self.recovery_threshold,
        )

        phases = list(KINEMATIC_PHASE_COLORS.keys())
        valid_animals = set(recovery_df["animal"])
        reach = reach[
            reach["subject_id"].isin(valid_animals)
            & reach["phase"].isin(phases)
        ].copy()

        print(f"  Reaches after filtering: {len(reach)}", flush=True)

        return {
            "reach": reach,
            "recovery_df": recovery_df,
            "n_outcome_excluded": n_outcome_excl,
            "n_plausible_excluded": total_plausible_excl,
            "features_excluded": feat_excl,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        recovery_df = data["recovery_df"]
        min_n = self.min_reaches

        rec_animals = set(recovery_df[recovery_df["recovered"]]["animal"])
        norec_animals = set(recovery_df[~recovery_df["recovered"]]["animal"])

        features_data = []
        stat_details = []

        for feat, label, cat in self.features:
            if feat not in reach.columns or reach[feat].isna().all():
                continue

            direction = RECOVERY_DIRECTION.get(feat, "neutral")
            feat_info = {
                "feat": feat, "label": label, "cat": cat,
                "direction": direction, "groups": {},
            }

            for group_name, group_animals in [
                ("Recovered", rec_animals),
                ("Not Recovered", norec_animals),
            ]:
                recovery_indices = []
                for subj in group_animals:
                    sdata = reach[reach["subject_id"] == subj]
                    pre_vals = sdata[
                        sdata["phase"] == "Pre-Injury"
                    ][feat].dropna()
                    post_vals = sdata[
                        sdata["phase"] == "Post-Injury_1"
                    ][feat].dropna()
                    rehab_vals = sdata[
                        sdata["phase"] == "Rehab_Pillar"
                    ][feat].dropna()

                    if (len(pre_vals) >= min_n
                            and len(post_vals) >= min_n
                            and len(rehab_vals) >= min_n):
                        pre_m = pre_vals.mean()
                        post_m = post_vals.mean()
                        rehab_m = rehab_vals.mean()
                        denom = pre_m - post_m
                        if abs(denom) > 1e-6:
                            ri = (rehab_m - post_m) / denom
                            recovery_indices.append(ri)

                group_info = {
                    "values": np.array(recovery_indices),
                    "n": len(recovery_indices),
                }

                if len(recovery_indices) >= 3:
                    ri_arr = np.array(recovery_indices)
                    group_info["mean"] = np.mean(ri_arr)
                    group_info["sem"] = np.std(ri_arr) / np.sqrt(len(ri_arr))
                    # One-sample t-test against 0 (no recovery)
                    try:
                        t_stat, p_val = sp_stats.ttest_1samp(ri_arr, 0)
                        d = (
                            np.mean(ri_arr) / np.std(ri_arr, ddof=1)
                            if np.std(ri_arr, ddof=1) > 0 else 0.0
                        )
                        group_info["t_stat"] = t_stat
                        group_info["p_val"] = p_val
                        group_info["d"] = d
                        detail = format_stat_result(
                            "t vs 0", t_stat, p_val, d=d,
                            n=len(ri_arr),
                        )
                        stat_details.append(
                            f"{label} ({group_name}): {detail}"
                        )
                    except Exception:
                        pass
                else:
                    group_info["mean"] = (
                        np.mean(recovery_indices)
                        if recovery_indices else np.nan
                    )
                    group_info["sem"] = 0

                feat_info["groups"][group_name] = group_info

            # Between-group Mann-Whitney
            rec_ri = feat_info["groups"]["Recovered"]["values"]
            norec_ri = feat_info["groups"]["Not Recovered"]["values"]
            if len(rec_ri) >= 3 and len(norec_ri) >= 3:
                try:
                    u_stat, u_p = sp_stats.mannwhitneyu(
                        rec_ri, norec_ri, alternative="two-sided",
                    )
                    feat_info["between_p"] = u_p
                    feat_info["between_stat"] = u_stat
                except Exception:
                    pass

            features_data.append(feat_info)

        # Sort by recovered group's mean RI descending
        features_data.sort(
            key=lambda x: x["groups"].get(
                "Recovered", {}
            ).get("mean", float("-inf")),
            reverse=True,
        )

        return {
            "features_data": features_data,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        features_data = results["features_data"]
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]

        if not features_data:
            ax.text(
                0.5, 0.5, "No features with sufficient data",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=14,
            )
            return

        y_pos = np.arange(len(features_data))
        bar_height = 0.35
        rng = np.random.default_rng(42)

        group_cfg = {
            "Recovered": {"color": "#2ECC71", "offset": -bar_height / 2},
            "Not Recovered": {"color": "#E74C3C", "offset": bar_height / 2},
        }

        for group_name, cfg in group_cfg.items():
            means = []
            sems = []
            valid_y = []
            all_dots_y = []
            all_dots_x = []

            for i, fd in enumerate(features_data):
                gdata = fd["groups"].get(group_name, {})
                m = gdata.get("mean", np.nan)
                s = gdata.get("sem", 0)
                if not np.isnan(m):
                    valid_y.append(y_pos[i])
                    means.append(m)
                    sems.append(s)

                # Individual dots
                vals = gdata.get("values", np.array([]))
                for v in vals:
                    all_dots_y.append(
                        y_pos[i] + cfg["offset"]
                        + rng.uniform(-0.08, 0.08)
                    )
                    all_dots_x.append(np.clip(v, -1.5, 2.5))

            # Bars
            ax.barh(
                [y + cfg["offset"] for y in valid_y],
                means, bar_height,
                xerr=sems, color=cfg["color"], alpha=0.6,
                edgecolor="white", linewidth=0.5,
                label=f"{group_name} (N={n_rec if group_name == 'Recovered' else n_norec})",
                capsize=2, zorder=3,
            )

            # Individual animal dots (Rule 6/19)
            if all_dots_x:
                ax.scatter(
                    all_dots_x, all_dots_y,
                    color=cfg["color"], s=15, alpha=0.5,
                    edgecolor="white", linewidth=0.2, zorder=4,
                )

        # Reference lines
        ax.axvline(
            x=1.0, color="#2ECC71", linestyle="--", linewidth=2,
            alpha=0.6, label="Full Recovery (1.0)", zorder=2,
        )
        ax.axvline(
            x=0.0, color="gray", linestyle=":", linewidth=1,
            alpha=0.5, label="No Recovery (0.0)", zorder=2,
        )

        # Y-axis labels with direction and category color
        feat_labels = []
        for fd in features_data:
            direction = fd.get("direction", "neutral")
            arrow = DIRECTION_ARROWS.get(direction, "")
            feat_labels.append(f"{fd['label']}{arrow}")

        ax.set_yticks(y_pos)
        ax.set_yticklabels(feat_labels, fontsize=9)
        for i, fd in enumerate(features_data):
            ax.get_yticklabels()[i].set_color(
                CATEGORY_COLORS.get(fd["cat"], "black")
            )

        # Between-group significance markers
        for i, fd in enumerate(features_data):
            p = fd.get("between_p")
            if p is not None and p < 0.05:
                ax.text(
                    2.3, y_pos[i], "*" if p >= 0.01 else "**",
                    ha="center", va="center", fontsize=10,
                    fontweight="bold", color="black",
                )

        ax.set_xlabel("Recovery Index", fontsize=12)
        ax.set_xlim(-1.5, 2.5)
        ax.legend(loc="lower right", fontsize=9, framealpha=0.9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.invert_yaxis()
        ax.grid(axis="x", alpha=0.15, zorder=0)

        ax.set_title(
            "Recovery Index: (Rehab - Post) / (Pre - Post)\n"
            "1.0 = full return to pre-injury | 0.0 = no change from "
            "post-injury",
            fontsize=13, fontweight="bold", pad=10,
        )

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        excl = _format_exclusion_report(
            data["n_outcome_excluded"],
            data["n_plausible_excluded"],
            data["features_excluded"],
        )
        stat_lines = (
            "\n  ".join(stat_details[:8]) if stat_details
            else "(insufficient data)"
        )

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion, CNT_01-04\n"
            f"SUBJECTS    N={n_rec} recovered + N={n_norec} not-recovered\n"
            f"RECOVERY    (rehab_test - post) / (pre - post) >= "
            f"{self.recovery_threshold} (pellet retrieval)\n"
            f"FORMULA     Recovery Index = (Rehab_mean - Post1_mean) / "
            f"(Pre_mean - Post1_mean) per subject per feature\n"
            f"            RI=1.0 = full return to pre-injury; "
            f"RI=0.0 = no change from acute post-injury\n"
            f"FILTER      Successful retrievals only; plausible ranges\n"
            f"EXCLUSIONS  {excl}\n"
            f"STATS       One-sample t-test vs 0 per group per feature; "
            f"Mann-Whitney U between groups (* on figure):\n"
            f"  {stat_lines}\n"
            f"PLOT        Bars = group mean +/- SEM; dots = individual "
            f"animals; * = between-group p<0.05"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        es_parts = [s for s in stat_details if "d=" in s]
        es_text = "; ".join(es_parts[:5]) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "Which kinematic features recover most during rehabilitation, "
                "and does recovery differ between functionally recovered "
                "and non-recovered animals?"
            ),
            method=(
                f"N={n_rec} recovered + N={n_norec} not-recovered from "
                f"CNT_01-04. Recovery Index = (Rehab - Post1) / (Pre - Post1) "
                f"per subject per feature. >={self.min_reaches} reaches per "
                f"phase. Successful retrievals only, plausible ranges."
            ),
            finding=(
                "Recovery indices vary across features and between groups. "
                "Recovered animals tend to show higher RI for spatial and "
                "velocity features, while postural features may not "
                "normalize even in recovered animals."
            ),
            analysis=(
                f"{stat_justification('t-test')} "
                "One-sample t-test against 0 per group per feature. "
                "Mann-Whitney U between groups."
            ),
            effect_sizes=es_text,
            confounds=(
                "RI undefined when pre==post (no deficit). Small recovered "
                "group may lack power. Feature independence not tested "
                "(multiple comparisons)."
            ),
            follow_up=(
                "Apply FDR correction for multiple features. Identify "
                "feature clusters with correlated recovery patterns."
            ),
        )


# ============================================================================
# Recipe: RehabKinematicChange
# ============================================================================

class RehabKinematicChange(FigureRecipe):
    """Session-by-session kinematic change during rehabilitation.

    Individual subject traces (not confidence bands) showing within-rehab
    trajectory for key features. Uses only rehab pillar TEST sessions
    (Rule 41), successful retrievals (Rule 31).
    """

    name = "rehab_kinematic_change"
    title = "Kinematic Change During Rehabilitation"
    category = "kinematic_recovery"
    data_sources = list(_DATA_SOURCES)
    figsize = (18, 14)

    def __init__(self, cohort_prefix="CNT_0",
                 learner_threshold=LEARNER_THRESHOLD,
                 recovery_threshold=RECOVERY_PELLET_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_session=2):
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.recovery_threshold = recovery_threshold
        self.features = features or TOP_FEATURES[:6]
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = (
            post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        )
        self.post_injury_2_4_dates = (
            post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        )
        self.rehab_pillar_cutoff = (
            rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        )
        self.min_reaches_per_session = min_reaches_per_session

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": self.recovery_threshold,
            "recovery_formula": "(rehab_test - post_injury) / (pre - post_injury)",
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_session": self.min_reaches_per_session,
            "outcome_filter": "successful retrievals only",
            "plausible_range_filter": "per-feature physiological bounds",
            "session_type": "rehab pillar TEST sessions only (not training)",
        }

    def create_axes(self, fig, plot_gs):
        n_feats = len(self.features)
        ncols = min(3, n_feats)
        nrows = (n_feats + ncols - 1) // ncols
        inner_gs = plot_gs.subgridspec(nrows, ncols, hspace=0.35, wspace=0.3)
        axes = {}
        for idx in range(n_feats):
            r, c = divmod(idx, ncols)
            axes[idx] = fig.add_subplot(inner_gs[r, c])
        for idx in range(n_feats, nrows * ncols):
            r, c = divmod(idx, ncols)
            empty_ax = fig.add_subplot(inner_gs[r, c])
            empty_ax.set_visible(False)
        return axes

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)

        reach, n_outcome_excl = _filter_successful_retrievals(reach)
        print(f"  Outcome filter: {n_outcome_excl} non-retrieved excluded",
              flush=True)

        reach = _assign_phases_multi_cohort(
            reach, surg,
            self.pre_injury_dates, self.post_injury_1_dates,
            self.post_injury_2_4_dates, self.rehab_pillar_cutoff,
        )

        total_plausible_excl = 0
        feat_excl = {}
        for feat, _, _ in self.features:
            reach, n_excl = _filter_plausible(reach, feat)
            total_plausible_excl += n_excl
            if n_excl > 0:
                feat_excl[feat] = n_excl

        recovery_df = _classify_recovery(
            ps, surg, self.learner_threshold, self.recovery_threshold,
        )

        # Keep only rehab pillar sessions
        rehab_reach = reach[reach["phase"] == "Rehab_Pillar"].copy()
        if rehab_reach.empty:
            print("  [!] No Rehab_Pillar reach data found", flush=True)

        # Assign rehab session number per animal (chronological)
        rehab_reach = rehab_reach.sort_values(
            ["subject_id", "session_date"]
        )
        day_map = {}
        for animal in rehab_reach["subject_id"].unique():
            dates = sorted(
                rehab_reach[
                    rehab_reach["subject_id"] == animal
                ]["session_date"].unique()
            )
            for i, d in enumerate(dates):
                day_map[(animal, d)] = i + 1
        rehab_reach["rehab_session"] = rehab_reach.apply(
            lambda r: day_map.get(
                (r["subject_id"], r["session_date"]), None
            ),
            axis=1,
        )

        valid_animals = set(recovery_df["animal"])
        rehab_reach = rehab_reach[
            rehab_reach["subject_id"].isin(valid_animals)
        ].copy()

        print(f"  Rehab reaches after filtering: {len(rehab_reach)}",
              flush=True)

        return {
            "rehab_reach": rehab_reach,
            "recovery_df": recovery_df,
            "n_outcome_excluded": n_outcome_excl,
            "n_plausible_excluded": total_plausible_excl,
            "features_excluded": feat_excl,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rehab_reach = data["rehab_reach"]
        recovery_df = data["recovery_df"]
        min_n = self.min_reaches_per_session

        rec_animals = sorted(
            recovery_df[recovery_df["recovered"]]["animal"]
        )
        norec_animals = sorted(
            recovery_df[~recovery_df["recovered"]]["animal"]
        )

        panels = []
        stat_details = []

        for feat, label, cat in self.features:
            if feat not in rehab_reach.columns:
                continue

            direction = RECOVERY_DIRECTION.get(feat, "neutral")
            panel = {
                "feat": feat, "label": label, "cat": cat,
                "direction": direction,
            }

            for group_name, group_animals in [
                ("rec", rec_animals), ("norec", norec_animals),
            ]:
                # Per-animal per-session means
                subj_traces = {}
                for animal in group_animals:
                    adata = rehab_reach[
                        (rehab_reach["subject_id"] == animal)
                        & rehab_reach[feat].notna()
                    ]
                    if adata.empty:
                        continue
                    sess_means = (
                        adata.groupby("rehab_session")[feat]
                        .agg(["mean", "count"])
                        .reset_index()
                    )
                    sess_means = sess_means[sess_means["count"] >= min_n]
                    if not sess_means.empty:
                        subj_traces[animal] = {
                            "sessions": sess_means["rehab_session"].values,
                            "means": sess_means["mean"].values,
                            "counts": sess_means["count"].values,
                        }
                panel[f"{group_name}_traces"] = subj_traces

                # Group mean per session
                all_sessions = sorted(set(
                    s for t in subj_traces.values()
                    for s in t["sessions"]
                ))
                group_means = []
                group_sems = []
                group_ns = []
                for sess in all_sessions:
                    vals = [
                        t["means"][list(t["sessions"]).index(sess)]
                        for t in subj_traces.values()
                        if sess in t["sessions"]
                    ]
                    if vals:
                        group_means.append(np.mean(vals))
                        group_sems.append(
                            np.std(vals) / np.sqrt(len(vals))
                            if len(vals) > 1 else 0
                        )
                        group_ns.append(len(vals))
                    else:
                        group_means.append(np.nan)
                        group_sems.append(0)
                        group_ns.append(0)

                panel[f"{group_name}_sessions"] = all_sessions
                panel[f"{group_name}_means"] = group_means
                panel[f"{group_name}_sems"] = group_sems
                panel[f"{group_name}_ns"] = group_ns
                panel[f"{group_name}_n"] = len(subj_traces)

            # Spearman correlation (session number vs value) for recovered
            rec_traces = panel["rec_traces"]
            if rec_traces:
                all_sess = []
                all_vals = []
                for t in rec_traces.values():
                    all_sess.extend(t["sessions"])
                    all_vals.extend(t["means"])
                if len(all_sess) >= 5:
                    try:
                        rho, p = sp_stats.spearmanr(all_sess, all_vals)
                        panel["rec_spearman_rho"] = rho
                        panel["rec_spearman_p"] = p
                        stat_details.append(
                            f"{label} (recovered): Spearman rho={rho:.3f}, "
                            f"p={p:.4f}"
                        )
                    except Exception:
                        pass

            panels.append(panel)

        return {
            "panels": panels,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        panels = results["panels"]
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]

        group_cfg = {
            "rec": {"color": "#2ECC71",
                    "label": f"Recovered (N={n_rec})"},
            "norec": {"color": "#E74C3C",
                      "label": f"Not Recovered (N={n_norec})"},
        }

        fig.suptitle(
            f"{self.title}\n"
            f"Rehab Pillar TEST sessions only -- individual subjects",
            fontsize=14, fontweight="bold",
        )

        for idx, panel in enumerate(panels):
            if idx not in ax:
                break
            panel_ax = ax[idx]
            label = panel["label"]
            direction = panel.get("direction", "neutral")

            for group_name, cfg in group_cfg.items():
                traces = panel[f"{group_name}_traces"]
                color = cfg["color"]
                sessions = panel[f"{group_name}_sessions"]

                # Individual subject traces (Rule 6/18/19)
                for subj, t in traces.items():
                    panel_ax.plot(
                        t["sessions"], t["means"],
                        "-", color=color, alpha=0.3, linewidth=0.8,
                        zorder=2,
                    )
                    panel_ax.scatter(
                        t["sessions"], t["means"],
                        color=color, s=12, alpha=0.4, zorder=3,
                        edgecolor="white", linewidth=0.2,
                    )

                # Group mean line
                means = panel[f"{group_name}_means"]
                if sessions and any(not np.isnan(m) for m in means):
                    valid = [
                        (s, m) for s, m in zip(sessions, means)
                        if not np.isnan(m)
                    ]
                    if len(valid) >= 2:
                        panel_ax.plot(
                            [v[0] for v in valid],
                            [v[1] for v in valid],
                            "o-", color=color, linewidth=2.5,
                            markersize=6, label=cfg["label"],
                            zorder=6, markeredgecolor="white",
                            markeredgewidth=0.5,
                        )

            # Direction indicator
            arrow = DIRECTION_ARROWS.get(direction, "")
            if arrow:
                panel_ax.text(
                    0.02, 0.98, f"Recovery: {direction}{arrow}",
                    transform=panel_ax.transAxes, ha="left", va="top",
                    fontsize=7, fontstyle="italic",
                )

            # Spearman annotation for recovered group
            rho = panel.get("rec_spearman_rho")
            p = panel.get("rec_spearman_p")
            if rho is not None:
                panel_ax.text(
                    0.98, 0.02,
                    f"rho={rho:.2f}, p={p:.3f}",
                    transform=panel_ax.transAxes,
                    ha="right", va="bottom", fontsize=7,
                    bbox=dict(
                        boxstyle="round", facecolor="wheat", alpha=0.7,
                    ),
                )

            # N per session annotation
            rec_ns = panel.get("rec_ns", [])
            rec_sessions = panel.get("rec_sessions", [])
            for s, n in zip(rec_sessions, rec_ns):
                if n > 0:
                    panel_ax.text(
                        s, panel_ax.get_ylim()[0],
                        f"n={n}", ha="center", va="bottom",
                        fontsize=6, color="gray",
                    )

            panel_ax.set_xlabel("Rehab Session", fontsize=10)
            panel_ax.set_ylabel(label, fontsize=10)
            panel_ax.set_title(label, fontsize=11, fontweight="bold")
            panel_ax.legend(fontsize=7, loc="upper right")
            panel_ax.spines["top"].set_visible(False)
            panel_ax.spines["right"].set_visible(False)
            panel_ax.grid(axis="y", alpha=0.15, zorder=0)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        excl = _format_exclusion_report(
            data["n_outcome_excluded"],
            data["n_plausible_excluded"],
            data["features_excluded"],
        )
        stat_lines = (
            "\n  ".join(stat_details[:6]) if stat_details
            else "(insufficient data)"
        )

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion, CNT_01-04\n"
            f"SUBJECTS    N={n_rec} recovered + N={n_norec} not-recovered\n"
            f"RECOVERY    (rehab_test - post) / (pre - post) >= "
            f"{self.recovery_threshold}\n"
            f"METRIC      Per-animal per-session mean (rehab pillar TEST "
            f"sessions only, not training -- Rule 41)\n"
            f"FILTER      Successful retrievals only; plausible ranges; "
            f">={self.min_reaches_per_session} reaches per session\n"
            f"EXCLUSIONS  {excl}\n"
            f"STATS       Spearman rank correlation (session # vs feature "
            f"value) in recovered group:\n  {stat_lines}\n"
            f"PLOT        Thin lines = individual subjects; thick = group "
            f"mean. Title says 'change' not 'learning' (Rule 47)."
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        n_rec = results["n_rec"]
        n_norec = results["n_norec"]
        stat_details = results["stat_details"]
        es_text = "; ".join(stat_details[:5]) if stat_details else "Not computed"

        return FigureLegend(
            question=(
                "Do kinematic features change progressively during "
                "rehabilitation, or does change occur abruptly?"
            ),
            method=(
                f"N={n_rec} recovered + N={n_norec} not-recovered from "
                f"CNT_01-04. Per-animal per-session means on rehab pillar "
                f"TEST sessions (not training, Rule 41). Successful "
                f"retrievals only, plausible ranges."
            ),
            finding=(
                "Within-rehab kinematic trajectories show variable patterns. "
                "Some features show progressive change across sessions while "
                "others fluctuate without clear trend."
            ),
            analysis=(
                "Spearman rank correlation between session number and "
                "feature value tests for monotonic within-rehab change. "
                "Correlation, not causation -- changes may reflect "
                "re-engagement rather than motor learning (Rule 47)."
            ),
            effect_sizes=es_text,
            confounds=(
                "Session numbering ignores gaps between sessions (weekends, "
                "skipped days). N per session varies as not all animals have "
                "data at every session. Changes during rehab are NOT "
                "necessarily 'learning' -- may reflect re-engagement, "
                "attentional recovery, or other non-motor factors (Rule 47)."
            ),
            follow_up=(
                "Add attention/engagement metric alongside performance to "
                "distinguish motor improvement from re-engagement. Annotate "
                "gaps between sessions."
            ),
        )
