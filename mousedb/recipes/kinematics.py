"""
Kinematic figure recipes.

Migrated from Connectome_Grant/kinematic_figures.py

KinematicRecoveryIndex: Bar chart of recovery index per kinematic feature,
    showing how much each feature recovers during rehab relative to pre-injury.

KinematicPhaseComparison: 2x2 grid of kinematic features across 4 experimental
    phases, with individual subject lines and group means.
"""

from typing import Any, Dict, List, Optional

import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scipy import stats as sp_stats

from mousedb import MOUSEDB_ROOT
from mousedb.figures.legends import FigureLegend
from mousedb.figures.palettes import (
    COHORT_COLORS,
    KINEMATIC_CATEGORY_COLORS,
    KINEMATIC_PHASE_COLORS,
    KINEMATIC_PHASE_LABELS,
)
from mousedb.figures.stats import cohens_d_paired, format_stat_result, stat_justification
from mousedb.recipes.base import DataSource, FigureRecipe


# ============================================================================
# Constants
# ============================================================================

# Top kinematic features for presentation (most interpretable)
TOP_FEATURES = [
    ("max_extent_mm", "Max Extent (mm)", "distance"),
    ("peak_velocity_px_per_frame", "Peak Velocity (px/fr)", "velocity"),
    ("duration_frames", "Duration (frames)", "timing"),
    ("trajectory_straightness", "Straightness", "quality"),
    ("trajectory_smoothness", "Smoothness", "quality"),
    ("hand_angle_at_apex_deg", "Hand Angle (deg)", "posture"),
]

# Aliases for brevity within this module (canonical source: palettes.py)
CATEGORY_COLORS = KINEMATIC_CATEGORY_COLORS
PHASE_LABELS = KINEMATIC_PHASE_LABELS

LEARNER_THRESHOLD = 5.0

# Default date-to-phase mapping for CNT_01 cohort
DEFAULT_PRE_INJURY_TEST_DATES = {"2025-06-27", "2025-06-30", "2025-07-01"}
DEFAULT_POST_INJURY_1_DATES = {"2025-07-11"}
DEFAULT_POST_INJURY_2_4_DATES = {"2025-07-18", "2025-07-25", "2025-08-01"}
DEFAULT_REHAB_PILLAR_CUTOFF = "2025-08-20"


# ============================================================================
# Shared helpers
# ============================================================================

def _load_kinematic_data(cohort_prefix="CNT_01_"):
    """Load reach data, pellet scores, and surgeries for a cohort."""
    dump_dir = MOUSEDB_ROOT / "database_dump"
    reach = pd.read_csv(dump_dir / "reach_data.csv", low_memory=False)
    ps = pd.read_csv(dump_dir / "pellet_scores.csv", low_memory=False)
    surg = pd.read_csv(dump_dir / "surgeries.csv", low_memory=False)

    reach = reach[reach["subject_id"].str.startswith(cohort_prefix)].copy()
    ps = ps[ps["subject_id"].str.startswith(cohort_prefix)].copy()
    surg = surg[surg["subject_id"].str.startswith(cohort_prefix)].copy()

    reach["session_date"] = pd.to_datetime(reach["session_date"])
    ps["session_date"] = pd.to_datetime(ps["session_date"])
    surg["surgery_date"] = pd.to_datetime(surg["surgery_date"])

    return reach, ps, surg


def _assign_phases(reach, surg, pre_dates, post1_dates, post24_dates,
                   rehab_cutoff):
    """Assign 4-timepoint phases based on explicit session dates."""
    contusion = surg[surg["surgery_type"] == "contusion"]
    surgery_date = contusion["surgery_date"].min()

    reach = reach.copy()
    date_str = reach["session_date"].dt.strftime("%Y-%m-%d")

    reach["phase"] = None
    reach.loc[date_str.isin(pre_dates), "phase"] = "Pre-Injury"
    reach.loc[date_str.isin(post1_dates), "phase"] = "Post-Injury_1"
    reach.loc[date_str.isin(post24_dates), "phase"] = "Post-Injury_2-4"
    rehab_mask = (date_str >= rehab_cutoff) & (reach["tray_type"] == "P")
    reach.loc[rehab_mask, "phase"] = "Rehab_Pillar"

    return reach, surgery_date


def _identify_learners(ps, pre_dates, threshold=LEARNER_THRESHOLD):
    """Identify learners with >= threshold eaten% on pre-injury test sessions."""
    ps = ps.copy()
    ps["eaten"] = (ps["score"] == 2).astype(int)
    date_str = ps["session_date"].dt.strftime("%Y-%m-%d")
    pre = ps[date_str.isin(pre_dates) & (ps["tray_type"] == "P")]
    if pre.empty:
        return []
    subj_pct = pre.groupby("subject_id")["eaten"].mean() * 100
    return subj_pct[subj_pct >= threshold].index.tolist()


# ============================================================================
# Recipe: KinematicRecoveryIndex
# ============================================================================

class KinematicRecoveryIndex(FigureRecipe):
    """Bar chart of recovery index per kinematic feature.

    Recovery index = (Rehab_mean - Post1_mean) / (Pre_mean - Post1_mean)
    per subject, then averaged. RI=1.0 means full return to pre-injury
    level; RI=0.0 means no change from acute post-injury deficit.

    Uses 2-panel layout (plot + methodology). Single panel.
    """

    name = "kinematic_recovery_index"
    title = "Kinematic Recovery Index: (Rehab - Post1) / (Pre - Post1)"
    category = "kinematics"
    data_sources = [
        DataSource("csv", "database_dump/reach_data.csv"),
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 11)

    def __init__(self, cohort_prefix="CNT_01_",
                 learner_threshold=LEARNER_THRESHOLD,
                 features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        """
        Parameters
        ----------
        cohort_prefix : str
            Subject ID prefix to filter (e.g., "CNT_01_").
        learner_threshold : float
            Minimum % pellets eaten pre-injury to qualify as a learner.
        features : list of tuple, optional
            List of (column_name, label, category) tuples. Defaults to TOP_FEATURES.
        pre_injury_dates : set of str, optional
            Session dates for pre-injury phase. Defaults to CNT_01 dates.
        post_injury_1_dates : set of str, optional
            Session dates for post-injury week 1.
        post_injury_2_4_dates : set of str, optional
            Session dates for post-injury weeks 2-4.
        rehab_pillar_cutoff : str, optional
            Date string (YYYY-MM-DD); sessions >= this on Pillar tray are Rehab.
        min_reaches_per_phase : int
            Minimum reaches per phase per subject to include.
        """
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.features = features or TOP_FEATURES
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        self.post_injury_2_4_dates = post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        self.rehab_pillar_cutoff = rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "features": [(f, l, c) for f, l, c in self.features],
            "min_reaches_per_phase": self.min_reaches,
            "pre_injury_dates": sorted(self.pre_injury_dates),
            "post_injury_1_dates": sorted(self.post_injury_1_dates),
            "post_injury_2_4_dates": sorted(self.post_injury_2_4_dates),
            "rehab_pillar_cutoff": self.rehab_pillar_cutoff,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)
        reach, surgery_date = _assign_phases(
            reach, surg,
            self.pre_injury_dates,
            self.post_injury_1_dates,
            self.post_injury_2_4_dates,
            self.rehab_pillar_cutoff,
        )
        learners = _identify_learners(ps, self.pre_injury_dates,
                                      self.learner_threshold)

        all_phases = list(KINEMATIC_PHASE_COLORS.keys())
        reach = reach[
            (reach["subject_id"].isin(learners))
            & (reach["phase"].isin(all_phases))
        ].copy()

        print(f"  Learners: {len(learners)}, reaches with phases: {len(reach)}",
              flush=True)
        print(f"  Phase counts: {reach['phase'].value_counts().to_dict()}",
              flush=True)

        return {
            "reach": reach,
            "learners": learners,
            "surgery_date": surgery_date,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        learners = data["learners"]
        min_n = self.min_reaches

        features_data = []
        for feat, label, cat in self.features:
            if feat not in reach.columns or reach[feat].isna().all():
                continue

            recovery_indices = []
            for subj in learners:
                subj_data = reach[reach["subject_id"] == subj]
                pre_vals = subj_data[subj_data["phase"] == "Pre-Injury"][feat].dropna()
                post1_vals = subj_data[subj_data["phase"] == "Post-Injury_1"][feat].dropna()
                rehab_vals = subj_data[subj_data["phase"] == "Rehab_Pillar"][feat].dropna()

                if (len(pre_vals) >= min_n and len(post1_vals) >= min_n
                        and len(rehab_vals) >= min_n):
                    pre_mean = pre_vals.mean()
                    post1_mean = post1_vals.mean()
                    rehab_mean = rehab_vals.mean()
                    denominator = pre_mean - post1_mean
                    if abs(denominator) > 1e-6:
                        ri = (rehab_mean - post1_mean) / denominator
                        recovery_indices.append(ri)

            if len(recovery_indices) >= 3:
                ri_arr = np.array(recovery_indices)
                t_stat, p_val = sp_stats.ttest_1samp(ri_arr, 0)
                d = np.mean(ri_arr) / np.std(ri_arr, ddof=1) if np.std(ri_arr, ddof=1) > 0 else 0.0
                features_data.append({
                    "feat": feat,
                    "label": label,
                    "cat": cat,
                    "mean_ri": np.mean(ri_arr),
                    "sem_ri": np.std(ri_arr) / np.sqrt(len(ri_arr)),
                    "n": len(ri_arr),
                    "values": ri_arr,
                    "t_stat": t_stat,
                    "p_val": p_val,
                    "d": d,
                })

        # Sort by mean recovery index descending
        features_data.sort(key=lambda x: x["mean_ri"], reverse=True)

        # Build formatted stat details
        stat_details = []
        for f in features_data:
            detail = format_stat_result(
                "One-sample t-test vs 0", f["t_stat"], f["p_val"],
                d=f["d"], n=f["n"],
            )
            stat_details.append(f"{f['label']}: {detail}")

        return {
            "features_data": features_data,
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        features_data = results["features_data"]

        if not features_data:
            ax.text(0.5, 0.5, "No features with sufficient data",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=14)
            return

        x = np.arange(len(features_data))
        means = [f["mean_ri"] for f in features_data]
        sems = [f["sem_ri"] for f in features_data]
        colors = [CATEGORY_COLORS[f["cat"]] for f in features_data]
        labels = [f["label"] for f in features_data]

        ax.bar(x, means, yerr=sems, capsize=5, color=colors,
               edgecolor="white", linewidth=0.5, width=0.6, zorder=3)

        # Individual subject dots (clipped to visible range)
        y_lo, y_hi = -3.0, 3.0
        rng = np.random.default_rng(42)
        for i, f in enumerate(features_data):
            clipped = np.clip(f["values"], y_lo, y_hi)
            jitter = rng.uniform(-0.15, 0.15, size=len(clipped))
            ax.scatter(i + jitter, clipped, color=colors[i], alpha=0.4, s=20,
                       edgecolor="white", linewidth=0.3, zorder=4)
            n_out = int(np.sum((f["values"] < y_lo) | (f["values"] > y_hi)))
            label_y = min(max(means[i] + sems[i], y_lo), y_hi - 0.3) + 0.15
            n_label = f"N={f['n']}"
            if n_out > 0:
                n_label += f" ({n_out} clipped)"
            ax.text(i, label_y, n_label,
                    ha="center", va="bottom", fontsize=8, fontweight="bold")

        # Reference lines
        ax.axhline(y=1.0, color="#2ECC71", linewidth=1.5, linestyle="--",
                   alpha=0.7, label="Full Recovery (1.0)", zorder=2)
        ax.axhline(y=0.0, color="#E74C3C", linewidth=1.5, linestyle="--",
                   alpha=0.7, label="No Recovery (0.0)", zorder=2)

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=11, rotation=20, ha="right")
        ax.set_ylabel("Recovery Index", fontsize=13)
        ax.set_ylim(y_lo, y_hi)
        ax.set_title(self.title, fontsize=16, fontweight="bold", pad=15)
        ax.grid(axis="y", alpha=0.2, zorder=0)

        # Category legend
        seen = dict.fromkeys(f["cat"] for f in features_data)
        cat_handles = [
            Patch(facecolor=CATEGORY_COLORS[c], label=c.capitalize())
            for c in seen
        ]
        cat_leg = ax.legend(handles=cat_handles, loc="upper left",
                            framealpha=0.9, fontsize=9, title="Category")
        ax.add_artist(cat_leg)

        # Reference lines legend
        ref_handles = [
            Line2D([0], [0], color="#2ECC71", linewidth=1.5, linestyle="--",
                   label="Full Recovery (1.0)"),
            Line2D([0], [0], color="#E74C3C", linewidth=1.5, linestyle="--",
                   label="No Recovery (0.0)"),
        ]
        ax.legend(handles=ref_handles, loc="upper right", framealpha=0.9,
                  fontsize=9)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        learners = data["learners"]
        stat_details = results["stat_details"]

        stat_lines = " | ".join(stat_details) if stat_details else "(no tests run)"

        return (
            f"SUBJECTS    N={len(learners)} {self.cohort_prefix.rstrip('_')} learners "
            f"(>={self.learner_threshold}% eaten pre-injury on "
            f"{len(self.pre_injury_dates)} test sessions)\n"
            "FORMULA     Recovery Index = (Rehab_mean - Post1_mean) / "
            "(Pre_mean - Post1_mean) per subject\n"
            "            RI=1.0 means full return to pre-injury; "
            "RI=0.0 means no change from acute post-injury\n"
            f"FILTER      All reaches on Pillar tray, per-subject means with "
            f">={self.min_reaches} reaches per phase\n"
            "PHASES      Pre-Injury=last 3 pillar tests | Post-1=1 wk post-injury "
            "| Post-2-4=2-4 wk | Rehab=last 3 pillar rehab\n"
            f"STATS       One-sample t-test vs 0 (no recovery): {stat_lines}\n"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        learners = data["learners"]
        features_data = results["features_data"]

        es_parts = []
        for f in features_data:
            if f["p_val"] < 0.05:
                es_parts.append(f"{f['label']}: d={f['d']:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "No significant effects"

        return FigureLegend(
            question=(
                "How much does each kinematic feature recover during "
                "rehabilitation relative to the acute post-injury deficit?"
            ),
            method=(
                f"N={len(learners)} {self.cohort_prefix.rstrip('_')} learners "
                f"(>={self.learner_threshold}% eaten pre-injury). "
                f"Recovery index = (Rehab - Post1) / (Pre - Post1) per subject, "
                f">={self.min_reaches} reaches per phase required."
            ),
            finding=(
                "Recovery indices vary across kinematic features. Some features "
                "show partial recovery toward pre-injury levels while others "
                "remain near post-injury deficit."
            ),
            analysis=(
                f"{stat_justification('t-test')} "
                "One-sample t-test against 0 (no recovery) per feature."
            ),
            effect_sizes=es_text,
            confounds=(
                "Recovery index is undefined when pre-injury and post-injury "
                "means are identical (denominator = 0). Subjects with fewer than "
                f"{self.min_reaches} reaches per phase are excluded, potentially "
                "biasing toward more active subjects."
            ),
            follow_up=(
                "Do features that recover kinematically correspond to features "
                "that predict pellet retrieval success?"
            ),
        )


# ============================================================================
# Recipe: KinematicPhaseComparison
# ============================================================================

class KinematicPhaseComparison(FigureRecipe):
    """2x2 grid of kinematic features across 4 experimental phases.

    Each panel shows individual subject lines (thin, semi-transparent) and
    group mean +/- SEM (thick black). Paired Wilcoxon signed-rank tests
    compare adjacent phases.

    Multi-panel recipe: overrides create_axes() for 2x2 subgridspec.
    """

    name = "kinematic_phase_comparison"
    title = "Kinematic Features Across Experimental Phases"
    category = "kinematics"
    data_sources = [
        DataSource("csv", "database_dump/reach_data.csv"),
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 13)

    def __init__(self, cohort_prefix="CNT_01_",
                 learner_threshold=LEARNER_THRESHOLD,
                 panel_features=None,
                 pre_injury_dates=None,
                 post_injury_1_dates=None,
                 post_injury_2_4_dates=None,
                 rehab_pillar_cutoff=None,
                 min_reaches_per_phase=3):
        """
        Parameters
        ----------
        cohort_prefix : str
            Subject ID prefix to filter.
        learner_threshold : float
            Minimum % pellets eaten pre-injury to qualify as a learner.
        panel_features : list of tuple, optional
            4 features for the 2x2 grid. Defaults to TOP_FEATURES[:4].
        pre_injury_dates : set of str, optional
            Session dates for pre-injury phase.
        post_injury_1_dates : set of str, optional
            Session dates for post-injury week 1.
        post_injury_2_4_dates : set of str, optional
            Session dates for post-injury weeks 2-4.
        rehab_pillar_cutoff : str, optional
            Date string; sessions >= this on Pillar tray are Rehab.
        min_reaches_per_phase : int
            Minimum reaches per phase per subject to include.
        """
        self.cohort_prefix = cohort_prefix
        self.learner_threshold = learner_threshold
        self.panel_features = panel_features or TOP_FEATURES[:4]
        self.pre_injury_dates = pre_injury_dates or DEFAULT_PRE_INJURY_TEST_DATES
        self.post_injury_1_dates = post_injury_1_dates or DEFAULT_POST_INJURY_1_DATES
        self.post_injury_2_4_dates = post_injury_2_4_dates or DEFAULT_POST_INJURY_2_4_DATES
        self.rehab_pillar_cutoff = rehab_pillar_cutoff or DEFAULT_REHAB_PILLAR_CUTOFF
        self.min_reaches = min_reaches_per_phase

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort_prefix": self.cohort_prefix,
            "learner_threshold": self.learner_threshold,
            "panel_features": [(f, l, c) for f, l, c in self.panel_features],
            "min_reaches_per_phase": self.min_reaches,
            "pre_injury_dates": sorted(self.pre_injury_dates),
            "post_injury_1_dates": sorted(self.post_injury_1_dates),
            "post_injury_2_4_dates": sorted(self.post_injury_2_4_dates),
            "rehab_pillar_cutoff": self.rehab_pillar_cutoff,
        }

    def create_axes(self, fig, plot_gs):
        inner_gs = plot_gs.subgridspec(2, 2, hspace=0.3, wspace=0.3)
        axes = np.array([[fig.add_subplot(inner_gs[r, c])
                          for c in range(2)] for r in range(2)])
        return axes

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        reach, ps, surg = _load_kinematic_data(self.cohort_prefix)
        reach, surgery_date = _assign_phases(
            reach, surg,
            self.pre_injury_dates,
            self.post_injury_1_dates,
            self.post_injury_2_4_dates,
            self.rehab_pillar_cutoff,
        )
        learners = _identify_learners(ps, self.pre_injury_dates,
                                      self.learner_threshold)

        all_phases = list(KINEMATIC_PHASE_COLORS.keys())
        reach = reach[
            (reach["subject_id"].isin(learners))
            & (reach["phase"].isin(all_phases))
        ].copy()

        print(f"  Learners: {len(learners)}, reaches with phases: {len(reach)}",
              flush=True)

        return {
            "reach": reach,
            "learners": learners,
            "surgery_date": surgery_date,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        learners = data["learners"]
        phases = list(KINEMATIC_PHASE_COLORS.keys())
        min_n = self.min_reaches

        panels = []
        all_stat_details = []

        comparisons = [
            ("Pre-Injury", "Post-Injury_1", "Pre vs Post1"),
            ("Post-Injury_1", "Post-Injury_2-4", "Post1 vs Post2-4"),
            ("Post-Injury_2-4", "Rehab_Pillar", "Post2-4 vs Rehab"),
        ]

        for feat, label, cat in self.panel_features:
            panel_info = {"feat": feat, "label": label, "cat": cat}

            if feat not in reach.columns:
                panel_info["subj_means"] = {}
                panel_info["group_means"] = []
                panel_info["group_sems"] = []
                panel_info["stats"] = []
                panels.append(panel_info)
                continue

            # Compute per-subject means across all phases
            subj_means = {}
            for subj in learners:
                subj_data = reach[reach["subject_id"] == subj]
                means = {}
                for phase in phases:
                    vals = subj_data[subj_data["phase"] == phase][feat].dropna()
                    if len(vals) >= min_n:
                        means[phase] = vals.mean()
                if len(means) == len(phases):
                    subj_means[subj] = means

            # Group means and SEMs
            group_means = []
            group_sems = []
            for phase in phases:
                if subj_means:
                    vals = [subj_means[s][phase] for s in subj_means]
                    group_means.append(np.mean(vals))
                    group_sems.append(np.std(vals) / np.sqrt(len(vals)))
                else:
                    group_means.append(np.nan)
                    group_sems.append(0)

            # Paired Wilcoxon tests on adjacent phases
            panel_stats = []
            if len(subj_means) >= 5:
                paired_vals = {
                    p: [subj_means[s][p] for s in subj_means] for p in phases
                }
                for p1, p2, comp_label in comparisons:
                    v1 = np.array(paired_vals[p1])
                    v2 = np.array(paired_vals[p2])
                    try:
                        stat, p = sp_stats.wilcoxon(v1, v2)
                        d = cohens_d_paired(v1, v2)
                        detail = format_stat_result(
                            "Wilcoxon signed-rank", stat, p, d=d,
                            n=len(v1),
                        )
                        panel_stats.append({
                            "comparison": comp_label,
                            "stat": stat,
                            "p": p,
                            "d": d,
                            "detail": f"{label} {comp_label}: {detail}",
                        })
                        all_stat_details.append(f"{label} {comp_label}: {detail}")
                    except Exception:
                        pass

            panel_info["subj_means"] = subj_means
            panel_info["group_means"] = group_means
            panel_info["group_sems"] = group_sems
            panel_info["stats"] = panel_stats
            panels.append(panel_info)

        return {
            "panels": panels,
            "phases": phases,
            "stat_details": all_stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        panels = results["panels"]
        phases = results["phases"]
        cohort_key = self.cohort_prefix.rstrip("_")
        default_color = COHORT_COLORS.get(cohort_key, "#888888")

        fig.suptitle(self.title, fontsize=16, fontweight="bold", y=0.98)

        for idx, panel in enumerate(panels):
            panel_ax = ax[idx // 2][idx % 2]
            feat = panel["feat"]
            label = panel["label"]
            subj_means = panel["subj_means"]

            if not subj_means:
                panel_ax.text(
                    0.5, 0.5, f"Insufficient paired data\nfor {label}",
                    transform=panel_ax.transAxes, ha="center", va="center",
                    fontsize=10,
                )
                continue

            x_pos = {p: i for i, p in enumerate(phases)}

            # Individual subject lines
            for subj, means in subj_means.items():
                xs = [x_pos[p] for p in phases]
                ys = [means[p] for p in phases]
                panel_ax.plot(xs, ys, color=default_color, alpha=0.25,
                              linewidth=0.8, zorder=2)
                panel_ax.scatter(xs, ys, color=default_color, s=15, alpha=0.4,
                                 zorder=3, edgecolor="white", linewidth=0.3)

            # Group means +/- SEM
            group_means = panel["group_means"]
            group_sems = panel["group_sems"]
            panel_ax.errorbar(
                range(len(phases)), group_means, yerr=group_sems,
                color="black", linewidth=2, capsize=5, capthick=1.5,
                marker="o", markersize=8, markerfacecolor="white",
                markeredgecolor="black", markeredgewidth=1.5, zorder=5,
            )

            panel_ax.set_xticks(range(len(phases)))
            panel_ax.set_xticklabels(
                [PHASE_LABELS[p] for p in phases], fontsize=10,
            )
            panel_ax.set_ylabel(label, fontsize=11)
            panel_ax.set_title(label, fontsize=12, fontweight="bold")
            panel_ax.text(
                0.98, 0.98, f"N={len(subj_means)}",
                transform=panel_ax.transAxes,
                ha="right", va="top", fontsize=9, fontweight="bold",
            )
            panel_ax.grid(axis="y", alpha=0.2, zorder=0)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        learners = data["learners"]
        stat_details = results["stat_details"]

        stat_summary = (
            "\n    ".join(stat_details) if stat_details
            else "Insufficient paired data"
        )

        return (
            f"SUBJECTS    N learners from {self.cohort_prefix.rstrip('_')} "
            f"(>={self.learner_threshold}% eaten pre-injury on "
            f"{len(self.pre_injury_dates)} test sessions) "
            f"with >={self.min_reaches} reaches per phase\n"
            f"FILTER      All reaches on Pillar tray, per-subject means with "
            f">={self.min_reaches} reaches per phase\n"
            "PHASES      Pre-Injury=last 3 pillar tests | Post-1=1 wk post-injury "
            "| Post-2-4=2-4 wk | Rehab=last 3 pillar rehab\n"
            f"STATS       Wilcoxon signed-rank (paired) with Cohen's d:\n"
            f"    {stat_summary}\n"
        )

    # -------------------------------------------------------- figure_legend
    def figure_legend(self, data, results):
        learners = data["learners"]
        panels = results["panels"]

        es_parts = []
        for panel in panels:
            for s in panel["stats"]:
                if s["p"] < 0.05:
                    es_parts.append(
                        f"{panel['label']} {s['comparison']}: d={s['d']:.2f}"
                    )
        es_text = "; ".join(es_parts) if es_parts else "No significant effects"

        feature_names = ", ".join(p["label"] for p in panels)

        return FigureLegend(
            question=(
                "How do key kinematic features change across pre-injury, "
                "acute post-injury, chronic post-injury, and rehabilitation phases?"
            ),
            method=(
                f"N={len(learners)} {self.cohort_prefix.rstrip('_')} learners "
                f"(>={self.learner_threshold}% eaten pre-injury). "
                f"Per-subject means with >={self.min_reaches} reaches per phase. "
                f"Features: {feature_names}."
            ),
            finding=(
                "Kinematic features show characteristic disruption after injury "
                "with variable recovery trajectories across features."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                "Paired Wilcoxon signed-rank tests between adjacent phases."
            ),
            effect_sizes=es_text,
            confounds=(
                "Only subjects with data in all 4 phases are included, "
                "potentially biasing toward subjects with higher reach rates. "
                "Phase boundaries are date-based, not behavior-based."
            ),
            follow_up=(
                "Which kinematic features best predict pellet retrieval success, "
                "and do those features show the most recovery?"
            ),
        )
