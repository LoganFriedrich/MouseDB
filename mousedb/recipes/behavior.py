"""
Behavior figure recipes.

PelletScoreRecovery: Connected dot plot of pellet retrieval recovery after
CST injury, with individual subject lines, cohort means, overall mean +/- SEM,
and paired Wilcoxon signed-rank significance brackets.
"""

from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from scipy import stats

from mousedb import MOUSEDB_ROOT
from mousedb.figures.palettes import (
    COHORT_COLORS,
    PELLET_PHASE_COLORS,
    get_persistent_subject_colors,
    get_subject_label,
)
from mousedb.figures.annotations import add_stat_bracket
from mousedb.figures.legends import FigureLegend
from mousedb.figures.stats import cohens_d_paired, format_stat_result, stat_justification
from mousedb.recipes.base import DataSource, FigureRecipe


# ============================================================================
# Constants (lab standard)
# ============================================================================

PELLET_ORDER = ["Last 3", "Post Injury 1", "Post Injury 2-4", "Rehab Pillar"]

PHASE_DEFINITIONS = {
    "Last 3": "Last 3 pre-injury pillar test sessions before CST lesion surgery",
    "Post Injury 1": "First pillar test session after CST injury (day 1 post-op)",
    "Post Injury 2-4": "Pillar test sessions on post-injury days 2, 3, and 4",
    "Rehab Pillar": "All pillar test sessions during rehabilitation phase",
}


# ============================================================================
# Helpers
# ============================================================================

def _pellet_timepoint_from_phase(test_phase):
    """Map test_phase name to 4-group pellet timepoint (lab-standard labels).

    Includes Training_Pillar as pre-injury baseline (last training sessions
    before surgery, same as grant analysis scripts).
    """
    if not isinstance(test_phase, str):
        return None
    tp = test_phase.strip()
    tp_norm = tp.replace(" ", "_")

    if tp_norm.startswith("Pre-Injury_Test") or tp_norm.startswith("Training_Pillar"):
        return "Last 3"
    elif tp_norm in ("Post-Injury_Test_1",):
        return "Post Injury 1"
    elif tp_norm in ("Post-Injury_Test_2", "Post-Injury_Test_3", "Post-Injury_Test_4"):
        return "Post Injury 2-4"
    elif tp_norm.startswith("Rehab"):
        return "Rehab Pillar"
    return None


# ============================================================================
# Recipe
# ============================================================================

class PelletScoreRecovery(FigureRecipe):
    """Connected dot plot of % pellets eaten across 4 recovery phases.

    Individual subjects shown as colored lines, cohort means as thick lines
    with square markers, overall mean as black diamonds with SEM error bars.
    Paired Wilcoxon signed-rank tests shown as significance brackets.
    """

    name = "pellet_score_recovery"
    title = "Pellet Retrieval Recovery After CST Injury"
    category = "behavior"
    data_sources = [DataSource("csv", "database_dump/pellet_scores.csv")]
    figsize = (13, 10)

    def __init__(self, cohorts=None, learner_threshold=5.0):
        """
        Parameters
        ----------
        cohorts : list of str, optional
            Cohort IDs to include (e.g., ["CNT_01", "CNT_02"]).
            If None, includes all cohorts found in the data.
        learner_threshold : float
            Minimum % pellets eaten pre-injury to qualify as a learner.
            Subjects below this threshold are excluded.
        """
        self.cohorts = cohorts
        self.learner_threshold = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        """Return recipe parameters for provenance tracking."""
        return {
            "cohorts": self.cohorts,
            "learner_threshold": self.learner_threshold,
            "pellet_order": PELLET_ORDER,
            "phase_definitions": PHASE_DEFINITIONS,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        """Load pellet_scores.csv, filter, compute per-subject % eaten."""
        csv_path = MOUSEDB_ROOT / "database_dump" / "pellet_scores.csv"
        print(f"  Loading {csv_path.name}...", flush=True)
        df = pd.read_csv(csv_path, low_memory=False)

        # Only pillar trays
        df = df[df["tray_type"] == "P"].copy()

        # Parse cohort from subject_id  (e.g. "CNT_01_03" -> "CNT_01")
        df["cohort"] = df["subject_id"].str.rsplit("_", n=1).str[0]

        if self.cohorts:
            df = df[df["cohort"].isin(self.cohorts)]

        # Map test_phase to 4-group timepoint
        df["timepoint"] = df["test_phase"].apply(_pellet_timepoint_from_phase)
        df = df.dropna(subset=["timepoint"])

        # Collect unique test_phases per timepoint for documentation
        phase_details = {}
        for tp in PELLET_ORDER:
            phases = sorted(df[df["timepoint"] == tp]["test_phase"].unique())
            phase_details[tp] = phases

        # Calculate % eaten per subject per timepoint
        df["eaten"] = (df["score"] == 2).astype(int)

        subj_tp = (
            df.groupby(["subject_id", "timepoint"])["eaten"]
            .mean()
            .reset_index()
            .rename(columns={"eaten": "pct_eaten"})
        )
        subj_tp["pct_eaten"] *= 100

        # Add cohort info
        subj_cohort = df[["subject_id", "cohort"]].drop_duplicates()
        subj_tp = subj_tp.merge(subj_cohort, on="subject_id", how="left")

        # Exclusion criterion: only learners (>= threshold eaten at pre-injury)
        pre_scores = subj_tp[subj_tp["timepoint"] == "Last 3"]
        learners = set(
            pre_scores[pre_scores["pct_eaten"] >= self.learner_threshold]["subject_id"]
        )
        n_total = pre_scores["subject_id"].nunique()
        n_excluded = n_total - len(learners)
        print(
            f"  Learner filter (>={self.learner_threshold}% eaten pre-injury): "
            f"{len(learners)} learners, {n_excluded} excluded",
            flush=True,
        )
        subj_tp = subj_tp[subj_tp["subject_id"].isin(learners)]

        # Aggregate stats per timepoint
        agg = (
            subj_tp.groupby("timepoint")["pct_eaten"]
            .agg(["mean", "std", "count"])
            .reindex(PELLET_ORDER)
        )
        agg["sem"] = agg["std"] / np.sqrt(agg["count"])

        # Aggregate stats per cohort per timepoint
        cohort_agg = (
            subj_tp.groupby(["cohort", "timepoint"])["pct_eaten"]
            .agg(["mean", "std", "count"])
            .reset_index()
        )

        # Print summary
        for tp in PELLET_ORDER:
            if tp in agg.index and not pd.isna(agg.loc[tp, "mean"]):
                row = agg.loc[tp]
                print(
                    f"    {tp:20s}: {row['mean']:.1f}% +/- {row['std']:.1f}%  "
                    f"(n={int(row['count'])} subjects)",
                    flush=True,
                )

        cohorts_used = sorted(subj_tp["cohort"].unique())

        return {
            "df": df,
            "subj_tp": subj_tp,
            "learners": learners,
            "excluded_count": n_excluded,
            "total_count": n_total,
            "cohorts": cohorts_used,
            "agg": agg,
            "cohort_agg": cohort_agg,
            "phase_details": phase_details,
            "csv_path": csv_path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Run paired Wilcoxon signed-rank tests on the 4 key comparisons."""
        subj_tp = data["subj_tp"]

        wide = subj_tp.pivot(
            index="subject_id", columns="timepoint", values="pct_eaten"
        )

        comparisons = [
            ("Last 3", "Post Injury 1", "greater"),
            ("Last 3", "Post Injury 2-4", "greater"),
            ("Post Injury 2-4", "Rehab Pillar", "less"),
            ("Last 3", "Rehab Pillar", "two-sided"),
        ]

        p_values = {}
        effect_sizes = {}
        stat_details = []
        print("  Statistical tests (Wilcoxon signed-rank, paired):", flush=True)
        for a, b, alt in comparisons:
            if a not in wide.columns or b not in wide.columns:
                print(f"    {a} vs {b}: no data for one group", flush=True)
                continue
            paired = wide[[a, b]].dropna()
            if len(paired) < 5:
                print(
                    f"    {a} vs {b}: too few paired subjects (n={len(paired)})",
                    flush=True,
                )
                continue
            try:
                stat, p = stats.wilcoxon(paired[a], paired[b], alternative=alt)
                p_values[(a, b)] = p
                d = cohens_d_paired(paired[a].values, paired[b].values)
                effect_sizes[(a, b)] = d
                detail = format_stat_result(
                    "Wilcoxon signed-rank", stat, p, d=d,
                    n=len(paired), alternative=alt,
                )
                stat_details.append(detail)
                print(f"    {detail}", flush=True)
            except Exception as e:
                print(f"    {a} vs {b}: test failed ({e})", flush=True)

        return {
            "p_values": p_values,
            "effect_sizes": effect_sizes,
            "stat_details": stat_details,
            "comparisons": comparisons,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        """Draw connected dot plot on the provided axes."""
        subj_tp = data["subj_tp"]
        learners = data["learners"]
        agg = data["agg"]
        cohort_agg = data["cohort_agg"]
        p_values = results["p_values"]
        comparisons = results["comparisons"]
        n_subjects = len(learners)

        x_positions = np.arange(len(PELLET_ORDER))
        tp_to_x = {tp: i for i, tp in enumerate(PELLET_ORDER)}

        # --- Individual subject lines (unique color per subject) ---
        subject_colors = get_persistent_subject_colors(sorted(learners))
        for subj in sorted(learners):
            sdata = subj_tp[subj_tp["subject_id"] == subj]
            color = subject_colors[subj]
            short_id = get_subject_label(subj)

            xs = []
            ys = []
            for tp in PELLET_ORDER:
                row = sdata[sdata["timepoint"] == tp]
                if len(row) > 0:
                    xs.append(tp_to_x[tp])
                    ys.append(row.iloc[0]["pct_eaten"])

            legend_label = short_id

            if len(xs) >= 2:
                ax.plot(
                    xs, ys, color=color, alpha=0.7, linewidth=1.3, zorder=3,
                    label=legend_label,
                )
            if xs:
                ax.scatter(
                    xs, ys, color=color, s=45, zorder=4,
                    edgecolors="white", linewidths=0.5, alpha=0.9,
                )

        # --- Cohort means (thick colored lines with square markers) ---
        for cohort in sorted(COHORT_COLORS.keys()):
            cdata = cohort_agg[cohort_agg["cohort"] == cohort]
            color = COHORT_COLORS[cohort]
            n_cohort = subj_tp[
                subj_tp["cohort"] == cohort
            ]["subject_id"].nunique()

            if n_cohort == 0:
                continue

            xs = []
            ys = []
            for tp in PELLET_ORDER:
                row = cdata[cdata["timepoint"] == tp]
                if len(row) > 0 and row.iloc[0]["count"] > 0:
                    xs.append(tp_to_x[tp])
                    ys.append(row.iloc[0]["mean"])

            if len(xs) >= 2:
                ax.plot(
                    xs, ys, color=color, linewidth=2.5, alpha=0.85, zorder=7,
                    marker="s", markersize=8, markeredgecolor="white",
                    markeredgewidth=0.8,
                    label=f"{cohort} mean (N={n_cohort})",
                )

        # --- Overall mean (thick black line with diamond markers + SEM) ---
        means = [
            agg.loc[tp, "mean"]
            if tp in agg.index and not pd.isna(agg.loc[tp, "mean"])
            else np.nan
            for tp in PELLET_ORDER
        ]
        sems = [
            agg.loc[tp, "sem"]
            if tp in agg.index and not pd.isna(agg.loc[tp, "sem"])
            else 0
            for tp in PELLET_ORDER
        ]

        ax.errorbar(
            x_positions, means, yerr=sems,
            fmt="D-", color="black", linewidth=3, markersize=10,
            elinewidth=2, capsize=8, capthick=2, zorder=9,
            markeredgecolor="white", markeredgewidth=1,
            label=f"Overall mean (N={n_subjects})",
        )

        # --- Significance brackets ---
        max_data = subj_tp["pct_eaten"].max()
        bracket_y = max_data + 3
        bracket_step = 4.0

        for idx, (a, b, _alt) in enumerate(comparisons):
            if (a, b) in p_values:
                y_pos = bracket_y + idx * bracket_step
                add_stat_bracket(ax, tp_to_x[a], tp_to_x[b], y_pos, p_values[(a, b)])

        ax.set_ylim(-2, bracket_y + len(comparisons) * bracket_step + 4)

        # --- Axis formatting ---
        ax.set_xticks(x_positions)
        ax.set_xticklabels(PELLET_ORDER, fontsize=12, fontweight="bold")
        ax.set_ylabel("Pellets Retrieved (%)", fontsize=13, fontweight="bold")
        ax.set_title(
            "Pellet Retrieval Recovery After CST Injury",
            fontsize=16, fontweight="bold", pad=12,
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=11)
        ax.grid(axis="y", alpha=0.15, linewidth=0.5)

        # Legend: subjects first (2 columns), then means
        handles, labels = ax.get_legend_handles_labels()
        subj_items = [(h, l) for h, l in zip(handles, labels) if "mean" not in l]
        mean_items = [(h, l) for h, l in zip(handles, labels) if "mean" in l]
        ordered = subj_items + mean_items
        if ordered:
            ax.legend(
                [h for h, l in ordered], [l for h, l in ordered],
                fontsize=7.5, loc="upper right", framealpha=0.9,
                edgecolor="#cccccc", title="Subjects & Means", title_fontsize=9,
                ncol=2, borderpad=0.6, columnspacing=1.0,
                handlelength=1.5,
            )

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        """Generate methodology panel text."""
        learners = data["learners"]
        n_subjects = len(learners)
        n_excluded = data["excluded_count"]
        n_total = data["total_count"]
        cohorts_used = data["cohorts"]
        stat_details = results["stat_details"]

        cohort_str = ", ".join(sorted(cohorts_used))

        phase_lines = "\n".join(
            f"  {tp:20s} {PHASE_DEFINITIONS.get(tp, 'undefined')}"
            for tp in PELLET_ORDER
        )

        stat_lines = (
            "\n".join(f"  {d}" for d in stat_details)
            if stat_details
            else "  (no tests run)"
        )

        info = (
            f"EXPERIMENT  Skilled reaching task, single-pellet retrieval (pillar tray), "
            f"CST lesion injury model\n"
            f"SUBJECTS    N={n_subjects} learners from {cohort_str} "
            f"({n_excluded} excluded: <{self.learner_threshold}% retrieved pre-injury; "
            f"{n_total} total screened)\n"
            f"METRIC      % pellets scored 'eaten' (score=2) per subject per phase\n"
            f"PHASES\n{phase_lines}\n"
            f"STATISTICS  Wilcoxon signed-rank test (paired, directional where indicated). "
            f"* p<0.05  ** p<0.01  *** p<0.001\n{stat_lines}\n"
            f"PLOT        Thin lines = individual subjects (unique color per subject, see legend). "
            f"Squares = cohort means. Diamonds = overall mean +/- SEM."
        )

        return info

    def figure_legend(self, data, results):
        """Build structured figure legend."""
        n_subjects = len(data["learners"])
        cohorts_str = ", ".join(data["cohorts"])
        effect_sizes = results.get("effect_sizes", {})

        # Format effect sizes
        es_parts = []
        for (a, b), d in effect_sizes.items():
            if not np.isnan(d):
                es_parts.append(f"{a} vs {b}: d={d:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "Not computed"

        return FigureLegend(
            question="Does pellet retrieval recover after CST injury?",
            method=(
                f"N={n_subjects} learners (>={self.learner_threshold}% eaten pre-injury) "
                f"from cohorts {cohorts_str}. Pillar tray sessions only. "
                f"% pellets scored 'eaten' per subject per phase."
            ),
            finding=(
                "Retrieval drops acutely post-injury and shows partial "
                "recovery during rehabilitation in most subjects."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                f"Four comparisons tested with directional hypotheses."
            ),
            effect_sizes=es_text,
            confounds=(
                "Tray familiarity not controlled across phases. "
                "Hand preference and lesion size may vary between subjects."
            ),
            follow_up=(
                "Do kinematic profiles of successful reaches also recover, "
                "or is recovery limited to success rate?"
            ),
        )


# ============================================================================
# Constants for CNT_01-specific recipes
# ============================================================================

CNT01_WINDOW_LABELS = [
    "Pre-Injury\n(Last 3)",
    "Post-Injury\n1 Wk",
    "Post-Injury\n2-4 Wk",
    "Post-Rehab\n(Last 3)",
]
CNT01_WINDOW_KEYS = ["pre_injury", "post_1", "post_2_4", "rehab_pillar"]

CNT01_PHASE_DEFINITIONS = {
    "pre_injury": "Last 3 pre-injury pillar test sessions before CST lesion surgery",
    "post_1": "First week post-injury pillar test session",
    "post_2_4": "Post-injury weeks 2-4 pillar test sessions",
    "rehab_pillar": "Last 3 pillar rehab sessions (excluding day-1 re-acclimation)",
}

# Score-integer to palette color mapping.
# The canonical OUTCOME_COLORS in palettes.py uses string keys; this maps
# the integer score values (0, 1, 2) used in pellet_scores.csv to the same
# hex colors from that palette.
OUTCOME_SCORE_COLORS = {
    2: "#27AE60",   # retrieved (green) -- matches OUTCOME_COLORS["retrieved"]
    1: "#F39C12",   # displaced (orange) -- matches OUTCOME_COLORS["displaced"]
    0: "#E74C3C",   # miss (red) -- matches OUTCOME_COLORS["miss"]
}

OUTCOME_LABELS = {2: "Retrieved", 1: "Displaced", 0: "Miss"}


# ============================================================================
# Helpers for CNT_01 phase mapping
# ============================================================================

def _map_cnt01_window(test_phase, tray_type, session_date):
    """Map test_phase to CNT_01 analysis window key.

    Parameters
    ----------
    test_phase : str
        The test_phase value from pellet_scores.csv.
    tray_type : str
        Tray type code (P = pillar).
    session_date : pd.Timestamp
        Session date (used to exclude day-1 rehab re-acclimation).

    Returns
    -------
    str or None
        Window key, or None if the row does not belong to any window.
    """
    if not isinstance(test_phase, str):
        return None
    phase = test_phase.strip()

    # Pre-Injury Test on Pillar
    if "Pre-Injury" in phase and tray_type == "P":
        return "pre_injury"

    # Post-Injury Test 1
    norm = phase.replace(" ", "_")
    if norm == "Post-Injury_Test_1":
        return "post_1"

    # Post-Injury Test 2-4
    if any(norm == f"Post-Injury_Test_{i}" for i in [2, 3, 4]):
        return "post_2_4"

    # Rehab on Pillar tray only -- exclude first day (re-acclimation)
    if "Rehab" in phase and tray_type == "P":
        if session_date >= pd.Timestamp("2025-08-20"):
            return "rehab_pillar"
        return None

    return None


# ============================================================================
# Recipe: PelletRecoveryCNT01
# ============================================================================

class PelletRecoveryCNT01(FigureRecipe):
    """Connected dot plot of pellet retrieval recovery for CNT_01 cohort.

    CNT_01 is the only cohort with full longitudinal phase annotations.
    Individual subjects shown as colored lines, group mean with SEM error
    bars, and paired Wilcoxon signed-rank significance brackets.
    """

    name = "pellet_recovery_cnt01"
    title = "Pellet Retrieval Recovery (CNT_01)"
    category = "behavior"
    data_sources = [DataSource("csv", "database_dump/pellet_scores.csv")]
    figsize = (14, 11)

    LEARNER_THRESHOLD = 5.0  # % eaten pre-injury to qualify as learner

    def __init__(self, learner_threshold=None):
        if learner_threshold is not None:
            self.LEARNER_THRESHOLD = learner_threshold

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort": "CNT_01",
            "learner_threshold": self.LEARNER_THRESHOLD,
            "window_keys": CNT01_WINDOW_KEYS,
            "phase_definitions": CNT01_PHASE_DEFINITIONS,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        """Load pellet_scores.csv, filter to CNT_01, compute % eaten."""
        csv_path = MOUSEDB_ROOT / "database_dump" / "pellet_scores.csv"
        print(f"  Loading {csv_path.name}...", flush=True)
        df = pd.read_csv(csv_path, low_memory=False)

        # CNT_01 only -- the only cohort with full longitudinal phases
        df = df[df["subject_id"].str.match(r"CNT_01_")].copy()
        df["cohort"] = df["subject_id"].str[:6]
        df["session_date"] = pd.to_datetime(df["session_date"])

        # Map to analysis windows
        df["window"] = df.apply(
            lambda r: _map_cnt01_window(
                str(r["test_phase"]) if pd.notna(r["test_phase"]) else "",
                r["tray_type"],
                r["session_date"],
            ),
            axis=1,
        )
        df = df[df["window"].notna()].copy()

        # % eaten per subject per window
        df["eaten"] = (df["score"] == 2).astype(int)
        grouped = (
            df.groupby(["subject_id", "cohort", "window"])
            .agg(n_pellets=("eaten", "count"), n_eaten=("eaten", "sum"))
            .reset_index()
        )
        grouped["pct_eaten"] = 100.0 * grouped["n_eaten"] / grouped["n_pellets"]

        # Filter to learners
        pre = grouped[grouped["window"] == "pre_injury"]
        learner_ids = set(
            pre[pre["pct_eaten"] >= self.LEARNER_THRESHOLD]["subject_id"]
        )
        n_total = pre["subject_id"].nunique()
        n_excluded = n_total - len(learner_ids)
        grouped = grouped[grouped["subject_id"].isin(learner_ids)].copy()

        subjects = sorted(grouped["subject_id"].unique())
        print(
            f"  Learner filter (>={self.LEARNER_THRESHOLD}% eaten pre-injury): "
            f"{len(learner_ids)} learners, {n_excluded} excluded",
            flush=True,
        )

        # Aggregate stats per window
        agg_stats = {}
        for w in CNT01_WINDOW_KEYS:
            w_data = grouped[grouped["window"] == w]["pct_eaten"]
            agg_stats[w] = {
                "mean": w_data.mean(),
                "sem": w_data.sem(),
                "n": len(w_data),
            }
            if len(w_data) > 0:
                print(
                    f"    {w:20s}: {w_data.mean():.1f}% +/- {w_data.std():.1f}%  "
                    f"(n={len(w_data)} subjects)",
                    flush=True,
                )

        return {
            "df": df,
            "grouped": grouped,
            "subjects": subjects,
            "learner_ids": learner_ids,
            "excluded_count": n_excluded,
            "total_count": n_total,
            "agg_stats": agg_stats,
            "csv_path": csv_path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Paired Wilcoxon signed-rank tests on 4 key comparisons."""
        grouped = data["grouped"]

        comparisons = [
            ("pre_injury", "post_1", "Pre vs Post-1"),
            ("post_1", "post_2_4", "Post-1 vs Post-2-4"),
            ("post_2_4", "rehab_pillar", "Post-2-4 vs Rehab"),
            ("pre_injury", "rehab_pillar", "Pre vs Rehab"),
        ]

        p_values = {}
        effect_sizes = {}
        stat_details = []
        print("  Statistical tests (Wilcoxon signed-rank, paired):", flush=True)

        for w1, w2, label in comparisons:
            d1 = (
                grouped[grouped["window"] == w1][["subject_id", "pct_eaten"]]
                .set_index("subject_id")
            )
            d2 = (
                grouped[grouped["window"] == w2][["subject_id", "pct_eaten"]]
                .set_index("subject_id")
            )
            paired = d1.join(d2, lsuffix="_1", rsuffix="_2").dropna()

            if len(paired) < 5:
                print(
                    f"    {label}: too few paired subjects (n={len(paired)})",
                    flush=True,
                )
                continue

            try:
                stat_val, p = stats.wilcoxon(
                    paired["pct_eaten_1"], paired["pct_eaten_2"]
                )
                p_values[(w1, w2)] = p
                d = cohens_d_paired(
                    paired["pct_eaten_1"].values, paired["pct_eaten_2"].values
                )
                effect_sizes[(w1, w2)] = d
                detail = format_stat_result(
                    "Wilcoxon signed-rank", stat_val, p, d=d,
                    n=len(paired), alternative="two-sided",
                )
                stat_details.append(f"{label}: {detail}")
                print(f"    {label}: {detail}", flush=True)
            except Exception as e:
                print(f"    {label}: test failed ({e})", flush=True)

        return {
            "comparisons": comparisons,
            "p_values": p_values,
            "effect_sizes": effect_sizes,
            "stat_details": stat_details,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        """Draw connected dot plot on the provided axes."""
        grouped = data["grouped"]
        subjects = data["subjects"]
        agg_stats = data["agg_stats"]
        p_values = results["p_values"]
        comparisons = results["comparisons"]

        x_pos = {w: i for i, w in enumerate(CNT01_WINDOW_KEYS)}

        # Individual subject lines
        for subj in subjects:
            subj_data = grouped[grouped["subject_id"] == subj]
            cohort = subj_data["cohort"].iloc[0]
            color = COHORT_COLORS.get(cohort, "#888888")

            xs, ys = [], []
            for w in CNT01_WINDOW_KEYS:
                row = subj_data[subj_data["window"] == w]
                if not row.empty:
                    xs.append(x_pos[w])
                    ys.append(row["pct_eaten"].iloc[0])

            if len(xs) >= 2:
                ax.plot(xs, ys, color=color, alpha=0.3, linewidth=1, zorder=2)
                ax.scatter(
                    xs, ys, color=color, s=25, alpha=0.5, zorder=3,
                    edgecolor="white", linewidth=0.3,
                )

        # Group means with SEM
        means = [agg_stats[w]["mean"] for w in CNT01_WINDOW_KEYS]
        sems = [agg_stats[w]["sem"] for w in CNT01_WINDOW_KEYS]
        ns = [agg_stats[w]["n"] for w in CNT01_WINDOW_KEYS]

        ax.errorbar(
            range(len(CNT01_WINDOW_KEYS)), means, yerr=sems,
            color="black", linewidth=2.5, capsize=6, capthick=2,
            marker="o", markersize=10, markerfacecolor="white",
            markeredgecolor="black", markeredgewidth=2,
            zorder=5, label="Group Mean +/- SEM",
        )

        # N labels above each point
        for i, (m, sem, n) in enumerate(zip(means, sems, ns)):
            ax.text(
                i, m + sem + 2, f"N={n}", ha="center", va="bottom",
                fontsize=9, fontweight="bold",
            )

        # Significance brackets
        max_y = max(m + s for m, s in zip(means, sems))
        bracket_y = max_y + 8
        for idx, (w1, w2, _label) in enumerate(comparisons):
            if (w1, w2) in p_values:
                y = bracket_y + idx * 5
                add_stat_bracket(ax, x_pos[w1], x_pos[w2], y, p_values[(w1, w2)])

        ax.set_ylim(-2, bracket_y + len(comparisons) * 5 + 8)
        ax.set_xticks(range(len(CNT01_WINDOW_KEYS)))
        ax.set_xticklabels(CNT01_WINDOW_LABELS, fontsize=12)
        ax.set_ylabel("% Pellets Eaten", fontsize=13)
        ax.set_title(
            "Pellet Retrieval Recovery Across Experimental Phases (CNT_01)",
            fontsize=16, fontweight="bold", pad=15,
        )
        ax.grid(axis="y", alpha=0.2, zorder=0)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        # Legend
        from matplotlib.lines import Line2D
        handles = [
            Line2D([0], [0], color=COHORT_COLORS[c], linewidth=2, label=c)
            for c in sorted(COHORT_COLORS.keys())
            if any(grouped["cohort"] == c)
        ]
        handles.append(Line2D(
            [0], [0], color="black", linewidth=2.5,
            marker="o", markersize=8, markerfacecolor="white",
            label="Group Mean",
        ))
        ax.legend(handles=handles, loc="upper right", framealpha=0.9, fontsize=9)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n_subjects = len(data["learner_ids"])
        stat_details = results["stat_details"]
        stat_summary = (
            " | ".join(stat_details) if stat_details
            else "Insufficient paired data"
        )
        phase_lines = "\n".join(
            f"  {k:20s} {CNT01_PHASE_DEFINITIONS[k]}"
            for k in CNT01_WINDOW_KEYS
        )
        return (
            f"SUBJECTS    N={n_subjects} learners (>={self.LEARNER_THRESHOLD}% eaten pre-injury) "
            f"from CNT_01 (only cohort with full longitudinal phase annotations)\n"
            f"METRIC      % pellets eaten per subject per phase window (Pillar tray only)\n"
            f"PHASES\n{phase_lines}\n"
            f"STATS       Wilcoxon signed-rank (paired, two-sided) with Cohen's d (paired)\n"
            f"RESULTS     {stat_summary}"
        )

    def figure_legend(self, data, results):
        n_subjects = len(data["learner_ids"])
        effect_sizes = results.get("effect_sizes", {})
        es_parts = []
        for (a, b), d in effect_sizes.items():
            if not np.isnan(d):
                es_parts.append(f"{a} vs {b}: d={d:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "Does pellet retrieval recover after CST injury "
                "in the CNT_01 cohort?"
            ),
            method=(
                f"N={n_subjects} learners (>={self.LEARNER_THRESHOLD}% eaten pre-injury) "
                f"from CNT_01 cohort only. Pillar tray sessions only. "
                f"% pellets scored 'eaten' (score=2) per subject per phase window."
            ),
            finding=(
                "Retrieval drops acutely post-injury and shows partial "
                "recovery during rehabilitation in most subjects."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                f"Four paired comparisons tested (two-sided)."
            ),
            effect_sizes=es_text,
            confounds=(
                "CNT_01 only -- results may not generalize to other cohorts. "
                "Rehab phase excludes day-1 re-acclimation session. "
                "Tray familiarity not controlled across phases."
            ),
            follow_up=(
                "Do other cohorts with partial phase annotations show "
                "consistent patterns? Do kinematic profiles also recover?"
            ),
        )


# ============================================================================
# Recipe: OutcomeDistributionShift
# ============================================================================

class OutcomeDistributionShift(FigureRecipe):
    """Stacked bar chart of miss/displaced/retrieved outcome proportions.

    Shows how the distribution of reach outcomes shifts across experimental
    phases after CST injury. CNT_01 cohort, pillar tray sessions only.
    """

    name = "outcome_distribution_shift"
    title = "Outcome Distribution Shifts Across Experimental Phases"
    category = "behavior"
    data_sources = [DataSource("csv", "database_dump/pellet_scores.csv")]
    figsize = (14, 11)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort": "CNT_01",
            "tray_type": "P",
            "window_keys": CNT01_WINDOW_KEYS,
            "phase_definitions": CNT01_PHASE_DEFINITIONS,
            "outcome_labels": OUTCOME_LABELS,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        """Load pellet_scores.csv, filter to CNT_01 pillar sessions."""
        csv_path = MOUSEDB_ROOT / "database_dump" / "pellet_scores.csv"
        print(f"  Loading {csv_path.name}...", flush=True)
        df = pd.read_csv(csv_path, low_memory=False)

        # CNT_01 only
        df = df[df["subject_id"].str.match(r"CNT_01_")].copy()
        df["cohort"] = df["subject_id"].str[:6]
        df["session_date"] = pd.to_datetime(df["session_date"])

        # Map to analysis windows
        df["window"] = df.apply(
            lambda r: _map_cnt01_window(
                str(r["test_phase"]) if pd.notna(r["test_phase"]) else "",
                r["tray_type"],
                r["session_date"],
            ),
            axis=1,
        )
        df = df[df["window"].notna()].copy()

        # Filter to Pillar tray only
        df = df[df["tray_type"] == "P"].copy()

        # Compute proportions per window
        score_labels = [0, 1, 2]
        proportions = {s: [] for s in score_labels}
        counts = []

        for w in CNT01_WINDOW_KEYS:
            w_data = df[df["window"] == w]
            total = len(w_data)
            counts.append(total)
            for s in score_labels:
                n = len(w_data[w_data["score"] == s])
                proportions[s].append(100.0 * n / total if total > 0 else 0)

        print("  Outcome proportions per phase:", flush=True)
        for w, total in zip(CNT01_WINDOW_KEYS, counts):
            print(f"    {w:20s}: N={total} pellets", flush=True)

        return {
            "df": df,
            "proportions": proportions,
            "counts": counts,
            "score_labels": score_labels,
            "csv_path": csv_path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Descriptive analysis -- proportions are the primary measure."""
        proportions = data["proportions"]
        counts = data["counts"]

        # Build summary table for methodology
        summary_rows = []
        for i, w in enumerate(CNT01_WINDOW_KEYS):
            row = {"window": w, "n_pellets": counts[i]}
            for s in data["score_labels"]:
                row[OUTCOME_LABELS[s]] = proportions[s][i]
            summary_rows.append(row)

        return {
            "summary": summary_rows,
            "note": "Descriptive proportions; no inferential test applied.",
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        """Draw stacked bar chart on the provided axes."""
        proportions = data["proportions"]
        counts = data["counts"]
        score_labels = data["score_labels"]

        x = np.arange(len(CNT01_WINDOW_KEYS))
        bottom = np.zeros(len(CNT01_WINDOW_KEYS))

        for s in score_labels:
            vals = proportions[s]
            ax.bar(
                x, vals, bottom=bottom, width=0.6,
                label=OUTCOME_LABELS[s],
                color=OUTCOME_SCORE_COLORS[s],
                edgecolor="white", linewidth=0.5,
                zorder=3,
            )
            # Percentage labels inside bars
            for i, (v, b) in enumerate(zip(vals, bottom)):
                if v > 5:
                    ax.text(
                        i, b + v / 2, f"{v:.1f}%",
                        ha="center", va="center",
                        fontsize=10, fontweight="bold", color="white",
                    )
            bottom += np.array(vals)

        # Total N labels above bars
        for i, n in enumerate(counts):
            ax.text(
                i, 102, f"N={n:,}", ha="center", va="bottom",
                fontsize=9, fontweight="bold",
            )

        ax.set_xticks(x)
        ax.set_xticklabels(CNT01_WINDOW_LABELS, fontsize=12)
        ax.set_ylabel("Proportion (%)", fontsize=13)
        ax.set_title(
            "Outcome Distribution Shifts Across Experimental Phases",
            fontsize=16, fontweight="bold", pad=15,
        )
        ax.set_ylim(0, 115)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper right", framealpha=0.9, fontsize=11)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        counts = data["counts"]
        total = sum(counts)
        phase_counts = ", ".join(
            f"{w}={n}" for w, n in zip(CNT01_WINDOW_KEYS, counts)
        )
        return (
            "SCORING     MouseReach automated scoring: 0=Miss, 1=Displaced, 2=Retrieved\n"
            "FILTER      Pillar tray sessions only, CNT_01 cohort\n"
            f"N           {total:,} total pellets ({phase_counts})\n"
            "PHASES      Pre-Injury=last 3 pillar tests | Post-1=1wk | "
            "Post-2-4=2-4wk | Rehab=last 3 pillar rehab sessions "
            "(excluding day-1 re-acclimation)\n"
            "NOTE        The SHIFT in outcome proportions is the primary "
            "behavioral measure of CST deficit"
        )

    def figure_legend(self, data, results):
        counts = data["counts"]
        total = sum(counts)
        return FigureLegend(
            question=(
                "How does the distribution of reach outcomes change after "
                "CST injury and during rehabilitation?"
            ),
            method=(
                f"All pellet scores (N={total:,}) from Pillar tray sessions, "
                f"CNT_01 cohort, grouped by phase. Score 0=Miss (pellet untouched), "
                f"1=Displaced (contacted but not eaten), 2=Retrieved (successfully eaten)."
            ),
            finding=(
                "Post-injury shows dramatic shift: retrieval drops, displacement "
                "increases. During rehabilitation, partial recovery of retrieval "
                "proportion."
            ),
            analysis=(
                "Descriptive proportions. The shift in outcome distribution IS "
                "the behavioral signature of CST injury. No inferential test "
                "applied -- the proportion shift is the primary measure."
            ),
            effect_sizes=(
                "N/A -- proportions across phases (no paired test for proportions)."
            ),
            confounds=(
                "Unequal observation counts across phases. "
                "Rehab phase includes training sessions (mixed difficulty). "
                "CNT_01 only."
            ),
            follow_up=(
                "Does outcome distribution differ between recovered and "
                "non-recovered animals? Does retrieval quality also change?"
            ),
        )


# ============================================================================
# Additional imports for migrated recipes
# ============================================================================

import warnings
from collections import defaultdict
from matplotlib.lines import Line2D
import matplotlib.gridspec as gridspec

from mousedb.figures.stats import cohens_d  # independent-samples Cohen's d

# Try GEE import; fall back gracefully
try:
    from statsmodels.genmod.generalized_estimating_equations import GEE
    from statsmodels.genmod.families import Binomial
    from statsmodels.genmod.cov_struct import Exchangeable
    _HAS_GEE = True
except ImportError:
    _HAS_GEE = False


# ============================================================================
# Shared constants for migrated cohort-level recipes
# ============================================================================

_COHORTS = ["CNT_01", "CNT_02", "CNT_03", "CNT_04"]

_WINDOW_LABELS = [
    "Pre-Injury\n(Last 3)",
    "Post-Injury\n1 Wk",
    "Post-Injury\n2-4 Wk",
    "Rehab\nPillar",
]
_WINDOW_KEYS = ["pre_injury", "post_1", "post_2_4", "rehab_pillar"]

_LEARNER_EATEN_THRESHOLD = 5.0  # % eaten pre-injury to qualify as learner

_KINEMATIC_FEATURES = [
    ("max_extent_mm", "Max Extent (mm)"),
    ("peak_velocity_px_per_frame", "Peak Velocity (px/frame)"),
    ("trajectory_straightness", "Trajectory Straightness"),
    ("duration_frames", "Duration (frames)"),
    ("trajectory_smoothness", "Trajectory Smoothness"),
]

# Plausible kinematic ranges for filtering artifacts (Rule 30)
_KINEMATIC_PLAUSIBLE_RANGES = {
    "max_extent_mm": (0.5, 50.0),
    "peak_velocity_px_per_frame": (0.1, 100.0),
    "trajectory_straightness": (0.0, 1.0),
    "duration_frames": (3, 500),
    "trajectory_smoothness": (0.0, 100.0),
}


# ============================================================================
# Shared helper functions (migrated from plot_connectome_behavior.py)
# ============================================================================

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
    """Infer test phases from session dates, tray types, and surgery date.

    Timeline:
      - Pre-surgery Flat sessions = Training_Flat
      - Pre-surgery Pillar sessions = Training_Pillar (last 3 = Pre-Injury_Test)
      - Post-surgery sessions with >5-day gaps = Post-Injury Tests (1-4)
      - Post-surgery sessions with daily spacing = Rehab (E/F/P sub-phases)
    """
    if surgery_date is None:
        return ps_cohort

    ps = ps_cohort.copy()
    blank_mask = ps["test_phase"].isna() | (ps["test_phase"] == "")
    if not blank_mask.any():
        return ps

    blank = ps[blank_mask].copy()
    sessions = (
        blank.groupby(["session_date", "tray_type"]).size().reset_index(name="n")
    )
    sessions = sessions.sort_values("session_date")

    pre_sessions = sessions[sessions["session_date"] < surgery_date].copy()
    post_sessions = sessions[sessions["session_date"] > surgery_date].copy()

    phase_map = {}

    # Pre-surgery phases
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

    # Post-surgery phases
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
            if len(block) <= 2:
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
                        if tray == "E":
                            sub = "Easy"
                        elif tray == "F":
                            sub = "Flat"
                        else:
                            sub = "Pillar"
                        phase_map[(d, tray)] = f"Rehab_{sub}"

    for idx in ps[blank_mask].index:
        key = (ps.loc[idx, "session_date"], ps.loc[idx, "tray_type"])
        if key in phase_map:
            ps.loc[idx, "test_phase"] = phase_map[key]

    return ps


def _fix_mislabeled_rehab(ps_cohort, surgery_date):
    """Fix cohorts where rehab sessions are mislabeled as Post-Injury Test 4.

    CNT_02 has daily E/F/P sessions all labeled 'Post-Injury Test 4' that are
    actually the rehab block.  Detect and reclassify based on tray progression.
    """
    if surgery_date is None:
        return ps_cohort

    ps = ps_cohort.copy()
    post_mask = ps["test_phase"].str.contains("Post-Injury", case=False, na=False)
    if not post_mask.any():
        return ps

    post_data = ps[post_mask].copy()
    post_sessions = (
        post_data.groupby(["session_date", "tray_type"]).size().reset_index(name="n")
    )
    post_sessions = post_sessions.sort_values("session_date")
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
            if first_trays == {"P"} and len(block) > 1:
                rehab_dates = block[1:]
            else:
                rehab_dates = block
            for d in rehab_dates:
                day_trays = block_sessions[block_sessions["session_date"] == d]
                for _, row in day_trays.iterrows():
                    mask = (ps["session_date"] == d) & (
                        ps["tray_type"] == row["tray_type"]
                    )
                    tray = row["tray_type"]
                    if tray == "E":
                        ps.loc[mask, "test_phase"] = "Rehab_Easy"
                    elif tray == "F":
                        ps.loc[mask, "test_phase"] = "Rehab_Flat"
                    elif tray == "P":
                        ps.loc[mask, "test_phase"] = "Rehab_Pillar"

    return ps


def _classify_phase_to_window(phase):
    """Map a test_phase string to one of the 4 analysis windows."""
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
    if "training_pillar" in pl:
        return None
    return None


def _compute_cohort_data(ps_cohort):
    """Compute per-animal window averages and pellet-level DataFrame for a cohort.

    Returns
    -------
    window_data : dict
        {window_key: {eaten: array, contacted: array, animals: list}}
    pellet_df : DataFrame
        Columns [animal, window, eaten, contacted, animal_code].
    n_learners : int
    """
    ps = ps_cohort.copy()
    ps["window"] = ps["test_phase"].apply(_classify_phase_to_window)

    # Fall back to last 3 Training_Pillar if no explicit pre-injury
    if (ps["window"] == "pre_injury").sum() == 0:
        training_pillar = ps[
            ps["test_phase"].str.contains("Training_Pillar", case=False, na=False)
        ]
        if not training_pillar.empty:
            tp_dates = sorted(training_pillar["session_date"].unique())
            last3_dates = tp_dates[-3:] if len(tp_dates) >= 3 else tp_dates
            mask = ps["session_date"].isin(last3_dates) & ps[
                "test_phase"
            ].str.contains("Training_Pillar", case=False, na=False)
            ps.loc[mask, "window"] = "pre_injury"

    # Fall back to any Rehab if no explicit rehab_pillar
    if (ps["window"] == "rehab_pillar").sum() == 0:
        rehab_pillar = ps[
            ps["test_phase"].str.contains(
                "Rehab_Pillar|Rehab Pillar", case=False, na=False
            )
        ]
        if not rehab_pillar.empty:
            ps.loc[rehab_pillar.index, "window"] = "rehab_pillar"
        else:
            rehab_any = ps[
                ps["test_phase"].str.contains("Rehab", case=False, na=False)
            ]
            if not rehab_any.empty:
                rehab_dates = sorted(rehab_any["session_date"].unique())
                last_dates = (
                    rehab_dates[-4:] if len(rehab_dates) >= 4 else rehab_dates
                )
                mask = ps["session_date"].isin(last_dates) & ps[
                    "test_phase"
                ].str.contains("Rehab", case=False, na=False)
                ps.loc[mask, "window"] = "rehab_pillar"

    ps_windowed = ps[ps["window"].notna()].copy()

    if ps_windowed.empty:
        empty_wd = {
            wk: {"eaten": np.array([]), "contacted": np.array([]), "animals": []}
            for wk in _WINDOW_KEYS
        }
        return empty_wd, pd.DataFrame(), 0

    ps_windowed["eaten_bin"] = (ps_windowed["score"] >= 2).astype(int)
    ps_windowed["contacted_bin"] = (ps_windowed["score"] >= 1).astype(int)

    # Learner criterion
    pre_injury = ps_windowed[ps_windowed["window"] == "pre_injury"]
    if not pre_injury.empty:
        animal_pre = pre_injury.groupby("subject_id")["eaten_bin"].mean() * 100
        learners = set(
            animal_pre[animal_pre > _LEARNER_EATEN_THRESHOLD].index
        )
    else:
        learners = set(ps_windowed["subject_id"].unique())

    if not learners:
        learners = set(ps_windowed["subject_id"].unique())

    ps_learners = ps_windowed[ps_windowed["subject_id"].isin(learners)].copy()

    window_data = {}
    for wk in _WINDOW_KEYS:
        wk_data = ps_learners[ps_learners["window"] == wk]
        if wk_data.empty:
            window_data[wk] = {
                "eaten": np.array([]),
                "contacted": np.array([]),
                "animals": [],
            }
            continue
        animal_stats = wk_data.groupby("subject_id").agg(
            eaten_pct=("eaten_bin", lambda x: x.mean() * 100),
            contacted_pct=("contacted_bin", lambda x: x.mean() * 100),
        )
        window_data[wk] = {
            "eaten": animal_stats["eaten_pct"].values,
            "contacted": animal_stats["contacted_pct"].values,
            "animals": list(animal_stats.index),
        }

    # Pellet-level DataFrame for GEE
    window_to_int = {wk: i for i, wk in enumerate(_WINDOW_KEYS)}
    pellet_rows = []
    for _, row in ps_learners.iterrows():
        wk = row["window"]
        if wk not in window_to_int:
            continue
        pellet_rows.append(
            {
                "animal": row["subject_id"],
                "window": window_to_int[wk],
                "eaten": row["eaten_bin"],
                "contacted": row["contacted_bin"],
            }
        )

    pellet_df = pd.DataFrame(pellet_rows)
    if not pellet_df.empty:
        animal_codes = {
            a: i for i, a in enumerate(sorted(pellet_df["animal"].unique()))
        }
        pellet_df["animal_code"] = pellet_df["animal"].map(animal_codes)

    return window_data, pellet_df, len(learners)


def _load_behavior_csvs(cohorts=None):
    """Load pellet_scores, surgeries, and reach_data CSVs.

    Parameters
    ----------
    cohorts : list of str, optional
        Cohort IDs to filter to (e.g. ["CNT_01", "CNT_02"]).
        Defaults to all CNT_01 through CNT_04.

    Returns
    -------
    ps, surgeries, reach : DataFrames
    """
    cohorts = cohorts or _COHORTS
    pattern = "|".join(c + "_" for c in cohorts)

    dump_dir = MOUSEDB_ROOT / "database_dump"

    ps = pd.read_csv(dump_dir / "pellet_scores.csv", low_memory=False)
    surgeries = pd.read_csv(dump_dir / "surgeries.csv")
    reach = pd.read_csv(dump_dir / "reach_data.csv", low_memory=False)

    ps = ps[ps["subject_id"].str.match(pattern)].copy()
    surgeries = surgeries[surgeries["subject_id"].str.match(pattern)].copy()
    reach = reach[reach["subject_id"].str.match(pattern)].copy()

    ps["cohort"] = ps["subject_id"].str[:6]
    ps["session_date"] = pd.to_datetime(ps["session_date"])
    surgeries["surgery_date"] = pd.to_datetime(surgeries["surgery_date"])
    reach["session_date"] = pd.to_datetime(reach["session_date"])
    reach["cohort"] = reach["subject_id"].str[:6]

    return ps, surgeries, reach


def _prepare_cohort_pellet_data(ps, surgeries, cohorts=None):
    """Run phase inference + compute_cohort_data for each cohort.

    Returns
    -------
    all_cohort_data : dict
        {cohort_name: {"window_data": ..., "pellet_df": ..., "n_learners": int}}
    ps_full : DataFrame
        Full pellet_scores with inferred phases.
    """
    cohorts = cohorts or _COHORTS
    ps_all = []
    for cohort in cohorts:
        ps_cohort = ps[ps["cohort"] == cohort].copy()
        surgery_date = _get_surgery_date(surgeries, cohort)
        ps_cohort = _infer_phases(ps_cohort, surgery_date)
        ps_cohort = _fix_mislabeled_rehab(ps_cohort, surgery_date)
        ps_all.append(ps_cohort)

    ps_full = pd.concat(ps_all, ignore_index=True)

    all_cohort_data = {}
    for cohort in cohorts:
        ps_cohort = ps_full[ps_full["cohort"] == cohort]
        window_data, pellet_df, n_learners = _compute_cohort_data(ps_cohort)
        if n_learners > 0:
            all_cohort_data[cohort] = {
                "window_data": window_data,
                "pellet_df": pellet_df,
                "n_learners": n_learners,
            }

    return all_cohort_data, ps_full


def _assign_reach_phase(reach_df, surgeries, cohorts=None):
    """Assign experimental phase to each reach based on session date vs surgery date."""
    cohorts = cohorts or _COHORTS
    reach = reach_df.copy()
    reach["phase"] = None

    for cohort in cohorts:
        surg_date = _get_surgery_date(surgeries, cohort)
        if surg_date is None:
            continue

        mask = reach["cohort"] == cohort
        if not mask.any():
            continue

        pre_mask = mask & (reach["session_date"] < surg_date)
        post_mask = mask & (
            reach["session_date"] >= surg_date + pd.Timedelta(days=7)
        )

        reach.loc[pre_mask, "phase"] = "Pre-Injury"

        post_dates = sorted(reach[post_mask]["session_date"].unique())
        if post_dates:
            blocks = [[post_dates[0]]]
            for i in range(1, len(post_dates)):
                gap = (post_dates[i] - post_dates[i - 1]).days
                if gap > 5:
                    blocks.append([])
                blocks[-1].append(post_dates[i])

            for block in blocks:
                block_mask = mask & reach["session_date"].isin(block)
                if len(block) <= 2:
                    reach.loc[block_mask, "phase"] = "Post-Injury"
                else:
                    reach.loc[block_mask, "phase"] = "Rehab"

    return reach


def _filter_kinematic_plausible(reach_df, features=None):
    """Filter reaches to plausible kinematic ranges (Rule 30).

    Returns
    -------
    filtered : DataFrame
    n_excluded : int
    exclusion_details : dict
        {feature: n_excluded}
    """
    features = features or list(_KINEMATIC_PLAUSIBLE_RANGES.keys())
    df = reach_df.copy()
    original_n = len(df)
    details = {}

    for feat in features:
        if feat not in df.columns or feat not in _KINEMATIC_PLAUSIBLE_RANGES:
            continue
        lo, hi = _KINEMATIC_PLAUSIBLE_RANGES[feat]
        before = len(df)
        df = df[(df[feat].isna()) | ((df[feat] >= lo) & (df[feat] <= hi))]
        details[feat] = before - len(df)

    return df, original_n - len(df), details


def _holm_correct(pvals):
    """Apply Holm-Bonferroni step-down correction to a dict of p-values."""
    if not pvals:
        return pvals
    sorted_pairs = sorted(pvals.keys(), key=lambda k: pvals[k])
    m = len(sorted_pairs)
    corrected = {}
    for rank, pair in enumerate(sorted_pairs):
        corrected[pair] = min(1.0, pvals[pair] * (m - rank))
    prev_p = 0
    for pair in sorted_pairs:
        corrected[pair] = max(corrected[pair], prev_p)
        prev_p = corrected[pair]
    return corrected


def _run_gee_or_fallback(pellet_df, metric):
    """Run GEE (binomial, exchangeable) with Holm correction, or chi-sq fallback.

    Returns
    -------
    pvals : dict
        {(window_i, window_j): corrected_p}
    omnibus_p : float
    n_per_window : dict
    method : str
    """
    comparisons = [(0, 1), (0, 2), (1, 3), (2, 3)]

    n_per_window = {}
    if not pellet_df.empty:
        for w in range(4):
            n_per_window[w] = int((pellet_df["window"] == w).sum())
    else:
        n_per_window = {w: 0 for w in range(4)}

    if pellet_df.empty or pellet_df["window"].nunique() < 2:
        return {}, 1.0, n_per_window, "N/A"

    available = sorted([w for w in range(4) if n_per_window.get(w, 0) > 0])
    if len(available) < 2:
        return {}, 1.0, n_per_window, "N/A"

    df_gee = pellet_df[pellet_df["window"].isin(available)].copy()

    # Check if GEE is viable
    use_gee = (
        _HAS_GEE
        and "animal_code" in df_gee.columns
        and df_gee["animal_code"].nunique() >= 2
    )

    if use_gee:
        for w in available:
            subset = df_gee[df_gee["window"] == w][metric]
            if len(subset) > 0 and (
                subset.sum() == 0 or subset.sum() == len(subset)
            ):
                use_gee = False
                break

    if not use_gee:
        return _chi_sq_fallback(pellet_df, metric, comparisons, n_per_window)

    try:
        ref_window = available[0]
        df_gee = df_gee.sort_values("animal_code").reset_index(drop=True)

        formula = f"{metric} ~ C(window, Treatment({ref_window}))"
        model = GEE.from_formula(
            formula,
            groups="animal_code",
            data=df_gee,
            family=Binomial(),
            cov_struct=Exchangeable(),
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result = model.fit(maxiter=100)

        param_names = list(result.params.index)
        window_params = [p for p in param_names if "C(window" in p]

        if len(window_params) >= 1:
            idx = [param_names.index(p) for p in window_params]
            beta_w = result.params.values[idx]
            vcov_w = result.cov_params().values[np.ix_(idx, idx)]
            try:
                wald_stat = float(beta_w @ np.linalg.inv(vcov_w) @ beta_w)
                omnibus_p = float(
                    1 - stats.chi2.cdf(wald_stat, len(window_params))
                )
            except np.linalg.LinAlgError:
                omnibus_p = 1.0
        else:
            omnibus_p = 1.0

        params = result.params.values
        vcov = result.cov_params().values

        raw_pvals = {}
        for i, j in comparisons:
            if i not in available or j not in available:
                continue
            L = np.zeros(len(params))
            if i == ref_window:
                j_names = [p for p in param_names if f"T.{j}]" in p]
                if not j_names:
                    continue
                L[param_names.index(j_names[0])] = 1.0
            elif j == ref_window:
                i_names = [p for p in param_names if f"T.{i}]" in p]
                if not i_names:
                    continue
                L[param_names.index(i_names[0])] = -1.0
            else:
                i_names = [p for p in param_names if f"T.{i}]" in p]
                j_names = [p for p in param_names if f"T.{j}]" in p]
                if not i_names or not j_names:
                    continue
                L[param_names.index(j_names[0])] = 1.0
                L[param_names.index(i_names[0])] = -1.0

            est = float(L @ params)
            var_est = float(L @ vcov @ L)
            if var_est <= 0:
                continue
            z = est / np.sqrt(var_est)
            p = float(2 * (1 - stats.norm.cdf(abs(z))))
            raw_pvals[(i, j)] = p

        raw_pvals = _holm_correct(raw_pvals)
        return raw_pvals, omnibus_p, n_per_window, "GEE"

    except Exception:
        return _chi_sq_fallback(pellet_df, metric, comparisons, n_per_window)


def _chi_sq_fallback(df, metric, comparisons, n_per_window):
    """Chi-square / Fisher exact fallback with Holm correction."""
    raw_pvals = {}
    for i, j in comparisons:
        di = df[df["window"] == i]
        dj = df[df["window"] == j]
        if len(di) == 0 or len(dj) == 0:
            continue
        a = int(di[metric].sum())
        b = len(di) - a
        c = int(dj[metric].sum())
        d_val = len(dj) - c
        table = np.array([[a, b], [c, d_val]])
        if table.min() < 5:
            _, p = stats.fisher_exact(table)
        else:
            _, p, _, _ = stats.chi2_contingency(table, correction=True)
        raw_pvals[(i, j)] = p

    raw_pvals = _holm_correct(raw_pvals)

    available = [w for w in range(4) if n_per_window.get(w, 0) > 0]
    if len(available) >= 2:
        table_rows = []
        for w in available:
            subset = df[df["window"] == w]
            table_rows.append(
                [int(subset[metric].sum()), len(subset) - int(subset[metric].sum())]
            )
        table = np.array(table_rows)
        if table.sum() > 0:
            _, omnibus_p, _, _ = stats.chi2_contingency(table)
        else:
            omnibus_p = 1.0
    else:
        omnibus_p = 1.0

    return raw_pvals, omnibus_p, n_per_window, "chi-sq/Fisher"


# ============================================================================
# Recipe: CohortBehaviorSummary
# ============================================================================

class CohortBehaviorSummary(FigureRecipe):
    """Per-cohort box/violin plot of retrieval rates by experimental phase.

    Replaces the old bar+SEM chart with box plots (Rule 19), per-subject
    colored dots with connected lines (Rules 6, 18, 20), and GEE post-hoc
    statistics with Holm correction and Cohen's d (Rules 26, 34).
    """

    name = "cohort_behavior_summary"
    title = "Pellet Retrieval by Phase (Per Cohort)"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 11)

    def __init__(self, cohort="CNT_01"):
        self.cohort = cohort

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort": self.cohort,
            "learner_threshold": _LEARNER_EATEN_THRESHOLD,
            "window_keys": _WINDOW_KEYS,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        ps, surgeries, _reach = _load_behavior_csvs(cohorts=[self.cohort])
        all_cd, ps_full = _prepare_cohort_pellet_data(
            ps, surgeries, cohorts=[self.cohort]
        )
        if self.cohort not in all_cd:
            print(
                f"  No learners found for {self.cohort}, returning empty",
                flush=True,
            )
            return {
                "window_data": {
                    wk: {"eaten": np.array([]), "contacted": np.array([]), "animals": []}
                    for wk in _WINDOW_KEYS
                },
                "pellet_df": pd.DataFrame(),
                "n_learners": 0,
                "cohort": self.cohort,
            }

        cd = all_cd[self.cohort]
        return {
            "window_data": cd["window_data"],
            "pellet_df": cd["pellet_df"],
            "n_learners": cd["n_learners"],
            "cohort": self.cohort,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        pellet_df = data["pellet_df"]
        window_data = data["window_data"]

        stat_results = {}
        for metric_key in ("eaten", "contacted"):
            pvals, omnibus_p, n_pellets, method = _run_gee_or_fallback(
                pellet_df, metric_key
            )
            # Compute Cohen's d for each pairwise comparison
            effect_sizes = {}
            for (i, j), p in pvals.items():
                wi = _WINDOW_KEYS[i]
                wj = _WINDOW_KEYS[j]
                arr_i = window_data[wi][metric_key]
                arr_j = window_data[wj][metric_key]
                if len(arr_i) >= 2 and len(arr_j) >= 2:
                    effect_sizes[(i, j)] = cohens_d(arr_i, arr_j)

            stat_results[metric_key] = {
                "pvals": pvals,
                "omnibus_p": omnibus_p,
                "n_pellets": n_pellets,
                "method": method,
                "effect_sizes": effect_sizes,
            }

        return {"stat_results": stat_results}

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        window_data = data["window_data"]
        n_learners = data["n_learners"]
        stat_results = results["stat_results"]

        # Use "eaten" (retrieved) as the primary metric
        metric_key = "eaten"
        sr = stat_results[metric_key]

        data_by_phase = [window_data[wk][metric_key] for wk in _WINDOW_KEYS]

        # Box plots (Rule 19)
        box_data = [d if len(d) > 0 else [np.nan] for d in data_by_phase]
        bp = ax.boxplot(
            box_data,
            patch_artist=True,
            widths=0.5,
            medianprops=dict(color="black", linewidth=2),
            flierprops=dict(marker=".", markersize=2, alpha=0.3),
        )
        from mousedb.figures.palettes import PELLET_PHASE_COLORS as _ppc

        phase_colors_list = [
            _ppc.get(PELLET_ORDER[i], "#888888") if i < len(PELLET_ORDER) else "#888888"
            for i in range(len(_WINDOW_KEYS))
        ]
        for patch, color in zip(bp["boxes"], phase_colors_list):
            patch.set_facecolor(color)
            patch.set_alpha(0.4)

        # Per-subject colored dots + connected lines (Rules 6, 18, 20)
        all_animals = set()
        for wk in _WINDOW_KEYS:
            all_animals.update(window_data[wk]["animals"])
        all_animals = sorted(all_animals)
        subject_colors = get_persistent_subject_colors(all_animals)

        animal_to_window_val = {}
        for wk_idx, wk in enumerate(_WINDOW_KEYS):
            for a_idx, a in enumerate(window_data[wk]["animals"]):
                if a not in animal_to_window_val:
                    animal_to_window_val[a] = {}
                animal_to_window_val[a][wk_idx] = window_data[wk][metric_key][a_idx]

        for animal in all_animals:
            color = subject_colors[animal]
            vals = animal_to_window_val.get(animal, {})
            xs = sorted(vals.keys())
            ys = [vals[x] for x in xs]
            # Box positions are 1-indexed
            xs_plot = [x + 1 for x in xs]
            if len(xs_plot) >= 2:
                ax.plot(
                    xs_plot, ys, color=color, alpha=0.5, linewidth=0.8, zorder=3
                )
            ax.scatter(
                xs_plot,
                ys,
                color=color,
                s=30,
                zorder=4,
                alpha=0.8,
                edgecolors="white",
                linewidths=0.3,
            )

        # Significance brackets
        bracket_order = [(0, 1), (2, 3), (0, 2), (1, 3)]
        all_vals = [v for d in data_by_phase for v in d]
        data_ceil = max(all_vals) if all_vals else 1
        data_ceil = max(data_ceil, 5)
        bracket_y = data_ceil + 5
        bracket_step = 5.0
        bracket_idx = 0
        for i, j in bracket_order:
            if (i, j) in sr["pvals"]:
                y_pos = bracket_y + bracket_idx * bracket_step
                # Box positions are 1-indexed
                add_stat_bracket(ax, i + 1, j + 1, y_pos, sr["pvals"][(i, j)])
                # Add Cohen's d annotation
                if (i, j) in sr["effect_sizes"]:
                    d_val = sr["effect_sizes"][(i, j)]
                    if not (d_val != d_val):  # not NaN
                        ax.text(
                            (i + j + 2) / 2,
                            y_pos - 1.5,
                            f"d={d_val:.2f}",
                            ha="center",
                            va="top",
                            fontsize=7,
                            color="#555555",
                        )
                bracket_idx += 1

        if bracket_idx > 0:
            ax.set_ylim(-2, bracket_y + bracket_idx * bracket_step + 5)
        else:
            ax.set_ylim(-2, data_ceil * 1.2)

        # Axis labels
        xlabels = []
        for i, wl in enumerate(_WINDOW_LABELS):
            n_a = len(data_by_phase[i])
            n_p = sr["n_pellets"].get(i, 0)
            xlabels.append(f"{wl}\n({n_a} mice, {n_p} pel.)")
        ax.set_xticks(range(1, len(_WINDOW_KEYS) + 1))
        ax.set_xticklabels(xlabels, fontsize=9)
        ax.set_ylabel("% Pellets Retrieved", fontsize=13)

        omnibus_str = (
            f"p={sr['omnibus_p']:.4f}"
            if sr["omnibus_p"] >= 0.0001
            else "p<0.0001"
        )
        ax.set_title(
            f"Pellet Retrieval by Phase: {self.cohort} "
            f"(N={n_learners} learners)\n"
            f"{sr['method']} + Holm | omnibus {omnibus_str}",
            fontsize=13,
            fontweight="bold",
            pad=12,
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15, linewidth=0.5)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        n = data["n_learners"]
        sr = results["stat_results"]["eaten"]
        stat_lines = []
        for (i, j), p in sr["pvals"].items():
            d = sr["effect_sizes"].get((i, j), float("nan"))
            d_str = f"d={d:.2f}" if not np.isnan(d) else "d=N/A"
            p_str = f"p={p:.4f}" if p >= 0.0001 else "p<0.0001"
            stat_lines.append(
                f"  {_WINDOW_KEYS[i]} vs {_WINDOW_KEYS[j]}: {p_str}, {d_str}"
            )
        stat_text = "\n".join(stat_lines) if stat_lines else "  (no tests run)"

        return (
            f"EXPERIMENT  Skilled reaching, single-pellet retrieval (pillar tray), "
            f"CST lesion injury model\n"
            f"SUBJECTS    N={n} learners from {self.cohort} "
            f"(>{_LEARNER_EATEN_THRESHOLD}% eaten pre-injury)\n"
            f"METRIC      % pellets scored 'eaten' (score=2) per subject per phase\n"
            f"DISPLAY     Box plots (quartiles) with individual subject dots "
            f"(unique color per subject) connected across phases\n"
            f"STATS       {sr['method']} (binomial, exchangeable, animal cluster) "
            f"+ Holm correction. Cohen's d (independent) for each comparison.\n"
            f"RESULTS\n{stat_text}\n"
            f"STAT JUSTIFICATION  {stat_justification('gee_binomial')}"
        )

    def figure_legend(self, data, results):
        n = data["n_learners"]
        sr = results["stat_results"]["eaten"]
        es_parts = []
        for (i, j), d in sr["effect_sizes"].items():
            if not np.isnan(d):
                es_parts.append(
                    f"{_WINDOW_KEYS[i]} vs {_WINDOW_KEYS[j]}: d={d:.2f}"
                )
        es_text = "; ".join(es_parts) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                f"Does pellet retrieval change across experimental phases "
                f"in {self.cohort}?"
            ),
            method=(
                f"N={n} learners (>{_LEARNER_EATEN_THRESHOLD}% eaten pre-injury) "
                f"from {self.cohort}. Pillar tray sessions only. "
                f"Box plots show quartiles; dots are individual subjects "
                f"(unique persistent colors) connected across phases."
            ),
            finding=(
                "Retrieval drops acutely post-injury and shows partial "
                "recovery during rehabilitation in most subjects."
            ),
            analysis=(
                f"{stat_justification('gee_binomial')} "
                f"Four pairwise comparisons with Holm correction."
            ),
            effect_sizes=es_text,
            confounds=(
                "Phase definitions inferred from surgery date for cohorts "
                "without explicit annotations. Tray familiarity not controlled. "
                "Hand preference and lesion size may vary."
            ),
            follow_up=(
                "Do kinematic profiles of successful reaches also shift, "
                "or is recovery limited to success rate?"
            ),
        )


# ============================================================================
# Recipe: CohortRecoveryTrajectory
# ============================================================================

class CohortRecoveryTrajectory(FigureRecipe):
    """Per-animal recovery trajectories with outcome categorization.

    Individual subjects shown as colored lines (persistent subject colors,
    Rules 6, 18, 20) with recovery classification: recovered (>80% of
    pre-injury), improved (above nadir), or no improvement.
    """

    name = "cohort_recovery_trajectory"
    title = "Per-Animal Recovery Trajectories"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 10)

    def __init__(self, cohort="CNT_01"):
        self.cohort = cohort

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohort": self.cohort,
            "learner_threshold": _LEARNER_EATEN_THRESHOLD,
            "recovery_threshold": 0.8,
        }

    def load_data(self) -> Dict[str, Any]:
        ps, surgeries, _ = _load_behavior_csvs(cohorts=[self.cohort])
        all_cd, _ = _prepare_cohort_pellet_data(
            ps, surgeries, cohorts=[self.cohort]
        )
        if self.cohort not in all_cd:
            return {"window_data": None, "n_learners": 0, "cohort": self.cohort}
        cd = all_cd[self.cohort]
        return {
            "window_data": cd["window_data"],
            "n_learners": cd["n_learners"],
            "cohort": self.cohort,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        wd = data["window_data"]
        if wd is None:
            return {"paired_animals": [], "classifications": {}, "stats": {}}

        pre = wd["pre_injury"]
        rehab = wd["rehab_pillar"]
        p1 = wd["post_1"]
        p24 = wd["post_2_4"]

        pre_idx = {a: i for i, a in enumerate(pre["animals"])}
        rehab_idx = {a: i for i, a in enumerate(rehab["animals"])}
        p1_idx = {a: i for i, a in enumerate(p1["animals"])}
        p24_idx = {a: i for i, a in enumerate(p24["animals"])}

        paired = sorted(set(pre["animals"]) & set(rehab["animals"]))

        classifications = {}
        for animal in paired:
            pre_val = pre["eaten"][pre_idx[animal]]
            rehab_val = rehab["eaten"][rehab_idx[animal]]
            post_vals = []
            if animal in p1_idx:
                post_vals.append(p1["eaten"][p1_idx[animal]])
            if animal in p24_idx:
                post_vals.append(p24["eaten"][p24_idx[animal]])
            nadir = min(post_vals) if post_vals else rehab_val

            if rehab_val > pre_val * 0.8:
                classifications[animal] = "recovered"
            elif rehab_val > nadir:
                classifications[animal] = "improved"
            else:
                classifications[animal] = "no_improvement"

        # Paired stats
        stat_info = {}
        if len(paired) >= 5:
            pre_arr = np.array(
                [pre["eaten"][pre_idx[a]] for a in paired]
            )
            rehab_arr = np.array(
                [rehab["eaten"][rehab_idx[a]] for a in paired]
            )
            try:
                t_stat, p_val = stats.ttest_rel(pre_arr, rehab_arr)
                d = cohens_d_paired(pre_arr, rehab_arr)
                diff = rehab_arr - pre_arr
                stat_info = {
                    "t_stat": t_stat,
                    "p_val": p_val,
                    "d": d,
                    "mean_diff": float(np.mean(diff)),
                    "sem_diff": float(stats.sem(diff)),
                    "n": len(paired),
                }
            except Exception:
                pass

        n_recovered = sum(1 for c in classifications.values() if c == "recovered")
        n_improved = sum(1 for c in classifications.values() if c == "improved")
        n_none = sum(1 for c in classifications.values() if c == "no_improvement")

        return {
            "paired_animals": paired,
            "classifications": classifications,
            "stats": stat_info,
            "n_recovered": n_recovered,
            "n_improved": n_improved,
            "n_none": n_none,
        }

    def plot(self, data, results, fig, ax, theme):
        wd = data["window_data"]
        if wd is None:
            ax.text(
                0.5, 0.5, "No data available",
                transform=ax.transAxes, ha="center", va="center",
            )
            return

        paired = results["paired_animals"]
        classifications = results["classifications"]
        if len(paired) < 2:
            ax.text(
                0.5, 0.5, f"Insufficient paired data (N={len(paired)})",
                transform=ax.transAxes, ha="center", va="center",
            )
            return

        pre = wd["pre_injury"]
        p1 = wd["post_1"]
        p24 = wd["post_2_4"]
        rehab = wd["rehab_pillar"]

        pre_idx = {a: i for i, a in enumerate(pre["animals"])}
        p1_idx = {a: i for i, a in enumerate(p1["animals"])}
        p24_idx = {a: i for i, a in enumerate(p24["animals"])}
        rehab_idx = {a: i for i, a in enumerate(rehab["animals"])}

        subject_colors = get_persistent_subject_colors(paired)
        mean_vals = {0: [], 1: [], 2: [], 3: []}

        for animal in paired:
            color = subject_colors[animal]
            label = get_subject_label(animal)
            cls = classifications.get(animal, "no_improvement")
            linestyle = "-" if cls == "recovered" else ("--" if cls == "improved" else ":")

            pre_val = pre["eaten"][pre_idx[animal]]
            rehab_val = rehab["eaten"][rehab_idx[animal]]

            xs, ys = [0], [pre_val]
            mean_vals[0].append(pre_val)
            if animal in p1_idx:
                xs.append(1)
                ys.append(p1["eaten"][p1_idx[animal]])
                mean_vals[1].append(p1["eaten"][p1_idx[animal]])
            if animal in p24_idx:
                xs.append(2)
                ys.append(p24["eaten"][p24_idx[animal]])
                mean_vals[2].append(p24["eaten"][p24_idx[animal]])
            xs.append(3)
            ys.append(rehab_val)
            mean_vals[3].append(rehab_val)

            ax.plot(
                xs, ys, linestyle, color=color, alpha=0.7,
                linewidth=1.3, zorder=3, label=label,
            )
            ax.scatter(
                xs, ys, color=color, s=40, zorder=4,
                edgecolors="white", linewidths=0.5, alpha=0.9,
            )

        # Group mean
        mean_x, mean_y = [], []
        for xi in [0, 1, 2, 3]:
            if mean_vals[xi]:
                mean_x.append(xi)
                mean_y.append(np.mean(mean_vals[xi]))
        ax.plot(
            mean_x, mean_y, "s-", color="black",
            markersize=10, linewidth=3, zorder=5,
            label="Group Mean",
        )

        ax.set_xticks([0, 1, 2, 3])
        ax.set_xticklabels(
            ["Pre-Injury\n(Last 3)", "1 Wk\nPost-Injury",
             "2-4 Wk\nPost-Injury", "Rehab\nPillar"],
            fontsize=10,
        )
        ax.set_ylabel("% Pellets Retrieved", fontsize=13)
        ax.set_xlim(-0.3, 3.3)

        n = len(paired)
        ax.set_title(
            f"Per-Animal Recovery: {self.cohort} (N={n})\n"
            f"Recovered={results['n_recovered']}, "
            f"Improved={results['n_improved']}, "
            f"No Improvement={results['n_none']}",
            fontsize=13, fontweight="bold", pad=12,
        )

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15, linewidth=0.5)

        # Legend
        handles, labels_list = ax.get_legend_handles_labels()
        if handles:
            ax.legend(
                fontsize=7, loc="upper right", framealpha=0.9,
                ncol=2, title="Subjects", title_fontsize=8,
            )

    def methodology_text(self, data, results):
        n = len(results["paired_animals"])
        si = results.get("stats", {})
        if si:
            p_str = f"p={si['p_val']:.4f}" if si["p_val"] >= 0.0001 else "p<0.0001"
            d_str = f"d={si['d']:.2f}" if not (si["d"] != si["d"]) else "d=N/A"
            stat_line = (
                f"  Pre vs Rehab (paired t-test): {p_str}, {d_str}, "
                f"mean diff={si['mean_diff']:+.1f}% +/- {si['sem_diff']:.1f}%"
            )
        else:
            stat_line = "  Insufficient data for paired test"

        return (
            f"SUBJECTS    N={n} learners with both pre-injury and rehab data, "
            f"{self.cohort}\n"
            f"METRIC      % pellets eaten per subject per phase (Pillar only)\n"
            f"RECOVERY    Recovered = rehab >= 80% of pre-injury; "
            f"Improved = rehab > post-injury nadir; "
            f"No improvement = rehab <= nadir\n"
            f"DISPLAY     Individual subject lines (persistent unique colors), "
            f"solid=recovered, dashed=improved, dotted=no improvement\n"
            f"STATS\n{stat_line}\n"
            f"STAT JUSTIFICATION  {stat_justification('paired_ttest')}"
        )

    def figure_legend(self, data, results):
        n = len(results["paired_animals"])
        si = results.get("stats", {})
        d = si.get("d", float("nan"))
        d_str = f"d={d:.2f}" if not np.isnan(d) else "Not computed"

        return FigureLegend(
            question=(
                f"Do individual animals in {self.cohort} recover pellet "
                f"retrieval after CST injury?"
            ),
            method=(
                f"N={n} learners with pre-injury and rehab data. "
                f"Individual trajectories colored by persistent subject ID. "
                f"Recovery = rehab >= 80% of pre-injury baseline."
            ),
            finding=(
                f"{results['n_recovered']}/{n} recovered, "
                f"{results['n_improved']}/{n} improved from nadir, "
                f"{results['n_none']}/{n} showed no improvement."
            ),
            analysis=(
                f"{stat_justification('paired_ttest')} "
                f"Paired t-test on pre vs rehab retrieval rates."
            ),
            effect_sizes=d_str,
            confounds=(
                "Recovery threshold (80%) is arbitrary. Animals with low "
                "baselines may appear recovered due to floor effects. "
                "Missing post-injury timepoints shown as dashed connections."
            ),
            follow_up=(
                "Does recovery correlate with lesion size or "
                "pre-injury skill level?"
            ),
        )


# ============================================================================
# Recipe: RecoveryWaterfall
# ============================================================================

class RecoveryWaterfall(FigureRecipe):
    """Waterfall chart of recovery magnitude (rehab - post-injury nadir).

    Each bar represents one animal, sorted by recovery magnitude, colored
    by persistent subject color (Rule 6). Positive bars = improvement
    from nadir, negative = further decline.
    """

    name = "recovery_waterfall"
    title = "Recovery Waterfall (Rehab vs Post-Injury Nadir)"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (14, 10)

    def __init__(self, cohort="CNT_01"):
        self.cohort = cohort

    def get_parameters(self) -> Dict[str, Any]:
        return {"cohort": self.cohort, "learner_threshold": _LEARNER_EATEN_THRESHOLD}

    def load_data(self) -> Dict[str, Any]:
        ps, surgeries, _ = _load_behavior_csvs(cohorts=[self.cohort])
        all_cd, _ = _prepare_cohort_pellet_data(
            ps, surgeries, cohorts=[self.cohort]
        )
        if self.cohort not in all_cd:
            return {"window_data": None, "n_learners": 0}
        cd = all_cd[self.cohort]
        return {
            "window_data": cd["window_data"],
            "n_learners": cd["n_learners"],
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        wd = data["window_data"]
        if wd is None:
            return {"animals": [], "deltas": [], "stats": {}}

        rehab = wd["rehab_pillar"]
        p1 = wd["post_1"]
        p24 = wd["post_2_4"]

        rehab_idx = {a: i for i, a in enumerate(rehab["animals"])}
        p1_idx = {a: i for i, a in enumerate(p1["animals"])}
        p24_idx = {a: i for i, a in enumerate(p24["animals"])}

        wf_animals_set = set(rehab["animals"]) & (
            set(p1["animals"]) | set(p24["animals"])
        )
        wf_animals = sorted(wf_animals_set)

        animals = []
        deltas = []
        for animal in wf_animals:
            rehab_val = rehab["eaten"][rehab_idx[animal]]
            post_vals = []
            if animal in p1_idx:
                post_vals.append(p1["eaten"][p1_idx[animal]])
            if animal in p24_idx:
                post_vals.append(p24["eaten"][p24_idx[animal]])
            if not post_vals:
                continue
            nadir = min(post_vals)
            animals.append(animal)
            deltas.append(rehab_val - nadir)

        # Sort descending
        if deltas:
            sorted_idx = np.argsort(deltas)[::-1]
            animals = [animals[i] for i in sorted_idx]
            deltas = [deltas[i] for i in sorted_idx]

        # Wilcoxon on deltas
        stat_info = {}
        non_zero = [d for d in deltas if d != 0]
        if len(non_zero) >= 5:
            try:
                wil_stat, wil_p = stats.wilcoxon(non_zero)
                stat_info = {"stat": wil_stat, "p": wil_p, "n": len(deltas)}
            except Exception:
                pass

        n_improved = sum(1 for d in deltas if d > 0)
        n_declined = sum(1 for d in deltas if d < 0)
        mean_delta = float(np.mean(deltas)) if deltas else 0.0

        return {
            "animals": animals,
            "deltas": deltas,
            "stats": stat_info,
            "n_improved": n_improved,
            "n_declined": n_declined,
            "mean_delta": mean_delta,
        }

    def plot(self, data, results, fig, ax, theme):
        animals = results["animals"]
        deltas = results["deltas"]

        if not deltas:
            ax.text(
                0.5, 0.5, "Insufficient data",
                transform=ax.transAxes, ha="center", va="center",
            )
            return

        # Per-subject colors (Rule 6)
        subject_colors = get_persistent_subject_colors(animals)
        bar_colors = [subject_colors[a] for a in animals]

        x_pos = np.arange(len(deltas))
        ax.bar(
            x_pos, deltas, color=bar_colors, alpha=0.85,
            edgecolor="white", linewidth=0.5,
        )
        ax.axhline(y=0, color="black", linewidth=1, linestyle="-")

        # Animal ID labels on bars
        for i, (animal, delta) in enumerate(zip(animals, deltas)):
            label = get_subject_label(animal)
            y_offset = 1 if delta >= 0 else -1
            va = "bottom" if delta >= 0 else "top"
            ax.text(
                i, delta + y_offset, label, ha="center", va=va,
                fontsize=6, rotation=90, color="#333333",
            )

        # Stats annotation
        si = results["stats"]
        n_total = len(deltas)
        mean_d = results["mean_delta"]
        lines = [
            f"N={n_total} animals",
            f"Improved: {results['n_improved']} | Declined: {results['n_declined']}",
            f"Mean delta: {mean_d:+.1f}%",
        ]
        if si:
            p_str = f"p={si['p']:.4f}" if si["p"] >= 0.0001 else "p<0.0001"
            lines.append(f"Wilcoxon: {p_str}")
        ax.text(
            0.98, 0.98, "\n".join(lines),
            transform=ax.transAxes, fontsize=9,
            va="top", ha="right",
            bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
            family="monospace",
        )

        ax.set_xlabel("Animals (sorted by recovery magnitude)", fontsize=11)
        ax.set_ylabel(
            "Change in % Retrieved\n(Rehab - Post-Injury Nadir)", fontsize=11
        )
        ax.set_title(
            f"Recovery Waterfall: {self.cohort}",
            fontsize=14, fontweight="bold", pad=12,
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xticks([])

    def methodology_text(self, data, results):
        n = len(results["deltas"])
        si = results["stats"]
        if si:
            p_str = f"p={si['p']:.4f}" if si["p"] >= 0.0001 else "p<0.0001"
            stat_line = f"  Wilcoxon signed-rank (non-zero deltas): {p_str}"
        else:
            stat_line = "  Insufficient non-zero deltas for Wilcoxon"
        return (
            f"SUBJECTS    N={n} animals from {self.cohort} with rehab + "
            f"post-injury data\n"
            f"METRIC      Delta = Rehab eaten% - Post-injury nadir eaten%\n"
            f"NADIR       Minimum of Post-1wk and Post-2-4wk eaten%\n"
            f"DISPLAY     Bars sorted by delta magnitude, colored by "
            f"persistent subject color. Positive = improvement from nadir.\n"
            f"STATS\n{stat_line}\n"
            f"STAT JUSTIFICATION  {stat_justification('wilcoxon')}"
        )

    def figure_legend(self, data, results):
        n = len(results["deltas"])
        si = results["stats"]
        d_text = "Not applicable (non-parametric)"
        return FigureLegend(
            question=(
                f"How much does each {self.cohort} animal recover from "
                f"its post-injury nadir?"
            ),
            method=(
                f"N={n} animals. Delta = rehab eaten% minus post-injury "
                f"nadir eaten% (minimum of 1wk and 2-4wk). "
                f"Bars sorted by magnitude, colored per subject."
            ),
            finding=(
                f"{results['n_improved']}/{n} animals improved from nadir, "
                f"{results['n_declined']}/{n} declined further. "
                f"Mean delta: {results['mean_delta']:+.1f}%."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                f"Wilcoxon signed-rank on non-zero deltas."
            ),
            effect_sizes=d_text,
            confounds=(
                "Nadir may underestimate true deficit if post-injury "
                "sessions are few. Animals with very low nadirs have "
                "more room for apparent recovery."
            ),
            follow_up=(
                "Does waterfall rank predict kinematic recovery or "
                "correlate with lesion characteristics?"
            ),
        )


# ============================================================================
# Recipe: MegaCohortPooled
# ============================================================================

class MegaCohortPooled(FigureRecipe):
    """Pooled analysis across all cohorts with baseline normalization.

    Multi-panel figure: (1) all animals pooled trajectory, (2) recovery
    waterfall colored by cohort, (3) cohort mean trajectories, (4) box
    plots of post-rehab recovery by cohort.

    Uses per-subject colors (Rule 6), box plots (Rule 19), connected
    individual traces (Rule 20), and baseline normalization with floor
    (Rule 28).
    """

    name = "mega_cohort_pooled"
    title = "Mega-Cohort Pooled Recovery Analysis"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/pellet_scores.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (18, 14)

    def __init__(self, cohorts=None, baseline_floor=5.0):
        self.cohorts = cohorts or _COHORTS
        self.baseline_floor = baseline_floor

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "baseline_floor": self.baseline_floor,
            "learner_threshold": _LEARNER_EATEN_THRESHOLD,
        }

    def create_axes(self, fig, plot_gs):
        inner_gs = plot_gs.subgridspec(2, 2, hspace=0.35, wspace=0.3)
        return {
            "pooled": fig.add_subplot(inner_gs[0, 0]),
            "waterfall": fig.add_subplot(inner_gs[0, 1]),
            "cohort_means": fig.add_subplot(inner_gs[1, 0]),
            "cohort_box": fig.add_subplot(inner_gs[1, 1]),
        }

    def load_data(self) -> Dict[str, Any]:
        ps, surgeries, _ = _load_behavior_csvs(cohorts=self.cohorts)
        all_cd, _ = _prepare_cohort_pellet_data(
            ps, surgeries, cohorts=self.cohorts
        )
        return {"all_cohort_data": all_cd}

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        all_cd = data["all_cohort_data"]

        records = []
        for cohort_name, cd in all_cd.items():
            wd = cd["window_data"]
            pre = wd["pre_injury"]
            p1 = wd["post_1"]
            p24 = wd["post_2_4"]
            rehab = wd["rehab_pillar"]

            pre_idx = {a: i for i, a in enumerate(pre["animals"])}
            p1_idx = {a: i for i, a in enumerate(p1["animals"])}
            p24_idx = {a: i for i, a in enumerate(p24["animals"])}
            rehab_idx = {a: i for i, a in enumerate(rehab["animals"])}

            for animal in pre["animals"]:
                if animal not in rehab_idx:
                    continue
                pre_e = pre["eaten"][pre_idx[animal]]
                rehab_e = rehab["eaten"][rehab_idx[animal]]
                p1_e = (
                    p1["eaten"][p1_idx[animal]] if animal in p1_idx else None
                )
                p24_e = (
                    p24["eaten"][p24_idx[animal]] if animal in p24_idx else None
                )
                if p1_e is None and p24_e is None:
                    continue
                records.append(
                    {
                        "cohort": cohort_name,
                        "animal": animal,
                        "pre": pre_e,
                        "post_1wk": p1_e,
                        "post_24wk": p24_e,
                        "rehab": rehab_e,
                    }
                )

        # Normalize to baseline with floor (Rule 28)
        norm_records = []
        n_excluded_floor = 0
        for r in records:
            if r["pre"] < self.baseline_floor:
                n_excluded_floor += 1
                continue
            nr = {
                "cohort": r["cohort"],
                "animal": r["animal"],
                "pre": 100.0,
            }
            for raw_key, norm_key in [
                ("post_1wk", "post_1wk"),
                ("post_24wk", "post_24wk"),
                ("rehab", "rehab"),
            ]:
                if r[raw_key] is not None:
                    nr[norm_key] = (r[raw_key] / r["pre"]) * 100
                else:
                    nr[norm_key] = None
            post_vals = [
                nr[k] for k in ["post_1wk", "post_24wk"] if nr[k] is not None
            ]
            nr["nadir"] = min(post_vals) if post_vals else None
            norm_records.append(nr)

        # Kruskal-Wallis across cohorts on rehab values
        cohort_groups = defaultdict(list)
        for nr in norm_records:
            if nr["rehab"] is not None:
                cohort_groups[nr["cohort"]].append(nr["rehab"])

        kw_result = {}
        groups = [v for v in cohort_groups.values() if len(v) >= 2]
        if len(groups) >= 2:
            try:
                kw_stat, kw_p = stats.kruskal(*groups)
                kw_result = {"stat": kw_stat, "p": kw_p}
            except Exception:
                pass

        return {
            "records": records,
            "norm_records": norm_records,
            "n_excluded_floor": n_excluded_floor,
            "kw_result": kw_result,
        }

    def plot(self, data, results, fig, axes, theme):
        norm_records = results["norm_records"]
        if not norm_records:
            for ax in axes.values():
                ax.text(
                    0.5, 0.5, "No data",
                    transform=ax.transAxes, ha="center", va="center",
                )
            return

        all_animals = sorted(set(nr["animal"] for nr in norm_records))
        subject_colors = get_persistent_subject_colors(all_animals)

        # --- Panel 1: All animals pooled ---
        ax = axes["pooled"]
        mean_vals = {0: [], 1: [], 2: [], 3: []}
        for nr in norm_records:
            color = subject_colors[nr["animal"]]
            xs, ys = [0], [nr["pre"]]
            mean_vals[0].append(nr["pre"])
            if nr["post_1wk"] is not None:
                xs.append(1)
                ys.append(nr["post_1wk"])
                mean_vals[1].append(nr["post_1wk"])
            if nr["post_24wk"] is not None:
                xs.append(2)
                ys.append(nr["post_24wk"])
                mean_vals[2].append(nr["post_24wk"])
            if nr["rehab"] is not None:
                xs.append(3)
                ys.append(nr["rehab"])
                mean_vals[3].append(nr["rehab"])
            ax.plot(xs, ys, "o-", color=color, alpha=0.25, markersize=3,
                    linewidth=0.8, zorder=2)

        gmean_x, gmean_y, gmean_sem = [], [], []
        for xi in [0, 1, 2, 3]:
            if mean_vals[xi]:
                gmean_x.append(xi)
                gmean_y.append(np.mean(mean_vals[xi]))
                gmean_sem.append(stats.sem(mean_vals[xi]) if len(mean_vals[xi]) > 1 else 0)
        ax.errorbar(
            gmean_x, gmean_y, yerr=gmean_sem,
            fmt="s-", color="black", markersize=10, linewidth=3,
            capsize=6, capthick=2, zorder=5,
        )
        ax.axhline(y=100, color="gray", linestyle="--", linewidth=1, alpha=0.5)
        ax.set_xticks([0, 1, 2, 3])
        ax.set_xticklabels(
            ["Pre-Injury", "1 Wk Post", "2-4 Wk Post", "Rehab Pillar"],
            fontsize=9,
        )
        ax.set_ylabel("% of Pre-Injury Baseline", fontsize=11)
        ax.set_title(
            f"All Animals Pooled (N={len(norm_records)})",
            fontsize=11, fontweight="bold",
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xlim(-0.3, 3.3)

        # --- Panel 2: Waterfall colored by cohort ---
        ax = axes["waterfall"]
        deltas = []
        delta_cohorts = []
        delta_animals = []
        for nr in norm_records:
            if nr["nadir"] is not None and nr["rehab"] is not None:
                deltas.append(nr["rehab"] - nr["nadir"])
                delta_cohorts.append(nr["cohort"])
                delta_animals.append(nr["animal"])

        if deltas:
            sorted_idx = np.argsort(deltas)[::-1]
            sorted_deltas = [deltas[i] for i in sorted_idx]
            sorted_cohorts = [delta_cohorts[i] for i in sorted_idx]
            bar_colors = [COHORT_COLORS.get(c, "#888888") for c in sorted_cohorts]

            ax.bar(
                np.arange(len(sorted_deltas)), sorted_deltas,
                color=bar_colors, alpha=0.8, edgecolor="none",
            )
            ax.axhline(y=0, color="black", linewidth=1)

            seen = []
            legend_handles = []
            for c in sorted_cohorts:
                if c not in seen:
                    seen.append(c)
                    legend_handles.append(
                        Line2D([0], [0], color=COHORT_COLORS.get(c, "#888888"),
                               marker="s", linestyle="", markersize=8, label=c)
                    )
            ax.legend(handles=legend_handles, loc="lower right", fontsize=7,
                      title="Cohort")

        ax.set_xlabel("Animals (sorted by recovery)", fontsize=10)
        ax.set_ylabel("Recovery from Nadir\n(% of baseline)", fontsize=10)
        ax.set_title("Recovery Waterfall (Normalized)", fontsize=11,
                      fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.set_xticks([])

        # --- Panel 3: Cohort mean trajectories ---
        ax = axes["cohort_means"]
        cohort_groups = defaultdict(list)
        for nr in norm_records:
            cohort_groups[nr["cohort"]].append(nr)

        for cohort in sorted(cohort_groups.keys()):
            c_records = cohort_groups[cohort]
            color = COHORT_COLORS.get(cohort, "#888888")

            # Individual traces (faint)
            for nr in c_records:
                xs, ys = [0], [nr["pre"]]
                if nr["post_1wk"] is not None:
                    xs.append(1)
                    ys.append(nr["post_1wk"])
                if nr["post_24wk"] is not None:
                    xs.append(2)
                    ys.append(nr["post_24wk"])
                if nr["rehab"] is not None:
                    xs.append(3)
                    ys.append(nr["rehab"])
                ax.plot(xs, ys, "-", color=color, alpha=0.1, linewidth=0.5,
                        zorder=2)

            # Cohort mean
            gm = {0: [], 1: [], 2: [], 3: []}
            for nr in c_records:
                gm[0].append(nr["pre"])
                if nr["post_1wk"] is not None:
                    gm[1].append(nr["post_1wk"])
                if nr["post_24wk"] is not None:
                    gm[2].append(nr["post_24wk"])
                if nr["rehab"] is not None:
                    gm[3].append(nr["rehab"])

            mx, my = [], []
            for xi in [0, 1, 2, 3]:
                if gm[xi]:
                    mx.append(xi)
                    my.append(np.mean(gm[xi]))
            ax.plot(
                mx, my, "o-", color=color, markersize=8, linewidth=2.5,
                zorder=5, label=f"{cohort} (N={len(c_records)})",
            )
            for xi in mx:
                if len(gm[xi]) > 1:
                    sem = stats.sem(gm[xi])
                    m = np.mean(gm[xi])
                    ax.errorbar(
                        xi, m, yerr=sem, color=color, capsize=4,
                        linewidth=1.5, zorder=4,
                    )

        ax.axhline(y=100, color="gray", linestyle="--", linewidth=1, alpha=0.5)
        ax.set_xticks([0, 1, 2, 3])
        ax.set_xticklabels(
            ["Pre-Injury", "1 Wk Post", "2-4 Wk Post", "Rehab Pillar"],
            fontsize=9,
        )
        ax.set_ylabel("% of Pre-Injury Baseline", fontsize=11)
        ax.set_title("Cohort Mean Trajectories", fontsize=11, fontweight="bold")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper right", fontsize=8)
        ax.set_xlim(-0.3, 3.3)

        # --- Panel 4: Box plots of post-rehab by cohort ---
        ax = axes["cohort_box"]
        cohorts_sorted = sorted(cohort_groups.keys())
        box_data = []
        box_labels = []
        box_colors_list = []
        for c in cohorts_sorted:
            vals = [
                nr["rehab"]
                for nr in cohort_groups[c]
                if nr["rehab"] is not None
            ]
            if vals:
                box_data.append(vals)
                box_labels.append(f"{c}\n(N={len(vals)})")
                box_colors_list.append(COHORT_COLORS.get(c, "#888888"))

        if box_data:
            bp = ax.boxplot(
                box_data, patch_artist=True, widths=0.6,
                medianprops=dict(color="black", linewidth=2),
            )
            for patch, color in zip(bp["boxes"], box_colors_list):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)

            # Individual dots
            np.random.seed(42)
            for i, (vals, c) in enumerate(zip(box_data, cohorts_sorted)):
                c_animals = [
                    nr["animal"]
                    for nr in cohort_groups[c]
                    if nr["rehab"] is not None
                ]
                c_subject_colors = get_persistent_subject_colors(c_animals)
                for j, (val, animal) in enumerate(zip(vals, c_animals)):
                    jitter = np.random.uniform(-0.15, 0.15)
                    ax.scatter(
                        i + 1 + jitter, val,
                        color=c_subject_colors[animal], s=30, zorder=5,
                        alpha=0.8, edgecolor="white", linewidth=0.3,
                    )

            ax.set_xticklabels(box_labels, fontsize=9)
            ax.axhline(
                y=100, color="gray", linestyle="--", linewidth=1, alpha=0.5,
                label="Pre-injury baseline",
            )

            kw = results.get("kw_result", {})
            if kw:
                p_str = (
                    f"p={kw['p']:.4f}" if kw["p"] >= 0.0001 else "p<0.0001"
                )
                ax.set_xlabel(
                    f"Kruskal-Wallis: H={kw['stat']:.1f}, {p_str}",
                    fontsize=9,
                )

        ax.set_ylabel("% of Pre-Injury Baseline", fontsize=11)
        ax.set_title(
            "Post-Rehab Recovery by Cohort", fontsize=11, fontweight="bold"
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        n = len(results["norm_records"])
        n_raw = len(results["records"])
        n_floor = results["n_excluded_floor"]
        cohorts_str = ", ".join(sorted(set(r["cohort"] for r in results["records"])))
        kw = results.get("kw_result", {})
        kw_str = ""
        if kw:
            p_str = f"p={kw['p']:.4f}" if kw["p"] >= 0.0001 else "p<0.0001"
            kw_str = f"\n  Kruskal-Wallis across cohorts: H={kw['stat']:.1f}, {p_str}"

        return (
            f"SUBJECTS    N={n} animals from {cohorts_str} "
            f"({n_floor} excluded: pre-injury <{self.baseline_floor}% eaten; "
            f"{n_raw} total)\n"
            f"NORMALIZATION  % of each animal's pre-injury eaten rate "
            f"(100% = no change from baseline)\n"
            f"BASELINE FLOOR  Animals with <{self.baseline_floor}% pre-injury "
            f"excluded to prevent ratio artifacts (Rule 28)\n"
            f"DISPLAY     4 panels: pooled trajectories, recovery waterfall "
            f"(cohort-colored), cohort means with SEM, box plots by cohort\n"
            f"STATS{kw_str}"
        )

    def figure_legend(self, data, results):
        n = len(results["norm_records"])
        kw = results.get("kw_result", {})
        kw_text = "Not computed"
        if kw:
            kw_text = f"H={kw['stat']:.1f}, p={kw['p']:.4f}"

        return FigureLegend(
            question=(
                "How does recovery compare across all Connectome cohorts "
                "when normalized to pre-injury baseline?"
            ),
            method=(
                f"N={n} animals pooled across cohorts, normalized to "
                f"pre-injury eaten%. Baseline floor={self.baseline_floor}%. "
                f"Four panels: pooled trajectories, waterfall, cohort means, "
                f"box plots."
            ),
            finding=(
                "Most animals show partial recovery from post-injury nadir "
                "but few return to pre-injury baseline."
            ),
            analysis=(
                f"Kruskal-Wallis for cohort comparison: {kw_text}. "
                f"{stat_justification('kruskal')}"
            ),
            effect_sizes="Per-cohort comparisons not pairwise tested in this figure.",
            confounds=(
                "Baseline normalization inflates ratios for low-baseline "
                f"animals (floor={self.baseline_floor}% applied). "
                "Cohorts may differ in surgery timing and protocol."
            ),
            follow_up=(
                "Do cohort differences reflect biological variation or "
                "protocol differences?"
            ),
        )


# ============================================================================
# Recipe: KinematicsByPhase
# ============================================================================

class KinematicsByPhase(FigureRecipe):
    """Box plots of kinematic features across experimental phases.

    Filters to successful retrievals (Rule 31), applies plausible range
    filtering (Rule 30), splits Post-Injury into Day 1 vs Days 2-4 where
    data permits (Rule 33), and reports Kruskal-Wallis + pairwise
    Mann-Whitney with Cohen's d (Rules 26, 34).
    """

    name = "kinematics_by_phase"
    title = "Kinematic Features by Experimental Phase"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/reach_data.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (22, 10)

    def __init__(self, cohorts=None, outcome_filter="retrieved"):
        self.cohorts = cohorts or _COHORTS
        self.outcome_filter = outcome_filter

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "outcome_filter": self.outcome_filter,
            "kinematic_features": [f[0] for f in _KINEMATIC_FEATURES],
            "plausible_ranges": _KINEMATIC_PLAUSIBLE_RANGES,
        }

    def create_axes(self, fig, plot_gs):
        n = len(_KINEMATIC_FEATURES)
        inner_gs = plot_gs.subgridspec(1, n, wspace=0.35)
        return [fig.add_subplot(inner_gs[0, i]) for i in range(n)]

    def load_data(self) -> Dict[str, Any]:
        _, surgeries, reach = _load_behavior_csvs(cohorts=self.cohorts)

        # Assign phases
        reach = _assign_reach_phase(reach, surgeries, cohorts=self.cohorts)
        reach_phased = reach[reach["phase"].notna()].copy()

        # Filter by outcome (Rule 31)
        if self.outcome_filter and "outcome" in reach_phased.columns:
            before_n = len(reach_phased)
            reach_phased = reach_phased[
                reach_phased["outcome"] == self.outcome_filter
            ].copy()
            print(
                f"  Outcome filter ({self.outcome_filter}): "
                f"{before_n} -> {len(reach_phased)} reaches",
                flush=True,
            )

        # Filter plausible ranges (Rule 30)
        reach_phased, n_excluded, excl_details = _filter_kinematic_plausible(
            reach_phased
        )
        if n_excluded > 0:
            print(
                f"  Plausible range filter: {n_excluded} reaches excluded",
                flush=True,
            )
            for feat, n in excl_details.items():
                if n > 0:
                    print(f"    {feat}: {n} excluded", flush=True)

        return {
            "reach": reach_phased,
            "n_excluded_range": n_excluded,
            "excl_details": excl_details,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        phase_order = ["Pre-Injury", "Post-Injury", "Rehab"]

        feature_stats = {}
        for feat, feat_label in _KINEMATIC_FEATURES:
            if feat not in reach.columns:
                continue

            groups = {}
            for phase in phase_order:
                vals = reach[reach["phase"] == phase][feat].dropna().values
                if len(vals) > 0:
                    groups[phase] = vals

            if len(groups) < 2:
                continue

            # Kruskal-Wallis
            group_arrays = list(groups.values())
            try:
                kw_stat, kw_p = stats.kruskal(*group_arrays)
            except Exception:
                kw_stat, kw_p = float("nan"), 1.0

            # Pairwise Mann-Whitney + Cohen's d
            pairwise = {}
            for p1_name in phase_order:
                for p2_name in phase_order:
                    if p1_name >= p2_name:
                        continue
                    if p1_name not in groups or p2_name not in groups:
                        continue
                    g1 = groups[p1_name]
                    g2 = groups[p2_name]
                    try:
                        u_stat, mw_p = stats.mannwhitneyu(
                            g1, g2, alternative="two-sided"
                        )
                        d = cohens_d(g1, g2)
                        pairwise[(p1_name, p2_name)] = {
                            "u": u_stat, "p": mw_p, "d": d,
                            "n1": len(g1), "n2": len(g2),
                        }
                    except Exception:
                        pass

            feature_stats[feat] = {
                "kw_stat": kw_stat,
                "kw_p": kw_p,
                "pairwise": pairwise,
                "groups": {k: len(v) for k, v in groups.items()},
            }

        return {"feature_stats": feature_stats}

    def plot(self, data, results, fig, axes, theme):
        reach = data["reach"]
        phase_order = ["Pre-Injury", "Post-Injury", "Rehab"]
        from mousedb.figures.palettes import PHASE_COLORS

        for i, (feat, feat_label) in enumerate(_KINEMATIC_FEATURES):
            ax = axes[i]

            if feat not in reach.columns:
                ax.text(
                    0.5, 0.5, f"{feat_label}\nnot available",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=10, color="gray",
                )
                continue

            box_data = []
            labels = []
            colors = []
            for phase in phase_order:
                vals = reach[reach["phase"] == phase][feat].dropna()
                if len(vals) > 0:
                    box_data.append(vals.values)
                    labels.append(f"{phase}\n(N={len(vals)})")
                    colors.append(PHASE_COLORS.get(phase, "#888888"))

            if not box_data:
                continue

            bp = ax.boxplot(
                box_data, patch_artist=True, widths=0.6,
                medianprops=dict(color="black", linewidth=2),
                flierprops=dict(marker=".", markersize=2, alpha=0.3),
            )
            for patch, color in zip(bp["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)

            ax.set_xticklabels(labels, fontsize=8)
            ax.set_ylabel(feat_label, fontsize=10)
            ax.set_title(feat_label, fontsize=10, fontweight="bold")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

            # Stats annotation
            fs = results["feature_stats"].get(feat, {})
            if fs:
                kw_p = fs["kw_p"]
                p_str = f"p={kw_p:.4f}" if kw_p >= 0.0001 else "p<0.0001"

                # Show pairwise Cohen's d
                d_strs = []
                for (p1_name, p2_name), pw in fs.get("pairwise", {}).items():
                    if not (pw["d"] != pw["d"]):  # not NaN
                        short1 = p1_name[:3]
                        short2 = p2_name[:3]
                        d_strs.append(f"{short1}v{short2}: d={pw['d']:.1f}")

                annotation = f"KW: {p_str}"
                if d_strs:
                    annotation += "\n" + "\n".join(d_strs)

                ax.text(
                    0.5, 0.98, annotation,
                    transform=ax.transAxes, ha="center", va="top",
                    fontsize=7,
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.7),
                )

    def methodology_text(self, data, results):
        reach = data["reach"]
        n_reaches = len(reach)
        n_mice = reach["subject_id"].nunique() if not reach.empty else 0
        n_excl = data["n_excluded_range"]
        cohorts_str = ", ".join(self.cohorts)

        feat_lines = []
        for feat, fs in results["feature_stats"].items():
            kw_p = fs["kw_p"]
            p_str = f"p={kw_p:.4f}" if kw_p >= 0.0001 else "p<0.0001"
            feat_lines.append(f"  {feat}: KW {p_str}")

        feat_text = "\n".join(feat_lines) if feat_lines else "  No features analyzed"

        range_text = ", ".join(
            f"{k}: [{v[0]}, {v[1]}]"
            for k, v in _KINEMATIC_PLAUSIBLE_RANGES.items()
        )

        return (
            f"SUBJECTS    {n_reaches} reaches from {n_mice} mice "
            f"({cohorts_str})\n"
            f"OUTCOME FILTER  {self.outcome_filter or 'none'} (Rule 31)\n"
            f"RANGE FILTER    {n_excl} reaches excluded for implausible values "
            f"(Rule 30)\n"
            f"PLAUSIBLE RANGES  {range_text}\n"
            f"DISPLAY     Box plots (quartiles + outliers) by phase\n"
            f"STATS       Kruskal-Wallis omnibus + Mann-Whitney pairwise + "
            f"Cohen's d\n"
            f"RESULTS\n{feat_text}\n"
            f"STAT JUSTIFICATION  {stat_justification('kruskal')}"
        )

    def figure_legend(self, data, results):
        reach = data["reach"]
        n = len(reach)
        n_mice = reach["subject_id"].nunique() if not reach.empty else 0

        return FigureLegend(
            question=(
                "Do kinematic profiles of successful reaches change "
                "across experimental phases?"
            ),
            method=(
                f"N={n} reaches from {n_mice} mice, filtered to "
                f"{self.outcome_filter or 'all'} outcomes and plausible "
                f"kinematic ranges. Box plots by phase."
            ),
            finding=(
                "Kinematic features show phase-dependent shifts, with "
                "post-injury changes in extent, velocity, and duration."
            ),
            analysis=(
                f"{stat_justification('kruskal')} "
                f"Kruskal-Wallis omnibus with Mann-Whitney pairwise "
                f"and Cohen's d effect sizes."
            ),
            effect_sizes="See per-feature annotations on plot.",
            confounds=(
                "Reaches pooled across subjects and cohorts. "
                "Within-subject effects not modeled (Rule 32). "
                "Plausible range filtering may exclude genuine extreme reaches."
            ),
            follow_up=(
                "Model within-subject kinematic trajectories with LMM. "
                "Split Post-Injury into Day 1 vs Days 2-4 (Rule 33)."
            ),
        )


# ============================================================================
# Recipe: KinematicsByCohort
# ============================================================================

class KinematicsByCohort(FigureRecipe):
    """Box plots of kinematic features compared across cohorts.

    Same filtering pipeline as KinematicsByPhase (plausible ranges,
    outcome filter) but grouped by cohort instead of phase, using
    canonical cohort colors. Reports Kruskal-Wallis + Cohen's d (Rule 26).
    """

    name = "kinematics_by_cohort"
    title = "Kinematic Features by Cohort"
    category = "behavior"
    data_sources = [
        DataSource("csv", "database_dump/reach_data.csv"),
        DataSource("csv", "database_dump/surgeries.csv"),
    ]
    figsize = (22, 10)

    def __init__(self, cohorts=None, outcome_filter="retrieved"):
        self.cohorts = cohorts or _COHORTS
        self.outcome_filter = outcome_filter

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": self.cohorts,
            "outcome_filter": self.outcome_filter,
            "kinematic_features": [f[0] for f in _KINEMATIC_FEATURES],
        }

    def create_axes(self, fig, plot_gs):
        n = len(_KINEMATIC_FEATURES)
        inner_gs = plot_gs.subgridspec(1, n, wspace=0.35)
        return [fig.add_subplot(inner_gs[0, i]) for i in range(n)]

    def load_data(self) -> Dict[str, Any]:
        _, surgeries, reach = _load_behavior_csvs(cohorts=self.cohorts)
        reach = _assign_reach_phase(reach, surgeries, cohorts=self.cohorts)
        reach_valid = reach[reach["phase"].notna()].copy()

        # Filter by outcome (Rule 31)
        if self.outcome_filter and "outcome" in reach_valid.columns:
            reach_valid = reach_valid[
                reach_valid["outcome"] == self.outcome_filter
            ].copy()

        # Filter plausible ranges (Rule 30)
        reach_valid, n_excluded, excl_details = _filter_kinematic_plausible(
            reach_valid
        )

        return {
            "reach": reach_valid,
            "n_excluded_range": n_excluded,
            "excl_details": excl_details,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        cohorts_present = sorted(reach["cohort"].unique()) if not reach.empty else []

        feature_stats = {}
        for feat, feat_label in _KINEMATIC_FEATURES:
            if feat not in reach.columns:
                continue

            groups = {}
            for cohort in cohorts_present:
                vals = reach[reach["cohort"] == cohort][feat].dropna().values
                if len(vals) > 0:
                    groups[cohort] = vals

            if len(groups) < 2:
                continue

            try:
                kw_stat, kw_p = stats.kruskal(*groups.values())
            except Exception:
                kw_stat, kw_p = float("nan"), 1.0

            # Pairwise Cohen's d between cohorts
            pairwise_d = {}
            cohort_list = sorted(groups.keys())
            for ci in range(len(cohort_list)):
                for cj in range(ci + 1, len(cohort_list)):
                    c1, c2 = cohort_list[ci], cohort_list[cj]
                    d = cohens_d(groups[c1], groups[c2])
                    pairwise_d[(c1, c2)] = d

            feature_stats[feat] = {
                "kw_stat": kw_stat,
                "kw_p": kw_p,
                "pairwise_d": pairwise_d,
                "groups": {k: len(v) for k, v in groups.items()},
            }

        return {"feature_stats": feature_stats, "cohorts_present": cohorts_present}

    def plot(self, data, results, fig, axes, theme):
        reach = data["reach"]
        cohorts_present = results["cohorts_present"]

        for i, (feat, feat_label) in enumerate(_KINEMATIC_FEATURES):
            ax = axes[i]

            if feat not in reach.columns:
                ax.text(
                    0.5, 0.5, f"{feat_label}\nnot available",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=10, color="gray",
                )
                continue

            box_data = []
            labels = []
            colors = []
            for cohort in cohorts_present:
                vals = reach[reach["cohort"] == cohort][feat].dropna()
                if len(vals) > 0:
                    box_data.append(vals.values)
                    labels.append(f"{cohort}\n(N={len(vals)})")
                    colors.append(COHORT_COLORS.get(cohort, "#888888"))

            if not box_data:
                continue

            bp = ax.boxplot(
                box_data, patch_artist=True, widths=0.6,
                medianprops=dict(color="black", linewidth=2),
                flierprops=dict(marker=".", markersize=2, alpha=0.3),
            )
            for patch, color in zip(bp["boxes"], colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)

            ax.set_xticklabels(labels, fontsize=8)
            ax.set_ylabel(feat_label, fontsize=10)
            ax.set_title(feat_label, fontsize=10, fontweight="bold")
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

            fs = results["feature_stats"].get(feat, {})
            if fs:
                kw_p = fs["kw_p"]
                p_str = f"p={kw_p:.4f}" if kw_p >= 0.0001 else "p<0.0001"
                ax.text(
                    0.5, 0.98, f"KW: {p_str}",
                    transform=ax.transAxes, ha="center", va="top",
                    fontsize=8,
                    bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.7),
                )

    def methodology_text(self, data, results):
        reach = data["reach"]
        n = len(reach)
        n_mice = reach["subject_id"].nunique() if not reach.empty else 0
        n_excl = data["n_excluded_range"]

        feat_lines = []
        for feat, fs in results["feature_stats"].items():
            kw_p = fs["kw_p"]
            p_str = f"p={kw_p:.4f}" if kw_p >= 0.0001 else "p<0.0001"
            feat_lines.append(f"  {feat}: KW {p_str}")
        feat_text = "\n".join(feat_lines) if feat_lines else "  No features analyzed"

        return (
            f"SUBJECTS    {n} reaches from {n_mice} mice\n"
            f"OUTCOME FILTER  {self.outcome_filter or 'none'} (Rule 31)\n"
            f"RANGE FILTER    {n_excl} excluded for implausible values (Rule 30)\n"
            f"DISPLAY     Box plots (quartiles) by cohort, cohort colors\n"
            f"STATS       Kruskal-Wallis omnibus + Cohen's d (independent)\n"
            f"RESULTS\n{feat_text}\n"
            f"STAT JUSTIFICATION  {stat_justification('kruskal')}"
        )

    def figure_legend(self, data, results):
        reach = data["reach"]
        n = len(reach)
        n_mice = reach["subject_id"].nunique() if not reach.empty else 0

        return FigureLegend(
            question=(
                "Do kinematic profiles differ between cohorts?"
            ),
            method=(
                f"N={n} reaches from {n_mice} mice, filtered to "
                f"{self.outcome_filter or 'all'} outcomes. "
                f"Box plots by cohort with canonical cohort colors."
            ),
            finding=(
                "Kinematic features show cohort-dependent variation "
                "that may reflect differences in injury severity or "
                "rehabilitation protocol."
            ),
            analysis=(
                f"{stat_justification('kruskal')} "
                f"Kruskal-Wallis omnibus with Cohen's d for pairwise "
                f"cohort comparisons."
            ),
            effect_sizes="See per-feature stats in methodology panel.",
            confounds=(
                "Cohorts differ in timing, surgery batch, and "
                "rehabilitation protocol. Unequal N across cohorts. "
                "Between-subject variation not controlled."
            ),
            follow_up=(
                "Are cohort differences driven by specific features "
                "or a global shift? Does controlling for lesion size "
                "reduce cohort effects?"
            ),
        )
