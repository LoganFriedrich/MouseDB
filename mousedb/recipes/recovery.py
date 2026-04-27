"""
Recovery prediction recipes -- migrated from predict_recovery.py.

Seven figures examining which behavioral trends at each stage predict
whether an animal recovers to pre-injury baseline after rehabilitation.

Recovery definition (Rule 40):
    recovery_ratio = (rehab - post_injury) / (pre_injury - post_injury)
    i.e. proportion of lost function restored, not raw ratio to baseline.
    Threshold: recovery_ratio >= 0.5 (50% of deficit restored).

All recipes filter to pillar tray only for cross-phase comparisons (Rule 22),
use canonical palettes (Rule 4), report Cohen's d (Rule 26), and include
full FigureLegend narratives (Rule 25).
"""

import warnings
from typing import Any, Dict, List, Optional

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd
from scipy import stats

from mousedb import MOUSEDB_ROOT
from mousedb.figures.palettes import (
    COHORT_COLORS,
    PHASE_COLORS,
    get_persistent_subject_colors,
    get_subject_label,
)
from mousedb.figures.annotations import add_stat_bracket
from mousedb.figures.legends import FigureLegend
from mousedb.figures.stats import (
    cohens_d,
    cohens_d_paired,
    format_stat_result,
    interpret_d,
    stat_justification,
)
from mousedb.recipes.base import DataSource, FigureRecipe

# Try sklearn for logistic regression (fig6)
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ============================================================================
# Constants
# ============================================================================

COHORTS = ["CNT_01", "CNT_02", "CNT_03", "CNT_04"]
LEARNER_EATEN_THRESHOLD = 5.0  # % eaten to qualify as learner

# Recovery definition (Rule 40): proportion of deficit restored
# recovery_ratio = (rehab - nadir) / (pre - nadir)
# Threshold: >= 0.5 means at least 50% of lost function restored
RECOVERY_THRESHOLD = 0.5
RECOVERY_THRESHOLD_LABEL = (
    "recovery_ratio >= 0.5, where recovery_ratio = "
    "(rehab_pillar - nadir) / (pre_injury - nadir). "
    "Measures proportion of lost function restored, not raw ratio to baseline. "
    "Animals that never lost function (deficit < 5pp) are excluded from "
    "recovered/not-recovered classification."
)
MIN_DEFICIT_PP = 5.0  # minimum deficit in percentage points to classify

# Recovery group colors (not from inline -- semantic meaning)
RECOVERED_COLOR = COHORT_COLORS.get("CNT_03", "#2ecc71")  # green
NOT_RECOVERED_COLOR = COHORT_COLORS.get("CNT_01", "#e74c3c")  # red
IMPROVED_COLOR = COHORT_COLORS.get("CNT_04", "#9b59b6")  # purple

# Phase windows for the animal table
PHASE_WINDOWS = [
    "pre_injury", "post_1", "post_2_4",
    "rehab_easy", "rehab_flat", "rehab_pillar",
]

TRAJECTORY_WINDOWS = ["pre_injury", "post_1", "post_2_4", "rehab_pillar"]
TRAJECTORY_LABELS = ["Pre-Injury", "Post 1wk", "Post 2-4wk", "Rehab Pillar"]


# ============================================================================
# Shared helper functions (migrated from predict_recovery.py)
# ============================================================================

def _load_source_data(cohorts=None):
    """Load pellet_scores, surgeries, and reach_data CSVs.

    Returns
    -------
    tuple of (ps, surgeries, reach) DataFrames
    """
    cohorts = cohorts or COHORTS
    dump_dir = MOUSEDB_ROOT / "database_dump"

    ps = pd.read_csv(dump_dir / "pellet_scores.csv", low_memory=False)
    surgeries = pd.read_csv(dump_dir / "surgeries.csv")
    reach = pd.read_csv(dump_dir / "reach_data.csv", low_memory=False)

    # Filter to requested cohorts
    cohort_pattern = "|".join(c + "_" for c in cohorts)
    ps = ps[ps["subject_id"].str.match(cohort_pattern)].copy()
    surgeries = surgeries[surgeries["subject_id"].str.match(cohort_pattern)].copy()
    reach = reach[reach["subject_id"].str.match(cohort_pattern)].copy()

    ps["cohort"] = ps["subject_id"].str[:6]
    ps["session_date"] = pd.to_datetime(ps["session_date"])
    surgeries["surgery_date"] = pd.to_datetime(surgeries["surgery_date"])
    reach["session_date"] = pd.to_datetime(reach["session_date"])
    reach["cohort"] = reach["subject_id"].str[:6]

    return ps, surgeries, reach


def _get_surgery_date(surgeries, cohort):
    """Get the earliest contusion surgery date for a cohort."""
    cohort_surg = surgeries[
        (surgeries["subject_id"].str.startswith(cohort))
        & (surgeries["surgery_type"] == "contusion")
    ]
    if cohort_surg.empty:
        return None
    return cohort_surg["surgery_date"].min()


def _infer_phases(ps_cohort, surgery_date):
    """Infer test_phase for rows where it is blank, using surgery date."""
    if surgery_date is None:
        return ps_cohort
    ps = ps_cohort.copy()
    blank_mask = ps["test_phase"].isna() | (ps["test_phase"] == "")
    if not blank_mask.any():
        return ps

    blank = ps[blank_mask].copy()
    sessions = (
        blank.groupby(["session_date", "tray_type"]).size()
        .reset_index(name="n")
        .sort_values("session_date")
    )
    pre_sessions = sessions[sessions["session_date"] < surgery_date].copy()
    post_sessions = sessions[sessions["session_date"] > surgery_date].copy()
    phase_map = {}

    if not pre_sessions.empty:
        flat_dates = sorted(
            pre_sessions[pre_sessions["tray_type"] == "F"]["session_date"].unique()
        )
        pillar_dates = sorted(
            pre_sessions[pre_sessions["tray_type"] == "P"]["session_date"].unique()
        )
        for i, d in enumerate(flat_dates):
            phase_map[(d, "F")] = f"Training_Flat_{i + 1}"
        n_pillar = len(pillar_dates)
        pre_injury_start = max(0, n_pillar - 3)
        for i, d in enumerate(pillar_dates):
            if i >= pre_injury_start:
                phase_map[(d, "P")] = (
                    f"Pre-Injury_Test_Pillar_{i - pre_injury_start + 1}"
                )
            else:
                phase_map[(d, "P")] = f"Training_Pillar_{i + 1}"

    if not post_sessions.empty:
        post_dates = sorted(post_sessions["session_date"].unique())
        blocks = [[post_dates[0]]]
        for i in range(1, len(post_dates)):
            gap = (post_dates[i] - post_dates[i - 1]).days
            if gap > 5:
                blocks.append([])
            blocks[-1].append(post_dates[i])

        post_injury_count = 0
        for block in blocks:
            block_sessions = post_sessions[
                post_sessions["session_date"].isin(block)
            ]
            n_days = len(block)
            if n_days <= 2:
                post_injury_count += 1
                for d in block:
                    for _, row in block_sessions[
                        block_sessions["session_date"] == d
                    ].iterrows():
                        phase_map[(d, row["tray_type"])] = (
                            f"Post-Injury_Test_{post_injury_count}"
                        )
            else:
                for d in sorted(block):
                    day_sessions = block_sessions[
                        block_sessions["session_date"] == d
                    ]
                    for _, row in day_sessions.iterrows():
                        tray = row["tray_type"]
                        sub = {"E": "Easy", "F": "Flat"}.get(tray, "Pillar")
                        phase_map[(d, tray)] = f"Rehab_{sub}"

    for idx in ps[blank_mask].index:
        key = (ps.loc[idx, "session_date"], ps.loc[idx, "tray_type"])
        if key in phase_map:
            ps.loc[idx, "test_phase"] = phase_map[key]
    return ps


def _fix_mislabeled_rehab(ps_cohort, surgery_date):
    """Reclassify post-injury sessions that are actually rehab blocks."""
    if surgery_date is None:
        return ps_cohort
    ps = ps_cohort.copy()
    post_mask = ps["test_phase"].str.contains("Post-Injury", case=False, na=False)
    if not post_mask.any():
        return ps

    post_data = ps[post_mask].copy()
    post_sessions = (
        post_data.groupby(["session_date", "tray_type"]).size()
        .reset_index(name="n")
        .sort_values("session_date")
    )
    post_dates = sorted(post_sessions["session_date"].unique())
    if len(post_dates) < 5:
        return ps

    blocks = [[post_dates[0]]]
    for i in range(1, len(post_dates)):
        gap = (post_dates[i] - post_dates[i - 1]).days
        if gap > 5:
            blocks.append([])
        blocks[-1].append(post_dates[i])

    for block in blocks:
        if len(block) <= 4:
            continue
        block_sessions = post_sessions[post_sessions["session_date"].isin(block)]
        tray_types = set(block_sessions["tray_type"].unique())
        if "E" in tray_types or ("F" in tray_types and len(block) > 5):
            first_date = block[0]
            first_trays = set(
                block_sessions[block_sessions["session_date"] == first_date][
                    "tray_type"
                ]
            )
            rehab_dates = (
                block[1:] if first_trays == {"P"} and len(block) > 1 else block
            )
            for d in rehab_dates:
                day_trays = block_sessions[block_sessions["session_date"] == d]
                for _, row in day_trays.iterrows():
                    mask = (ps["session_date"] == d) & (
                        ps["tray_type"] == row["tray_type"]
                    )
                    tray = row["tray_type"]
                    sub = {"E": "Easy", "F": "Flat"}.get(tray, "Pillar")
                    ps.loc[mask, "test_phase"] = f"Rehab_{sub}"
    return ps


def _classify_phase_to_window(phase):
    """Map a test_phase string to a canonical window name."""
    if pd.isna(phase) or phase == "":
        return None
    pl = str(phase).lower()
    if "pre-injury" in pl or "pre_injury" in pl:
        return "pre_injury"
    if "post-injury" in pl or "post_injury" in pl:
        for sep in ["test_", "test "]:
            if sep in pl:
                try:
                    num = int(pl.split(sep)[-1])
                    if num == 1:
                        return "post_1"
                    elif 2 <= num <= 4:
                        return "post_2_4"
                except (ValueError, IndexError):
                    pass
        return "post_1"
    if "rehab" in pl and "pillar" in pl:
        return "rehab_pillar"
    if "rehab" in pl and "easy" in pl:
        return "rehab_easy"
    if "rehab" in pl and "flat" in pl:
        return "rehab_flat"
    if "training_pillar" in pl:
        return None
    return None


def _build_animal_table(ps, learner_threshold=LEARNER_EATEN_THRESHOLD,
                        recovery_threshold=RECOVERY_THRESHOLD,
                        min_deficit=MIN_DEFICIT_PP):
    """Build a per-animal summary with performance at each phase window.

    Uses deficit-aware recovery ratio (Rule 40):
        recovery_ratio = (rehab - nadir) / (pre - nadir)
    """
    ps = ps.copy()
    ps["window"] = ps["test_phase"].apply(_classify_phase_to_window)

    # Fallback: use Training_Pillar as pre-injury proxy
    if (ps["window"] == "pre_injury").sum() == 0:
        tp = ps[
            ps["test_phase"].str.contains("Training_Pillar", case=False, na=False)
        ]
        if not tp.empty:
            tp_dates = sorted(tp["session_date"].unique())
            last3 = tp_dates[-3:] if len(tp_dates) >= 3 else tp_dates
            mask = ps["session_date"].isin(last3) & ps[
                "test_phase"
            ].str.contains("Training_Pillar", case=False, na=False)
            ps.loc[mask, "window"] = "pre_injury"

    # Fallback: any rehab as rehab_pillar
    if (ps["window"] == "rehab_pillar").sum() == 0:
        rp = ps[
            ps["test_phase"].str.contains(
                "Rehab_Pillar|Rehab Pillar", case=False, na=False
            )
        ]
        if not rp.empty:
            ps.loc[rp.index, "window"] = "rehab_pillar"

    ps["eaten_bin"] = (ps["score"] >= 2).astype(int)
    ps["contacted_bin"] = (ps["score"] >= 1).astype(int)

    windowed = ps[ps["window"].isin(PHASE_WINDOWS)]
    animals = sorted(windowed["subject_id"].unique())
    rows = []
    for animal in animals:
        adf = windowed[windowed["subject_id"] == animal]
        cohort = animal[:6]
        row = {"animal": animal, "cohort": cohort}
        for w in PHASE_WINDOWS:
            wdf = adf[adf["window"] == w]
            if len(wdf) > 0:
                row[f"{w}_eaten"] = wdf["eaten_bin"].mean() * 100
                row[f"{w}_contacted"] = wdf["contacted_bin"].mean() * 100
                row[f"{w}_n_pellets"] = len(wdf)
            else:
                row[f"{w}_eaten"] = np.nan
                row[f"{w}_contacted"] = np.nan
                row[f"{w}_n_pellets"] = 0
        rows.append(row)

    df = pd.DataFrame(rows)

    # Derived features
    df["acute_deficit_eaten"] = df["pre_injury_eaten"] - df["post_1_eaten"]
    df["acute_deficit_contacted"] = (
        df["pre_injury_contacted"] - df["post_1_contacted"]
    )
    df["chronic_slope_eaten"] = df["post_2_4_eaten"] - df["post_1_eaten"]
    df["chronic_slope_contacted"] = (
        df["post_2_4_contacted"] - df["post_1_contacted"]
    )
    df["rehab_recovery_eaten"] = df["rehab_pillar_eaten"] - df["post_1_eaten"]
    df["rehab_recovery_contacted"] = (
        df["rehab_pillar_contacted"] - df["post_1_contacted"]
    )

    # Nadir (worst post-injury score)
    df["nadir_eaten"] = df[["post_1_eaten", "post_2_4_eaten"]].min(axis=1)
    df["nadir_contacted"] = df[["post_1_contacted", "post_2_4_contacted"]].min(
        axis=1
    )

    # Deficit-aware recovery ratio (Rule 40)
    # recovery_ratio = (rehab - nadir) / (pre - nadir)
    deficit_eaten = df["pre_injury_eaten"] - df["nadir_eaten"]
    df["recovery_ratio_eaten"] = np.where(
        deficit_eaten > min_deficit,
        (df["rehab_pillar_eaten"] - df["nadir_eaten"]) / deficit_eaten,
        np.nan,
    )
    deficit_contacted = df["pre_injury_contacted"] - df["nadir_contacted"]
    df["recovery_ratio_contacted"] = np.where(
        deficit_contacted > min_deficit,
        (df["rehab_pillar_contacted"] - df["nadir_contacted"]) / deficit_contacted,
        np.nan,
    )

    # Recovery classification
    df["recovered_eaten"] = df["recovery_ratio_eaten"] >= recovery_threshold
    df["recovered_contacted"] = (
        df["recovery_ratio_contacted"] >= recovery_threshold
    )

    # Improvement from nadir
    df["rehab_vs_nadir_eaten"] = df["rehab_pillar_eaten"] - df["nadir_eaten"]
    df["rehab_vs_nadir_contacted"] = (
        df["rehab_pillar_contacted"] - df["nadir_contacted"]
    )

    # Rehab progression: early (easy/flat) vs late (pillar)
    df["rehab_early_eaten"] = df[
        ["rehab_easy_eaten", "rehab_flat_eaten"]
    ].mean(axis=1)
    df["rehab_early_contacted"] = df[
        ["rehab_easy_contacted", "rehab_flat_contacted"]
    ].mean(axis=1)

    # Learner filter
    df["is_learner"] = df["pre_injury_eaten"] > learner_threshold

    return df


def _build_session_level_rehab(ps):
    """Build session-level rehab data to analyze learning curves."""
    ps = ps.copy()
    ps["window"] = ps["test_phase"].apply(_classify_phase_to_window)

    rehab = ps[
        ps["window"].isin(["rehab_easy", "rehab_flat", "rehab_pillar"])
    ].copy()
    if rehab.empty:
        return pd.DataFrame()

    rehab["eaten_bin"] = (rehab["score"] >= 2).astype(int)
    rehab["contacted_bin"] = (rehab["score"] >= 1).astype(int)

    session_stats = (
        rehab.groupby(["subject_id", "session_date", "tray_type"])
        .agg(
            eaten_pct=("eaten_bin", lambda x: x.mean() * 100),
            contacted_pct=("contacted_bin", lambda x: x.mean() * 100),
            n_pellets=("score", "count"),
        )
        .reset_index()
    )
    session_stats["cohort"] = session_stats["subject_id"].str[:6]
    session_stats = session_stats.sort_values(["subject_id", "session_date"])
    session_stats["rehab_day"] = (
        session_stats.groupby("subject_id").cumcount() + 1
    )
    return session_stats


def _prepare_all_data(cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
    """Full data pipeline: load, infer phases, build tables.

    Returns
    -------
    dict with keys: animal_df, rehab_sessions, ps, surgeries, reach
    """
    cohorts = cohorts or COHORTS
    ps, surgeries, reach = _load_source_data(cohorts)

    print("  Inferring phases...", flush=True)
    ps_all = []
    for cohort in cohorts:
        ps_cohort = ps[ps["cohort"] == cohort].copy()
        sd = _get_surgery_date(surgeries, cohort)
        ps_cohort = _infer_phases(ps_cohort, sd)
        ps_cohort = _fix_mislabeled_rehab(ps_cohort, sd)
        ps_all.append(ps_cohort)
    ps = pd.concat(ps_all, ignore_index=True)

    # Filter to pillar tray for cross-phase comparisons (Rule 22)
    ps_pillar = ps[ps["tray_type"] == "P"].copy()
    print(
        f"  Pillar-only filter: {len(ps_pillar)}/{len(ps)} rows retained",
        flush=True,
    )

    animal_df = _build_animal_table(ps_pillar, learner_threshold=learner_threshold)
    rehab_sessions = _build_session_level_rehab(ps)

    learners = animal_df[animal_df["is_learner"]]
    paired = learners.dropna(subset=["pre_injury_eaten", "rehab_pillar_eaten"])
    print(
        f"  Animals: {len(animal_df)} total, {len(learners)} learners, "
        f"{len(paired)} with pre+rehab data",
        flush=True,
    )

    return {
        "animal_df": animal_df,
        "rehab_sessions": rehab_sessions,
        "ps": ps,
        "surgeries": surgeries,
        "reach": reach,
    }


def _get_paired(animal_df):
    """Get learners with both pre-injury and rehab pillar data."""
    learners = animal_df[animal_df["is_learner"]].copy()
    return learners.dropna(subset=["pre_injury_eaten", "rehab_pillar_eaten"]).copy()


def _stars(p):
    """Convert p-value to significance stars (ASCII only)."""
    if p < 0.0001:
        return "****"
    if p < 0.001:
        return "***"
    if p < 0.01:
        return "**"
    if p < 0.05:
        return "*"
    return "ns"


# ============================================================================
# Recipe 1: RecoveryOverview
# ============================================================================

class RecoveryOverview(FigureRecipe):
    """Recovery classification breakdown and basic predictor relationships.

    Six panels: cohort breakdown, recovery ratio distribution,
    pre-injury vs recovery, acute deficit vs recovery,
    chronic slope vs recovery, and summary statistics.
    """

    name = "recovery_overview"
    title = "Recovery Predictors: Overview"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (18, 14)

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "recovery_formula": "(rehab - nadir) / (pre - nadir)",
            "min_deficit_pp": MIN_DEFICIT_PP,
            "tray_filter": "pillar only",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        animal_df = all_data["animal_df"]
        paired = _get_paired(animal_df)

        if len(paired) < 3:
            print("  [!] Fewer than 3 paired animals available", flush=True)

        return {
            "animal_df": animal_df,
            "paired": paired,
            "n_total": len(animal_df),
            "n_learners": animal_df["is_learner"].sum(),
            "n_paired": len(paired),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        paired = data["paired"]
        if len(paired) < 3:
            return {"correlations": [], "effect_sizes": {}}

        # Spearman correlations with recovery ratio
        predictors = [
            ("pre_injury_eaten", "Pre-injury eaten %"),
            ("acute_deficit_eaten", "Acute deficit (pre - post 1wk)"),
            ("chronic_slope_eaten", "Chronic slope (post 2-4wk - post 1wk)"),
            ("nadir_eaten", "Nadir eaten %"),
        ]

        correlations = []
        effect_sizes = {}
        for feat, label in predictors:
            valid = paired.dropna(subset=[feat, "recovery_ratio_eaten"])
            if len(valid) >= 5:
                r, p = stats.spearmanr(valid[feat], valid["recovery_ratio_eaten"])
                correlations.append({
                    "feature": label, "r": r, "p": p, "n": len(valid),
                })
                effect_sizes[label] = f"r={r:.2f}"
                detail = format_stat_result(
                    "Spearman", r, p, n=len(valid), alternative="two-sided",
                )
                print(f"    {label}: {detail}", flush=True)

        return {
            "correlations": correlations,
            "effect_sizes": effect_sizes,
        }

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 3, hspace=0.35, wspace=0.35)
        axes = {}
        labels = ["cohort_bar", "ratio_hist", "pre_vs_recovery",
                   "acute_vs_recovery", "chronic_vs_recovery", "summary"]
        for i, label in enumerate(labels):
            r, c = divmod(i, 3)
            axes[label] = fig.add_subplot(inner[r, c])
        return axes

    def plot(self, data, results, fig, ax, theme):
        paired = data["paired"]
        if len(paired) < 3:
            ax["summary"].text(
                0.5, 0.5, "Insufficient data (N < 3)",
                transform=ax["summary"].transAxes, ha="center", fontsize=14,
            )
            return

        cohorts_present = sorted(paired["cohort"].unique())
        subject_colors = get_persistent_subject_colors(
            sorted(paired["animal"].unique())
        )

        # -- 1A: Stacked bar of recovery classification per cohort --
        a = ax["cohort_bar"]
        n_rec, n_imp, n_non, cohort_labels = [], [], [], []
        for c in cohorts_present:
            cdf = paired[paired["cohort"] == c]
            nr = cdf["recovered_eaten"].sum()
            imp = (
                (~cdf["recovered_eaten"]) & (cdf["rehab_vs_nadir_eaten"] > 0)
            ).sum()
            non = len(cdf) - nr - imp
            n_rec.append(nr)
            n_imp.append(imp)
            n_non.append(non)
            cohort_labels.append(f"{c}\n(N={len(cdf)})")

        x = np.arange(len(cohorts_present))
        a.bar(x, n_rec, color=RECOVERED_COLOR, label="Recovered", alpha=0.8)
        a.bar(x, n_imp, bottom=n_rec, color=IMPROVED_COLOR,
              label="Improved from nadir", alpha=0.8)
        a.bar(
            x, n_non,
            bottom=[r + i for r, i in zip(n_rec, n_imp)],
            color=NOT_RECOVERED_COLOR, label="No improvement", alpha=0.8,
        )
        a.set_xticks(x)
        a.set_xticklabels(cohort_labels, fontsize=9)
        a.set_ylabel("Number of Animals")
        a.set_title("Recovery Classification by Cohort", fontweight="bold",
                     fontsize=11)
        a.legend(fontsize=7)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        # -- 1B: Recovery ratio distribution --
        a = ax["ratio_hist"]
        rr = paired["recovery_ratio_eaten"].dropna()
        a.hist(rr * 100, bins=15, color=PHASE_COLORS["Pre-Injury"],
               alpha=0.7, edgecolor="black")
        a.axvline(
            x=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
            linestyle="--", linewidth=2,
            label=f"Threshold ({RECOVERY_THRESHOLD * 100:.0f}%)",
        )
        a.axvline(x=100, color="gray", linestyle=":", linewidth=1.5,
                   label="Full recovery (100%)")
        a.set_xlabel("Recovery Ratio (%)")
        a.set_ylabel("Count")
        a.set_title("Recovery Ratio Distribution", fontweight="bold",
                     fontsize=11)
        a.legend(fontsize=7)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        if len(rr) > 0:
            a.text(
                0.98, 0.95,
                f"Mean: {np.nanmean(rr * 100):.1f}%\n"
                f"Median: {np.nanmedian(rr * 100):.1f}%",
                transform=a.transAxes, ha="right", va="top", fontsize=8,
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
                family="monospace",
            )

        # Scatter helper
        def _scatter_vs_recovery(axis, x_col, x_label, title_str):
            valid = paired.dropna(subset=[x_col, "recovery_ratio_eaten"])
            colors = [
                COHORT_COLORS.get(c, "#888888") for c in valid["cohort"]
            ]
            axis.scatter(
                valid[x_col], valid["recovery_ratio_eaten"] * 100,
                c=colors, s=50, alpha=0.7, edgecolor="black", linewidth=0.5,
            )
            axis.axhline(
                y=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
                linestyle="--", linewidth=1, alpha=0.6,
            )
            axis.set_xlabel(x_label, fontsize=10)
            axis.set_ylabel("Recovery Ratio (%)", fontsize=10)
            axis.set_title(title_str, fontweight="bold", fontsize=11)
            axis.spines["top"].set_visible(False)
            axis.spines["right"].set_visible(False)

            if len(valid) >= 5:
                r, p = stats.spearmanr(
                    valid[x_col], valid["recovery_ratio_eaten"]
                )
                axis.text(
                    0.02, 0.98, f"r={r:.3f}\np={p:.4f}",
                    transform=axis.transAxes, ha="left", va="top", fontsize=8,
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.7),
                    family="monospace",
                )

        _scatter_vs_recovery(
            ax["pre_vs_recovery"], "pre_injury_eaten",
            "Pre-Injury Eaten %", "Pre-Injury Performance vs Recovery",
        )
        _scatter_vs_recovery(
            ax["acute_vs_recovery"], "acute_deficit_eaten",
            "Acute Deficit (Pre - Post 1wk)", "Acute Deficit vs Recovery",
        )
        _scatter_vs_recovery(
            ax["chronic_vs_recovery"], "chronic_slope_eaten",
            "Chronic Slope (Post 2-4wk - Post 1wk)",
            "Spontaneous Recovery Slope vs Rehab Recovery",
        )

        # -- Summary panel --
        a = ax["summary"]
        a.axis("off")
        legend_elements = [
            Line2D([0], [0], marker="o", color="w",
                   markerfacecolor=COHORT_COLORS.get(c, "#888"), markersize=10,
                   label=c)
            for c in cohorts_present
        ]
        legend_elements.append(
            Line2D([0], [0], color=RECOVERED_COLOR, linestyle="--",
                   label=f"{RECOVERY_THRESHOLD * 100:.0f}% recovery line")
        )
        a.legend(handles=legend_elements, loc="upper center", fontsize=9,
                 title="Cohorts", title_fontsize=10)

        n_total = len(paired)
        n_recovered = int(paired["recovered_eaten"].sum())
        n_improved = int(
            ((~paired["recovered_eaten"]) & (paired["rehab_vs_nadir_eaten"] > 0)
             ).sum()
        )
        n_none = n_total - n_recovered - n_improved
        rr_mean = paired["recovery_ratio_eaten"].mean() * 100
        rr_med = paired["recovery_ratio_eaten"].median() * 100

        summary = (
            f"SUMMARY (Eaten metric)\n"
            f"{'-' * 35}\n"
            f"Total learners:     {n_total:3d}\n"
            f"Recovered (>={RECOVERY_THRESHOLD * 100:.0f}%): "
            f"{n_recovered:3d}  ({n_recovered / n_total * 100:.0f}%)\n"
            f"Improved from nadir:{n_improved:3d}  "
            f"({n_improved / n_total * 100:.0f}%)\n"
            f"No improvement:     {n_none:3d}  "
            f"({n_none / n_total * 100:.0f}%)\n"
            f"{'-' * 35}\n"
            f"Mean recovery ratio: {rr_mean:.1f}%\n"
            f"Median:              {rr_med:.1f}%"
        )
        a.text(
            0.5, 0.3, summary, transform=a.transAxes, ha="center",
            va="center", fontsize=9, family="monospace",
            bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.9),
        )

    def methodology_text(self, data, results):
        paired = data["paired"]
        cohort_str = ", ".join(data["cohorts"])
        corr_lines = []
        for c in results.get("correlations", []):
            corr_lines.append(
                f"  {c['feature']}: r={c['r']:.3f}, p={c['p']:.4f} "
                f"(n={c['n']})"
            )
        corr_text = "\n".join(corr_lines) if corr_lines else "  (none computed)"

        return (
            f"EXPERIMENT  Skilled reaching, single-pellet retrieval, "
            f"CST lesion model\n"
            f"SUBJECTS    N={len(paired)} learners (>={self.learner_threshold}% "
            f"eaten pre-injury) from {cohort_str}\n"
            f"TRAY FILTER Pillar tray sessions only (Rule 22)\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"STATISTICS  Spearman rank correlation with recovery ratio\n"
            f"{corr_text}\n"
            f"PLOT        Stacked bars = cohort recovery breakdown. "
            f"Scatters = predictor vs recovery ratio. "
            f"Dashed line = recovery threshold."
        )

    def figure_legend(self, data, results):
        es_parts = []
        for c in results.get("correlations", []):
            if c["p"] < 0.05:
                es_parts.append(f"{c['feature']}: r={c['r']:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "No significant correlations"

        return FigureLegend(
            question=(
                "Which pre-injury and post-injury behavioral features predict "
                "recovery of pellet retrieval after rehabilitation?"
            ),
            method=(
                f"N={data['n_paired']} learners "
                f"(>={self.learner_threshold}% eaten pre-injury) from "
                f"{', '.join(data['cohorts'])}. Pillar tray sessions only. "
                f"Recovery = proportion of deficit restored "
                f"(rehab - nadir) / (pre - nadir)."
            ),
            finding=(
                "Recovery ratio distribution and correlations with "
                "pre-injury performance, acute deficit magnitude, and "
                "chronic spontaneous recovery slope."
            ),
            analysis=(
                f"{stat_justification('spearman')} "
                f"Spearman rank correlations used because recovery ratio "
                f"is not normally distributed."
            ),
            effect_sizes=es_text,
            confounds=(
                "Cohort effects not controlled (different injury timing, "
                "housing conditions). Pillar tray difficulty may vary "
                "across cohorts."
            ),
            follow_up=(
                "Do kinematic features predict recovery independently of "
                "pellet retrieval rate? Does contacted% predict eaten% recovery?"
            ),
        )


# ============================================================================
# Recipe 2: RecoveredVsNot
# ============================================================================

class RecoveredVsNot(FigureRecipe):
    """Box plots comparing behavioral metrics between recovered and
    non-recovered animals, with individual data points and Mann-Whitney U.
    """

    name = "recovered_vs_not"
    title = "Recovered vs Non-Recovered Animals"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (20, 12)

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "recovery_formula": "(rehab - nadir) / (pre - nadir)",
            "tray_filter": "pillar only",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        paired = _get_paired(all_data["animal_df"])
        rec = paired[paired["recovered_eaten"]]
        norec = paired[~paired["recovered_eaten"]]
        return {
            "paired": paired,
            "rec": rec,
            "norec": norec,
            "n_rec": len(rec),
            "n_norec": len(norec),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rec = data["rec"]
        norec = data["norec"]
        if len(rec) < 2 or len(norec) < 2:
            return {"tests": [], "effect_sizes": {}}

        features = [
            ("pre_injury_eaten", "Pre-Injury Eaten %"),
            ("pre_injury_contacted", "Pre-Injury Contacted %"),
            ("acute_deficit_eaten", "Acute Deficit (Pre - Post 1wk)"),
            ("post_1_eaten", "Post 1wk Eaten %"),
            ("post_2_4_eaten", "Post 2-4wk Eaten %"),
            ("chronic_slope_eaten", "Chronic Slope"),
            ("nadir_eaten", "Nadir Eaten %"),
            ("rehab_vs_nadir_eaten", "Rehab - Nadir Eaten %"),
        ]

        tests = []
        effect_sizes = {}
        for feat, label in features:
            rv = rec[feat].dropna()
            nv = norec[feat].dropna()
            if len(rv) < 2 or len(nv) < 2:
                continue
            try:
                u_stat, u_p = stats.mannwhitneyu(rv, nv, alternative="two-sided")
                d = cohens_d(rv.values, nv.values)
                detail = format_stat_result(
                    "Mann-Whitney U", u_stat, u_p, d=d,
                    n=len(rv) + len(nv), alternative="two-sided",
                )
                tests.append({
                    "feature": feat, "label": label,
                    "u": u_stat, "p": u_p, "d": d, "detail": detail,
                })
                effect_sizes[label] = f"d={d:.2f} ({interpret_d(d)})"
                print(f"    {detail}", flush=True)
            except Exception:
                pass

        return {"tests": tests, "effect_sizes": effect_sizes}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 4, hspace=0.4, wspace=0.35)
        return np.array([
            [fig.add_subplot(inner[r, c]) for c in range(4)] for r in range(2)
        ])

    def plot(self, data, results, fig, ax, theme):
        rec = data["rec"]
        norec = data["norec"]
        if len(rec) < 2 or len(norec) < 2:
            ax[0, 0].text(0.5, 0.5, "Insufficient groups",
                          transform=ax[0, 0].transAxes, ha="center")
            return

        features = [
            ("pre_injury_eaten", "Pre-Injury\nEaten %"),
            ("pre_injury_contacted", "Pre-Injury\nContacted %"),
            ("acute_deficit_eaten", "Acute Deficit\n(Pre - Post 1wk)"),
            ("post_1_eaten", "Post 1wk\nEaten %"),
            ("post_2_4_eaten", "Post 2-4wk\nEaten %"),
            ("chronic_slope_eaten", "Chronic Slope\n(Post 2-4 - Post 1)"),
            ("nadir_eaten", "Nadir\nEaten %"),
            ("rehab_vs_nadir_eaten", "Rehab - Nadir\nEaten %"),
        ]

        test_map = {t["feature"]: t for t in results.get("tests", [])}

        for idx, (feat, label) in enumerate(features):
            a = ax[idx // 4, idx % 4]
            rec_vals = rec[feat].dropna()
            norec_vals = norec[feat].dropna()

            if len(rec_vals) == 0 or len(norec_vals) == 0:
                a.text(0.5, 0.5, "No data", transform=a.transAxes, ha="center")
                a.set_title(label, fontsize=9, fontweight="bold")
                continue

            bp = a.boxplot(
                [rec_vals, norec_vals], patch_artist=True, widths=0.6,
                medianprops=dict(color="black", linewidth=2),
            )
            bp["boxes"][0].set_facecolor(RECOVERED_COLOR)
            bp["boxes"][0].set_alpha(0.6)
            bp["boxes"][1].set_facecolor(NOT_RECOVERED_COLOR)
            bp["boxes"][1].set_alpha(0.6)

            rng = np.random.RandomState(42)
            for i, (vals, color) in enumerate([
                (rec_vals, RECOVERED_COLOR), (norec_vals, NOT_RECOVERED_COLOR),
            ]):
                jitter = rng.uniform(-0.12, 0.12, len(vals))
                a.scatter(
                    np.full(len(vals), i + 1) + jitter, vals,
                    color=color, s=30, alpha=0.6, edgecolor="white",
                    linewidth=0.3, zorder=5,
                )

            a.set_xticklabels(["Recovered", "Not\nRecovered"], fontsize=9)
            a.set_title(label, fontsize=9, fontweight="bold")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            t = test_map.get(feat)
            if t:
                a.text(
                    0.5, 0.98,
                    f"MWU p={t['p']:.4f} {_stars(t['p'])}\n"
                    f"d={t['d']:.2f}",
                    transform=a.transAxes, ha="center", va="top", fontsize=7,
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.7),
                )

        fig.suptitle(
            f"Recovered vs Non-Recovered Animals\n"
            f"Recovery: {RECOVERY_THRESHOLD_LABEL[:60]}...\n"
            f"Recovered: N={data['n_rec']} | Not Recovered: N={data['n_norec']}",
            fontsize=12, fontweight="bold",
        )

    def methodology_text(self, data, results):
        test_lines = []
        for t in results.get("tests", []):
            test_lines.append(f"  {t['detail']}")
        test_text = "\n".join(test_lines) if test_lines else "  (none)"

        return (
            f"EXPERIMENT  Skilled reaching, CST lesion model\n"
            f"SUBJECTS    Recovered N={data['n_rec']}, "
            f"Not Recovered N={data['n_norec']}\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"TRAY FILTER Pillar tray sessions only\n"
            f"STATISTICS  {stat_justification('mann-whitney')}\n"
            f"{test_text}\n"
            f"PLOT        Box plots with individual data points. "
            f"Green=recovered, red=not recovered."
        )

    def figure_legend(self, data, results):
        es_parts = []
        for t in results.get("tests", []):
            if t["p"] < 0.05:
                es_parts.append(
                    f"{t['label']}: d={t['d']:.2f} ({interpret_d(t['d'])})"
                )
        es_text = "; ".join(es_parts) if es_parts else "No significant differences"

        return FigureLegend(
            question=(
                "Do recovered and non-recovered animals differ on "
                "pre-injury, acute, and chronic behavioral metrics?"
            ),
            method=(
                f"Recovered (N={data['n_rec']}) vs Not Recovered "
                f"(N={data['n_norec']}), classified by deficit-aware "
                f"recovery ratio >= {RECOVERY_THRESHOLD * 100:.0f}%. "
                f"Pillar tray only."
            ),
            finding=(
                "Box plots show distribution of 8 behavioral metrics "
                "comparing the two recovery groups."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Cohen's d (independent, pooled SD) for effect sizes."
            ),
            effect_sizes=es_text,
            confounds=(
                "Small sample sizes in recovered group may limit power. "
                "Recovery threshold is somewhat arbitrary."
            ),
            follow_up=(
                "Which individual metrics best discriminate recovered "
                "from non-recovered in a multivariate model?"
            ),
        )


# ============================================================================
# Recipe 3: TrajectoryProfiles
# ============================================================================

class TrajectoryProfiles(FigureRecipe):
    """Mean trajectory lines (eaten and contacted) for recovered vs
    non-recovered animals across 4 experimental phases.
    """

    name = "trajectory_profiles"
    title = "Trajectory Profiles: Recovered vs Non-Recovered"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 9)

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "tray_filter": "pillar only",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        paired = _get_paired(all_data["animal_df"])
        rec = paired[paired["recovered_eaten"]]
        norec = paired[~paired["recovered_eaten"]]
        return {
            "paired": paired, "rec": rec, "norec": norec,
            "n_rec": len(rec), "n_norec": len(norec),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rec = data["rec"]
        norec = data["norec"]
        stat_details = []
        effect_sizes = {}

        # Compare groups at each timepoint (eaten)
        for w, label in zip(TRAJECTORY_WINDOWS, TRAJECTORY_LABELS):
            col = f"{w}_eaten"
            rv = rec[col].dropna()
            nv = norec[col].dropna()
            if len(rv) >= 2 and len(nv) >= 2:
                try:
                    u, p = stats.mannwhitneyu(rv, nv, alternative="two-sided")
                    d = cohens_d(rv.values, nv.values)
                    detail = format_stat_result(
                        "Mann-Whitney U", u, p, d=d,
                        n=len(rv) + len(nv), alternative="two-sided",
                    )
                    stat_details.append({"phase": label, "detail": detail,
                                         "p": p, "d": d})
                    effect_sizes[label] = f"d={d:.2f}"
                    print(f"    {label}: {detail}", flush=True)
                except Exception:
                    pass

        return {"stat_details": stat_details, "effect_sizes": effect_sizes}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(1, 2, wspace=0.3)
        return {
            "eaten": fig.add_subplot(inner[0]),
            "contacted": fig.add_subplot(inner[1]),
        }

    def plot(self, data, results, fig, ax, theme):
        rec = data["rec"]
        norec = data["norec"]
        if len(rec) < 1 or len(norec) < 1:
            return

        subject_colors = get_persistent_subject_colors(
            sorted(data["paired"]["animal"].unique())
        )

        for col_key, (metric, ylabel) in [
            ("eaten", ("eaten", "% Eaten")),
            ("contacted", ("contacted", "% Contacted")),
        ]:
            a = ax[col_key]
            for group, group_color, label_prefix in [
                (rec, RECOVERED_COLOR, "Recovered"),
                (norec, NOT_RECOVERED_COLOR, "Not Recovered"),
            ]:
                # Individual traces with per-subject colors
                for _, row in group.iterrows():
                    xs, ys = [], []
                    for i, w in enumerate(TRAJECTORY_WINDOWS):
                        val = row[f"{w}_{metric}"]
                        if not np.isnan(val):
                            xs.append(i)
                            ys.append(val)
                    s_color = subject_colors.get(row["animal"], group_color)
                    if len(xs) >= 2:
                        a.plot(xs, ys, "-", color=s_color, alpha=0.35,
                               linewidth=0.8)

                # Group mean + SEM
                mean_x, mean_y, sem_y = [], [], []
                for i, w in enumerate(TRAJECTORY_WINDOWS):
                    vals = group[f"{w}_{metric}"].dropna()
                    if len(vals) > 0:
                        mean_x.append(i)
                        mean_y.append(vals.mean())
                        sem_y.append(
                            stats.sem(vals) if len(vals) > 1 else 0
                        )
                mean_y = np.array(mean_y)
                sem_y = np.array(sem_y)
                a.plot(
                    mean_x, mean_y, "o-", color=group_color, linewidth=3,
                    markersize=10,
                    label=f"{label_prefix} (N={len(group)})", zorder=5,
                )
                a.fill_between(
                    mean_x, mean_y - sem_y, mean_y + sem_y,
                    color=group_color, alpha=0.2, zorder=4,
                )

            a.set_xticks(range(len(TRAJECTORY_LABELS)))
            a.set_xticklabels(TRAJECTORY_LABELS, fontsize=10)
            a.set_ylabel(ylabel, fontsize=12)
            a.set_title(f"{ylabel}: Mean Trajectories", fontsize=12,
                        fontweight="bold")
            a.legend(fontsize=9)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            a.set_xlim(-0.2, len(TRAJECTORY_LABELS) - 0.8)

    def methodology_text(self, data, results):
        stat_lines = "\n".join(
            f"  {s['detail']}" for s in results.get("stat_details", [])
        ) or "  (none)"
        return (
            f"EXPERIMENT  Skilled reaching, CST lesion model\n"
            f"SUBJECTS    Recovered N={data['n_rec']}, "
            f"Not Recovered N={data['n_norec']}\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"TRAY FILTER Pillar tray sessions only\n"
            f"PHASES      Pre-Injury -> Post 1wk -> Post 2-4wk -> Rehab Pillar\n"
            f"STATISTICS  Mann-Whitney U at each phase (eaten metric)\n"
            f"{stat_lines}\n"
            f"PLOT        Thin colored lines = individual subjects. "
            f"Thick lines = group mean +/- SEM."
        )

    def figure_legend(self, data, results):
        es_parts = []
        for s in results.get("stat_details", []):
            if s["p"] < 0.05:
                es_parts.append(f"{s['phase']}: d={s['d']:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "No significant phase differences"

        return FigureLegend(
            question=(
                "Do recovered and non-recovered animals show different "
                "trajectories across experimental phases?"
            ),
            method=(
                f"Recovered N={data['n_rec']} vs Not Recovered "
                f"N={data['n_norec']}. Pillar tray only. "
                f"4 phases: Pre-Injury, Post 1wk, Post 2-4wk, Rehab Pillar."
            ),
            finding=(
                "Mean trajectory lines show the pattern of decline and "
                "recovery for both eaten and contacted metrics."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Compared groups at each phase independently."
            ),
            effect_sizes=es_text,
            confounds=(
                "Individual traces reveal high variance. SEM may understate "
                "true spread. Rehab pillar may include training sessions "
                "(Rule 41 -- verify session labels)."
            ),
            follow_up=(
                "Does session-by-session rehab learning differ between groups?"
            ),
        )


# ============================================================================
# Recipe 4: RehabLearningCurve
# ============================================================================

class RehabLearningCurve(FigureRecipe):
    """Session-by-session rehab learning curves for recovered vs
    non-recovered animals.
    """

    name = "rehab_learning_curve"
    title = "Rehabilitation Session-by-Session Performance"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 9)

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "tray_filter": "pillar only for classification, all trays for rehab sessions",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        animal_df = all_data["animal_df"]
        rehab_sessions = all_data["rehab_sessions"]
        paired = _get_paired(animal_df)

        rec_animals = set(paired[paired["recovered_eaten"]]["animal"])
        norec_animals = set(paired[~paired["recovered_eaten"]]["animal"])

        return {
            "paired": paired,
            "rehab_sessions": rehab_sessions,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "n_rec": len(rec_animals),
            "n_norec": len(norec_animals),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        rehab = data["rehab_sessions"]
        if rehab.empty:
            return {"slope_test": None}

        rec_animals = data["rec_animals"]
        norec_animals = data["norec_animals"]

        rehab = rehab.copy()
        rehab["recovered"] = rehab["subject_id"].isin(rec_animals)

        # Compare slopes: linear regression per animal, then compare slopes
        slopes_rec = []
        slopes_norec = []
        for animal in rehab["subject_id"].unique():
            adf = rehab[rehab["subject_id"] == animal].sort_values("rehab_day")
            if len(adf) < 3:
                continue
            try:
                slope, _, _, _, _ = stats.linregress(
                    adf["rehab_day"], adf["eaten_pct"]
                )
                if animal in rec_animals:
                    slopes_rec.append(slope)
                elif animal in norec_animals:
                    slopes_norec.append(slope)
            except Exception:
                pass

        slope_test = None
        if len(slopes_rec) >= 2 and len(slopes_norec) >= 2:
            u, p = stats.mannwhitneyu(
                slopes_rec, slopes_norec, alternative="two-sided"
            )
            d = cohens_d(slopes_rec, slopes_norec)
            slope_test = {
                "u": u, "p": p, "d": d,
                "rec_mean": np.mean(slopes_rec),
                "norec_mean": np.mean(slopes_norec),
                "detail": format_stat_result(
                    "Mann-Whitney U", u, p, d=d,
                    n=len(slopes_rec) + len(slopes_norec),
                    alternative="two-sided",
                ),
            }
            print(f"    Rehab slope comparison: {slope_test['detail']}",
                  flush=True)

        return {"slope_test": slope_test}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(1, 2, wspace=0.3)
        return {
            "eaten": fig.add_subplot(inner[0]),
            "contacted": fig.add_subplot(inner[1]),
        }

    def plot(self, data, results, fig, ax, theme):
        rehab = data["rehab_sessions"]
        if rehab.empty:
            ax["eaten"].text(0.5, 0.5, "No rehab session data",
                             transform=ax["eaten"].transAxes, ha="center")
            return

        rec_animals = data["rec_animals"]
        rehab = rehab.copy()
        rehab["recovered"] = rehab["subject_id"].isin(rec_animals)

        subject_colors = get_persistent_subject_colors(
            sorted(rehab["subject_id"].unique())
        )

        for col_key, (metric, ylabel) in [
            ("eaten", ("eaten_pct", "% Eaten")),
            ("contacted", ("contacted_pct", "% Contacted")),
        ]:
            a = ax[col_key]
            for is_rec, group_color, label_prefix in [
                (True, RECOVERED_COLOR, "Recovered"),
                (False, NOT_RECOVERED_COLOR, "Not Recovered"),
            ]:
                group = rehab[rehab["recovered"] == is_rec]
                if group.empty:
                    continue

                day_means = (
                    group.groupby("rehab_day")[metric]
                    .agg(["mean", "sem", "count"])
                    .reset_index()
                )
                day_means = day_means[day_means["count"] >= 2]
                if day_means.empty:
                    continue

                a.plot(
                    day_means["rehab_day"], day_means["mean"], "o-",
                    color=group_color, linewidth=2, markersize=5,
                    label=f"{label_prefix}", zorder=5,
                )
                a.fill_between(
                    day_means["rehab_day"],
                    day_means["mean"] - day_means["sem"],
                    day_means["mean"] + day_means["sem"],
                    color=group_color, alpha=0.2,
                )

                # Individual traces with per-subject colors
                for animal in group["subject_id"].unique():
                    adf = group[group["subject_id"] == animal].sort_values(
                        "rehab_day"
                    )
                    s_color = subject_colors.get(animal, group_color)
                    a.plot(
                        adf["rehab_day"], adf[metric], "-",
                        color=s_color, alpha=0.2, linewidth=0.5,
                    )

            a.set_xlabel("Rehab Session Number", fontsize=11)
            a.set_ylabel(ylabel, fontsize=12)
            a.set_title(
                f"{ylabel}: Session-by-Session During Rehabilitation",
                fontsize=11, fontweight="bold",
            )
            a.legend(fontsize=9)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        slope = results.get("slope_test")
        slope_text = slope["detail"] if slope else "(not computed)"
        return (
            f"EXPERIMENT  Rehabilitation phase, all tray types shown\n"
            f"SUBJECTS    Recovered N={data['n_rec']}, "
            f"Not Recovered N={data['n_norec']}\n"
            f"NOTE        Rehab sessions include training (easy/flat/pillar) "
            f"-- not post-rehab test (Rule 41). Interpret as therapy progress, "
            f"not final assessment.\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"STATISTICS  Per-animal linear slope compared via Mann-Whitney U\n"
            f"  Slope test: {slope_text}\n"
            f"PLOT        Thick lines = group mean +/- SEM. "
            f"Thin lines = individual subjects."
        )

    def figure_legend(self, data, results):
        slope = results.get("slope_test")
        if slope:
            es_text = (
                f"Rehab learning slope: d={slope['d']:.2f} "
                f"({interpret_d(slope['d'])}), "
                f"rec mean={slope['rec_mean']:.2f}%/session, "
                f"norec mean={slope['norec_mean']:.2f}%/session"
            )
        else:
            es_text = "Not computed (insufficient data)"

        return FigureLegend(
            question=(
                "Do recovered animals show steeper learning curves "
                "during rehabilitation sessions?"
            ),
            method=(
                f"Recovered N={data['n_rec']} vs Not Recovered "
                f"N={data['n_norec']}. All rehab session types included. "
                f"Linear slope fitted per animal across rehab days."
            ),
            finding=(
                "Session-by-session eaten and contacted rates during rehab, "
                "split by eventual recovery outcome."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Per-animal linear regression slopes compared between groups."
            ),
            effect_sizes=es_text,
            confounds=(
                "Rehab sessions mix tray types (easy/flat/pillar) which "
                "have different baseline difficulty. Session count varies "
                "across animals. Weekend gaps may cause engagement dips (Rule 45)."
            ),
            follow_up=(
                "Are kinematic features during rehab sessions also predictive?"
            ),
        )


# ============================================================================
# Recipe 5: KinematicPredictors
# ============================================================================

class KinematicPredictors(FigureRecipe):
    """Pre-injury kinematics as predictors of recovery.

    Top row: scatter of each kinematic feature vs recovery ratio.
    Bottom row: box plot recovered vs not for each feature.
    """

    name = "kinematic_predictors"
    title = "Pre-Injury Kinematics as Predictors of Recovery"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
        DataSource("csv", "database_dump/reach_data.csv",
                   "Pre-injury reaches only"),
    ]
    figsize = (20, 12)

    KIN_FEATURES = [
        ("max_extent_mm", "Max Extent (mm)"),
        ("peak_velocity_px_per_frame", "Peak Velocity"),
        ("trajectory_straightness", "Straightness"),
        ("duration_frames", "Duration (frames)"),
        ("trajectory_smoothness", "Smoothness"),
    ]

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "kinematic_features": [f for f, _ in self.KIN_FEATURES],
            "tray_filter": "pillar only",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        paired = _get_paired(all_data["animal_df"])
        reach = all_data["reach"]
        surgeries = all_data["surgeries"]

        # Get pre-injury reaches
        reach_pre = pd.DataFrame()
        for cohort in self.cohorts:
            sd = _get_surgery_date(surgeries, cohort)
            if sd is None:
                continue
            mask = (reach["cohort"] == cohort) & (reach["session_date"] < sd)
            reach_pre = pd.concat([reach_pre, reach[mask]])

        available_feats = [
            (f, l) for f, l in self.KIN_FEATURES
            if f in reach_pre.columns and reach_pre[f].notna().sum() > 0
        ] if not reach_pre.empty else []

        merged = pd.DataFrame()
        if available_feats and not reach_pre.empty:
            feat_cols = [f for f, _ in available_feats]
            kin_means = (
                reach_pre.groupby("subject_id")[feat_cols].mean().reset_index()
            )
            kin_means = kin_means.rename(columns={"subject_id": "animal"})
            merged = paired.merge(
                kin_means, on="animal", how="inner", suffixes=("", "_kin")
            )

        rec = merged[merged["recovered_eaten"]] if len(merged) > 0 else pd.DataFrame()
        norec = merged[~merged["recovered_eaten"]] if len(merged) > 0 else pd.DataFrame()

        return {
            "merged": merged,
            "rec": rec,
            "norec": norec,
            "available_feats": available_feats,
            "n_merged": len(merged),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        merged = data["merged"]
        rec = data["rec"]
        norec = data["norec"]
        available_feats = data["available_feats"]

        correlations = []
        group_tests = []
        effect_sizes = {}

        for feat, label in available_feats:
            # Correlation with recovery ratio
            valid = merged.dropna(subset=[feat, "recovery_ratio_eaten"])
            if len(valid) >= 5:
                r, p = stats.spearmanr(valid[feat], valid["recovery_ratio_eaten"])
                correlations.append({
                    "feature": feat, "label": label,
                    "r": r, "p": p, "n": len(valid),
                })
                if p < 0.05:
                    effect_sizes[f"{label} (corr)"] = f"r={r:.2f}"

            # Group comparison
            rv = rec[feat].dropna() if len(rec) > 0 else pd.Series(dtype=float)
            nv = norec[feat].dropna() if len(norec) > 0 else pd.Series(dtype=float)
            if len(rv) >= 2 and len(nv) >= 2:
                try:
                    u, p_mw = stats.mannwhitneyu(rv, nv, alternative="two-sided")
                    d = cohens_d(rv.values, nv.values)
                    group_tests.append({
                        "feature": feat, "label": label,
                        "u": u, "p": p_mw, "d": d,
                        "detail": format_stat_result(
                            "Mann-Whitney U", u, p_mw, d=d,
                            n=len(rv) + len(nv),
                        ),
                    })
                    if p_mw < 0.05:
                        effect_sizes[f"{label} (group)"] = (
                            f"d={d:.2f} ({interpret_d(d)})"
                        )
                except Exception:
                    pass

        return {
            "correlations": correlations,
            "group_tests": group_tests,
            "effect_sizes": effect_sizes,
        }

    def create_axes(self, fig, plot_gs):
        n_feats = 5  # max features
        inner = plot_gs.subgridspec(2, n_feats, hspace=0.4, wspace=0.35)
        return np.array([
            [fig.add_subplot(inner[r, c]) for c in range(n_feats)]
            for r in range(2)
        ])

    def plot(self, data, results, fig, ax, theme):
        merged = data["merged"]
        rec = data["rec"]
        norec = data["norec"]
        available_feats = data["available_feats"]

        if len(merged) < 5 or not available_feats:
            ax[0, 0].text(
                0.5, 0.5, "Insufficient kinematic data",
                transform=ax[0, 0].transAxes, ha="center",
            )
            return

        corr_map = {c["feature"]: c for c in results.get("correlations", [])}
        test_map = {t["feature"]: t for t in results.get("group_tests", [])}

        for i, (feat, label) in enumerate(available_feats):
            if i >= ax.shape[1]:
                break

            # TOP: scatter vs recovery ratio
            a = ax[0, i]
            valid = merged.dropna(subset=[feat, "recovery_ratio_eaten"])
            colors = [COHORT_COLORS.get(c, "#888") for c in valid["cohort"]]
            a.scatter(
                valid[feat], valid["recovery_ratio_eaten"] * 100,
                c=colors, s=50, alpha=0.7, edgecolor="black", linewidth=0.5,
            )
            a.axhline(
                y=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
                linestyle="--", linewidth=1, alpha=0.6,
            )
            a.set_xlabel(label, fontsize=9)
            a.set_ylabel("Recovery Ratio (%)", fontsize=9)
            a.set_title(f"{label}\nvs Recovery", fontsize=9, fontweight="bold")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            c = corr_map.get(feat)
            if c:
                a.text(
                    0.02, 0.98, f"r={c['r']:.3f}\np={c['p']:.4f}",
                    transform=a.transAxes, ha="left", va="top", fontsize=7,
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.7),
                    family="monospace",
                )

            # BOTTOM: box recovered vs not
            a = ax[1, i]
            rec_vals = rec[feat].dropna() if len(rec) > 0 else pd.Series(dtype=float)
            norec_vals = norec[feat].dropna() if len(norec) > 0 else pd.Series(dtype=float)

            if len(rec_vals) > 0 and len(norec_vals) > 0:
                bp = a.boxplot(
                    [rec_vals, norec_vals], patch_artist=True, widths=0.6,
                    medianprops=dict(color="black", linewidth=2),
                )
                bp["boxes"][0].set_facecolor(RECOVERED_COLOR)
                bp["boxes"][0].set_alpha(0.6)
                bp["boxes"][1].set_facecolor(NOT_RECOVERED_COLOR)
                bp["boxes"][1].set_alpha(0.6)

                rng = np.random.RandomState(42)
                for j, (vals, color) in enumerate([
                    (rec_vals, RECOVERED_COLOR),
                    (norec_vals, NOT_RECOVERED_COLOR),
                ]):
                    jitter = rng.uniform(-0.12, 0.12, len(vals))
                    a.scatter(
                        np.full(len(vals), j + 1) + jitter, vals,
                        color=color, s=25, alpha=0.6, edgecolor="white",
                        linewidth=0.3, zorder=5,
                    )
                a.set_xticklabels(["Rec", "Not\nRec"], fontsize=8)

                t = test_map.get(feat)
                if t:
                    a.text(
                        0.5, 0.98,
                        f"p={t['p']:.4f} {_stars(t['p'])}\nd={t['d']:.2f}",
                        transform=a.transAxes, ha="center", va="top",
                        fontsize=7,
                        bbox=dict(boxstyle="round", facecolor="wheat",
                                  alpha=0.7),
                    )

            a.set_title(label, fontsize=9, fontweight="bold")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Hide unused axes
        for i in range(len(available_feats), ax.shape[1]):
            ax[0, i].axis("off")
            ax[1, i].axis("off")

    def methodology_text(self, data, results):
        corr_lines = []
        for c in results.get("correlations", []):
            corr_lines.append(
                f"  {c['label']}: r={c['r']:.3f}, p={c['p']:.4f}"
            )
        corr_text = "\n".join(corr_lines) or "  (none)"
        return (
            f"EXPERIMENT  Pre-injury kinematic features vs recovery outcome\n"
            f"SUBJECTS    N={data['n_merged']} with kinematic + recovery data\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"KINEMATICS  Pre-injury reaches only (before contusion surgery)\n"
            f"TRAY FILTER Pillar tray for recovery classification\n"
            f"STATISTICS  Top: Spearman correlations. Bottom: Mann-Whitney U.\n"
            f"{corr_text}\n"
            f"PLOT        Scatters (top) = feature vs recovery ratio. "
            f"Boxes (bottom) = recovered vs not."
        )

    def figure_legend(self, data, results):
        es = results.get("effect_sizes", {})
        es_text = "; ".join(f"{k}: {v}" for k, v in es.items()) or "None significant"
        return FigureLegend(
            question=(
                "Do pre-injury kinematic profiles predict which animals "
                "will recover after rehabilitation?"
            ),
            method=(
                f"N={data['n_merged']} animals with both kinematic and "
                f"recovery data. Pre-injury reaches only. Pillar tray for "
                f"recovery classification."
            ),
            finding=(
                "Scatter plots show correlation between each kinematic "
                "feature and recovery ratio. Box plots compare recovered "
                "and non-recovered groups."
            ),
            analysis=(
                f"Spearman rank for correlations (non-parametric, no normality "
                f"assumption). {stat_justification('mann-whitney')}"
            ),
            effect_sizes=es_text,
            confounds=(
                "Pre-injury kinematics may correlate with overall motor "
                "ability, which independently predicts recovery. Kinematic "
                "features are computed across all reach outcomes (not filtered "
                "to successful retrievals)."
            ),
            follow_up=(
                "Does a multivariate model using all predictors outperform "
                "any single kinematic feature?"
            ),
        )


# ============================================================================
# Recipe 6: PredictorSummary
# ============================================================================

class PredictorSummary(FigureRecipe):
    """Multivariate predictor summary: correlation bar chart and
    logistic regression coefficients.
    """

    name = "predictor_summary"
    title = "Multivariate Predictor Summary"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
        DataSource("csv", "database_dump/reach_data.csv",
                   "Pre-injury reaches for kinematic predictors"),
    ]
    figsize = (18, 10)

    BEHAVIORAL_PREDICTORS = [
        ("pre_injury_eaten", "Pre-Injury Eaten %"),
        ("pre_injury_contacted", "Pre-Injury Contacted %"),
        ("acute_deficit_eaten", "Acute Deficit"),
        ("chronic_slope_eaten", "Chronic Slope"),
        ("post_1_eaten", "Post 1wk Eaten %"),
        ("nadir_eaten", "Nadir Eaten %"),
    ]

    KIN_CANDIDATES = [
        "max_extent_mm", "peak_velocity_px_per_frame",
        "trajectory_straightness",
    ]
    KIN_LABELS = {
        "max_extent_mm": "Max Extent (mm)",
        "peak_velocity_px_per_frame": "Peak Velocity",
        "trajectory_straightness": "Straightness",
    }

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "tray_filter": "pillar only",
            "has_sklearn": HAS_SKLEARN,
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        paired = _get_paired(all_data["animal_df"])
        reach = all_data["reach"]
        surgeries = all_data["surgeries"]

        # Pre-injury kinematic means
        reach_pre = pd.DataFrame()
        for cohort in self.cohorts:
            sd = _get_surgery_date(surgeries, cohort)
            if sd is None:
                continue
            mask = (reach["cohort"] == cohort) & (reach["session_date"] < sd)
            reach_pre = pd.concat([reach_pre, reach[mask]])

        kin_feats_added = []
        if not reach_pre.empty:
            available = [
                f for f in self.KIN_CANDIDATES
                if f in reach_pre.columns and reach_pre[f].notna().sum() > 10
            ]
            if available:
                kin_means = (
                    reach_pre.groupby("subject_id")[available].mean()
                    .reset_index()
                    .rename(columns={"subject_id": "animal"})
                )
                paired = paired.merge(
                    kin_means, on="animal", how="left", suffixes=("", "_kin")
                )
                for f in available:
                    if f in paired.columns and paired[f].notna().sum() >= 5:
                        kin_feats_added.append(
                            (f, self.KIN_LABELS.get(f, f))
                        )

        all_predictors = list(self.BEHAVIORAL_PREDICTORS) + kin_feats_added

        return {
            "paired": paired,
            "all_predictors": all_predictors,
            "n_paired": len(paired),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        paired = data["paired"]
        all_predictors = data["all_predictors"]

        # Spearman correlations
        correlations = []
        for feat, label in all_predictors:
            valid = paired.dropna(subset=[feat, "recovery_ratio_eaten"])
            if len(valid) >= 5:
                r, p = stats.spearmanr(valid[feat], valid["recovery_ratio_eaten"])
                correlations.append({
                    "feature": feat, "label": label,
                    "r": r, "p": p, "n": len(valid),
                })

        # Logistic regression
        lr_result = None
        feat_cols = [f for f, _ in all_predictors]
        valid_lr = paired.dropna(
            subset=feat_cols + ["recovered_eaten"]
        ).copy()

        if HAS_SKLEARN and len(valid_lr) >= 10:
            X = valid_lr[feat_cols].values
            y = valid_lr["recovered_eaten"].astype(int).values

            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            model = LogisticRegression(
                penalty="l2", C=1.0, max_iter=1000, random_state=42,
            )
            model.fit(X_scaled, y)
            acc = model.score(X_scaled, y)

            lr_result = {
                "coefs": model.coef_[0].tolist(),
                "labels": [l for _, l in all_predictors],
                "accuracy": acc,
                "n": len(valid_lr),
            }
            print(
                f"    Logistic regression: accuracy={acc:.1%}, N={len(valid_lr)}",
                flush=True,
            )

        return {"correlations": correlations, "lr_result": lr_result}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(1, 2, wspace=0.4)
        return {
            "correlations": fig.add_subplot(inner[0]),
            "logistic": fig.add_subplot(inner[1]),
        }

    def plot(self, data, results, fig, ax, theme):
        # LEFT: Correlation bar chart
        a = ax["correlations"]
        corrs = results.get("correlations", [])
        if corrs:
            corr_df = pd.DataFrame(corrs).sort_values("r", ascending=True)
            colors = [
                RECOVERED_COLOR if r > 0 else NOT_RECOVERED_COLOR
                for r in corr_df["r"]
            ]
            bar_alphas = [0.9 if p < 0.05 else 0.4 for p in corr_df["p"]]
            edge_colors = [
                "black" if p < 0.05 else "gray" for p in corr_df["p"]
            ]

            y_pos = np.arange(len(corr_df))
            bars = a.barh(
                y_pos, corr_df["r"], color=colors, edgecolor=edge_colors,
                linewidth=1.5,
            )
            for bar, alpha in zip(bars, bar_alphas):
                bar.set_alpha(alpha)

            a.set_yticks(y_pos)
            a.set_yticklabels(corr_df["label"], fontsize=9)
            a.set_xlabel("Spearman r with Recovery Ratio", fontsize=10)
            a.set_title("Correlation with Recovery", fontsize=11,
                        fontweight="bold")
            a.axvline(x=0, color="black", linewidth=1)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            for i, (_, row) in enumerate(corr_df.iterrows()):
                stars = _stars(row["p"])
                x_pos = row["r"] + 0.02 if row["r"] >= 0 else row["r"] - 0.02
                ha = "left" if row["r"] >= 0 else "right"
                a.text(
                    x_pos, i,
                    f"{stars} (r={row['r']:.2f})",
                    va="center", ha=ha, fontsize=7,
                )

        # RIGHT: Logistic regression
        a = ax["logistic"]
        lr = results.get("lr_result")
        if lr:
            coefs = np.array(lr["coefs"])
            labels_lr = lr["labels"]
            sorted_idx = np.argsort(coefs)
            coefs_sorted = coefs[sorted_idx]
            labels_sorted = [labels_lr[i] for i in sorted_idx]

            colors_lr = [
                RECOVERED_COLOR if c > 0 else NOT_RECOVERED_COLOR
                for c in coefs_sorted
            ]
            y_pos = np.arange(len(coefs_sorted))
            a.barh(
                y_pos, coefs_sorted, color=colors_lr, alpha=0.8,
                edgecolor="black", linewidth=0.5,
            )
            a.set_yticks(y_pos)
            a.set_yticklabels(labels_sorted, fontsize=9)
            a.set_xlabel(
                "Logistic Regression Coefficient (standardized)", fontsize=10,
            )
            a.axvline(x=0, color="black", linewidth=1)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            a.set_title(
                f"Logistic Regression Coefficients\n"
                f"(accuracy={lr['accuracy']:.1%}, N={lr['n']})",
                fontsize=11, fontweight="bold",
            )

            for i, c in enumerate(coefs_sorted):
                x_text = c + 0.02 if c >= 0 else c - 0.02
                ha = "left" if c >= 0 else "right"
                a.text(x_text, i, f"{c:.2f}", va="center", ha=ha, fontsize=7)
        else:
            a.text(
                0.5, 0.5,
                "Logistic regression requires\nsklearn and N>=10",
                transform=a.transAxes, ha="center", va="center",
                fontsize=12, color="gray",
            )
            a.set_title("Logistic Regression (unavailable)", fontsize=11)

    def methodology_text(self, data, results):
        corr_lines = []
        for c in results.get("correlations", []):
            corr_lines.append(
                f"  {c['label']}: r={c['r']:.3f}, p={c['p']:.4f}"
            )
        corr_text = "\n".join(corr_lines) or "  (none)"

        lr = results.get("lr_result")
        lr_text = (
            f"L2-regularized logistic regression (C=1.0), "
            f"accuracy={lr['accuracy']:.1%}, N={lr['n']}"
            if lr else "sklearn not available or N < 10"
        )

        return (
            f"EXPERIMENT  Multivariate prediction of recovery\n"
            f"SUBJECTS    N={data['n_paired']} learners\n"
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}\n"
            f"PREDICTORS  Behavioral + kinematic (pre-injury averages)\n"
            f"STATISTICS\n"
            f"  Correlations (Spearman rank):\n{corr_text}\n"
            f"  Logistic regression: {lr_text}\n"
            f"PLOT        Left = Spearman r bars. Right = LR coefficients."
        )

    def figure_legend(self, data, results):
        sig_corrs = [
            c for c in results.get("correlations", []) if c["p"] < 0.05
        ]
        es_parts = [f"{c['label']}: r={c['r']:.2f}" for c in sig_corrs]
        lr = results.get("lr_result")
        if lr:
            es_parts.append(f"LR accuracy={lr['accuracy']:.1%}")
        es_text = "; ".join(es_parts) if es_parts else "No significant predictors"

        return FigureLegend(
            question=(
                "Which combination of behavioral and kinematic predictors "
                "best discriminates recovered from non-recovered animals?"
            ),
            method=(
                f"N={data['n_paired']} learners. "
                f"Spearman correlations + L2 logistic regression. "
                f"Features standardized (z-scored). Pillar tray for "
                f"recovery classification."
            ),
            finding=(
                "Correlation bar chart and logistic regression coefficients "
                "identify the strongest predictors of recovery."
            ),
            analysis=(
                f"Spearman rank (non-parametric correlations). "
                f"L2-regularized logistic regression for multivariate prediction. "
                f"Training accuracy reported (no cross-validation -- N is small)."
            ),
            effect_sizes=es_text,
            confounds=(
                "No cross-validation due to small N -- training accuracy is "
                "optimistic. Multicollinearity between predictors not addressed. "
                "L2 regularization mitigates overfitting but does not eliminate it."
            ),
            follow_up=(
                "Cross-validate with leave-one-out or k-fold when N increases. "
                "Consider LASSO (L1) for feature selection."
            ),
        )


# ============================================================================
# Recipe 7: ContactedVsEatenRecovery
# ============================================================================

class ContactedVsEatenRecovery(FigureRecipe):
    """Does contacted recovery predict eaten recovery? Three panels:
    post-1wk contact vs eaten recovery, contacted vs eaten recovery
    ratios, and confusion matrix.
    """

    name = "contacted_vs_eaten_recovery"
    title = "Contacted as Early Predictor of Eaten Recovery"
    category = "recovery"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv",
                   "Pillar tray only, CNT_01-04"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (18, 8)

    def __init__(self, cohorts=None, learner_threshold=LEARNER_EATEN_THRESHOLD):
        self.cohorts = cohorts or COHORTS
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "tray_filter": "pillar only",
        }

    def load_data(self) -> Dict[str, Any]:
        all_data = _prepare_all_data(self.cohorts, self.learner_threshold)
        paired = _get_paired(all_data["animal_df"])
        return {
            "paired": paired,
            "n_paired": len(paired),
            "cohorts": sorted(paired["cohort"].unique()),
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        paired = data["paired"]
        results_out = {"corr_post1": None, "corr_ratios": None, "confusion": None}

        # Post-1wk contacted vs eaten recovery
        valid = paired.dropna(subset=["post_1_contacted", "recovery_ratio_eaten"])
        if len(valid) >= 5:
            r, p = stats.spearmanr(
                valid["post_1_contacted"], valid["recovery_ratio_eaten"]
            )
            results_out["corr_post1"] = {"r": r, "p": p, "n": len(valid)}

        # Contacted recovery ratio vs eaten recovery ratio
        valid = paired.dropna(
            subset=["recovery_ratio_contacted", "recovery_ratio_eaten"]
        )
        if len(valid) >= 5:
            r, p = stats.spearmanr(
                valid["recovery_ratio_contacted"],
                valid["recovery_ratio_eaten"],
            )
            results_out["corr_ratios"] = {"r": r, "p": p, "n": len(valid)}

        # Confusion matrix
        valid = paired.dropna(
            subset=["recovered_contacted", "recovered_eaten"]
        )
        if len(valid) >= 5:
            tp = int(
                (valid["recovered_contacted"] & valid["recovered_eaten"]).sum()
            )
            fp = int(
                (valid["recovered_contacted"] & ~valid["recovered_eaten"]).sum()
            )
            fn = int(
                (~valid["recovered_contacted"] & valid["recovered_eaten"]).sum()
            )
            tn = int(
                (~valid["recovered_contacted"] & ~valid["recovered_eaten"]).sum()
            )
            sens = tp / (tp + fn) if (tp + fn) > 0 else 0
            spec = tn / (tn + fp) if (tn + fp) > 0 else 0
            ppv = tp / (tp + fp) if (tp + fp) > 0 else 0
            acc = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) > 0 else 0

            results_out["confusion"] = {
                "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "sensitivity": sens, "specificity": spec,
                "ppv": ppv, "accuracy": acc, "n": len(valid),
            }

        return results_out

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(1, 3, wspace=0.35)
        return {
            "post1": fig.add_subplot(inner[0]),
            "ratios": fig.add_subplot(inner[1]),
            "confusion": fig.add_subplot(inner[2]),
        }

    def plot(self, data, results, fig, ax, theme):
        paired = data["paired"]

        # 7A: Post-1wk contacted vs eaten recovery
        a = ax["post1"]
        valid = paired.dropna(subset=["post_1_contacted", "recovery_ratio_eaten"])
        colors = [COHORT_COLORS.get(c, "#888") for c in valid["cohort"]]
        a.scatter(
            valid["post_1_contacted"], valid["recovery_ratio_eaten"] * 100,
            c=colors, s=50, alpha=0.7, edgecolor="black", linewidth=0.5,
        )
        a.axhline(
            y=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
            linestyle="--", alpha=0.6,
        )
        a.set_xlabel("Post 1wk Contacted %")
        a.set_ylabel("Eaten Recovery Ratio (%)")
        a.set_title("Post-1wk Contact Rate\nvs Eaten Recovery",
                     fontweight="bold", fontsize=11)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        c1 = results.get("corr_post1")
        if c1:
            a.text(
                0.02, 0.98, f"r={c1['r']:.3f}, p={c1['p']:.4f}",
                transform=a.transAxes, ha="left", va="top", fontsize=8,
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
                family="monospace",
            )

        # 7B: Contacted vs eaten recovery ratios
        a = ax["ratios"]
        valid = paired.dropna(
            subset=["recovery_ratio_contacted", "recovery_ratio_eaten"]
        )
        colors = [COHORT_COLORS.get(c, "#888") for c in valid["cohort"]]
        a.scatter(
            valid["recovery_ratio_contacted"] * 100,
            valid["recovery_ratio_eaten"] * 100,
            c=colors, s=50, alpha=0.7, edgecolor="black", linewidth=0.5,
        )
        a.axhline(
            y=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
            linestyle="--", alpha=0.6,
        )
        a.axvline(
            x=RECOVERY_THRESHOLD * 100, color=RECOVERED_COLOR,
            linestyle="--", alpha=0.6,
        )
        a.plot([0, 200], [0, 200], "k:", alpha=0.3)
        a.set_xlabel("Contacted Recovery Ratio (%)")
        a.set_ylabel("Eaten Recovery Ratio (%)")
        a.set_title("Contacted vs Eaten\nRecovery Ratios",
                     fontweight="bold", fontsize=11)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        c2 = results.get("corr_ratios")
        if c2:
            a.text(
                0.02, 0.98, f"r={c2['r']:.3f}, p={c2['p']:.4f}",
                transform=a.transAxes, ha="left", va="top", fontsize=8,
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
                family="monospace",
            )

        # 7C: Confusion matrix
        a = ax["confusion"]
        cm = results.get("confusion")
        if cm:
            mat = np.array([[cm["tp"], cm["fp"]], [cm["fn"], cm["tn"]]])
            im = a.imshow(mat, cmap="Blues", aspect="auto")

            for (j, i), val in np.ndenumerate(mat):
                a.text(
                    i, j, f"{val}", ha="center", va="center",
                    fontsize=18, fontweight="bold",
                    color="white" if val > mat.max() * 0.5 else "black",
                )

            a.set_xticks([0, 1])
            a.set_yticks([0, 1])
            a.set_xticklabels(["Eaten\nRecovered", "Eaten\nNot Recovered"],
                              fontsize=9)
            a.set_yticklabels(["Contacted\nRecovered",
                               "Contacted\nNot Recovered"], fontsize=9)
            a.set_title("Contacted Recovery Predicts\nEaten Recovery",
                        fontweight="bold", fontsize=11)
            a.text(
                0.5, -0.15,
                f"Sens={cm['sensitivity']:.0%}  Spec={cm['specificity']:.0%}  "
                f"PPV={cm['ppv']:.0%}  Acc={cm['accuracy']:.0%}",
                transform=a.transAxes, ha="center", fontsize=9,
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.9),
            )
        else:
            a.text(0.5, 0.5, "Insufficient data",
                   transform=a.transAxes, ha="center")

    def methodology_text(self, data, results):
        c1 = results.get("corr_post1")
        c2 = results.get("corr_ratios")
        cm = results.get("confusion")

        lines = [
            f"EXPERIMENT  Can contacted% predict eaten% recovery?",
            f"SUBJECTS    N={data['n_paired']} learners",
            f"RECOVERY    {RECOVERY_THRESHOLD_LABEL}",
            f"TRAY FILTER Pillar tray sessions only",
            f"STATISTICS",
        ]
        if c1:
            lines.append(
                f"  Post-1wk contacted vs eaten recovery: "
                f"r={c1['r']:.3f}, p={c1['p']:.4f}"
            )
        if c2:
            lines.append(
                f"  Contacted vs eaten recovery ratio: "
                f"r={c2['r']:.3f}, p={c2['p']:.4f}"
            )
        if cm:
            lines.append(
                f"  Confusion matrix: sensitivity={cm['sensitivity']:.0%}, "
                f"specificity={cm['specificity']:.0%}, "
                f"PPV={cm['ppv']:.0%}, accuracy={cm['accuracy']:.0%}"
            )
        lines.append(
            f"PLOT        Scatters = correlations. Matrix = classification agreement."
        )
        return "\n".join(lines)

    def figure_legend(self, data, results):
        es_parts = []
        c1 = results.get("corr_post1")
        if c1 and c1["p"] < 0.05:
            es_parts.append(f"Post-1wk contacted vs recovery: r={c1['r']:.2f}")
        c2 = results.get("corr_ratios")
        if c2 and c2["p"] < 0.05:
            es_parts.append(
                f"Contacted vs eaten recovery ratio: r={c2['r']:.2f}"
            )
        cm = results.get("confusion")
        if cm:
            es_parts.append(f"Classification accuracy={cm['accuracy']:.0%}")
        es_text = "; ".join(es_parts) if es_parts else "No significant associations"

        return FigureLegend(
            question=(
                "Does recovery of pellet contact (motor planning preserved) "
                "predict recovery of pellet retrieval (full motor function)?"
            ),
            method=(
                f"N={data['n_paired']} learners. Pillar tray only. "
                f"Recovery ratio = (rehab - nadir) / (pre - nadir). "
                f"Threshold >= {RECOVERY_THRESHOLD * 100:.0f}%."
            ),
            finding=(
                "Scatter plots show relationship between contacted and eaten "
                "recovery metrics. Confusion matrix shows classification "
                "agreement."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Spearman rank for correlations. 2x2 confusion matrix for "
                f"classification agreement (sensitivity, specificity, PPV)."
            ),
            effect_sizes=es_text,
            confounds=(
                "Contacted and eaten are not independent -- every eaten pellet "
                "is also contacted. Contacted recovery may simply reflect "
                "higher engagement rather than preserved motor planning."
            ),
            follow_up=(
                "Does contacted recovery at post-1wk (earliest timepoint) "
                "provide clinically useful early prediction of eventual eaten "
                "recovery? What is the optimal contacted threshold?"
            ),
        )
