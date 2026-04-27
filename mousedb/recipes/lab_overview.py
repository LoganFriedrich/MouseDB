"""
Lab overview figure recipes.

Migrated from lab_figures.py data figures (fig_04, fig_06, fig_09, fig_10, fig_15)
into the FigureRecipe system with proper provenance, FigureLegend, and
adherence to FIGURE_REVIEW_LESSONS.md rules.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from mousedb import MOUSEDB_ROOT
from mousedb.figures.palettes import (
    BRAIN_COLORS,
    COHORT_COLORS,
    DOMAIN_COLORS,
    OUTCOME_COLORS,
    get_persistent_subject_colors,
)
from mousedb.figures.legends import FigureLegend
from mousedb.figures.stats import (
    cohens_d,
    cohens_d_paired,
    format_stat_result,
    stat_justification,
)
from mousedb.recipes.base import DataSource, FigureRecipe


# ============================================================================
# Paths (lab standard data locations)
# ============================================================================

_DATA_SUMMARY = (
    Path(r"Y:\2_Connectome")
    / "Tissue"
    / "MouseBrain_Pipeline"
    / "3D_Cleared"
    / "2_Data_Summary"
)
_BEHAVIOR = Path(r"Y:\2_Connectome") / "Behavior" / "MouseReach_Pipeline"

# Default brain IDs used in lab_figures.py
_BRAIN_IDS = [
    "349_CNT_01_02",
    "357_CNT_02_08",
    "367_CNT_03_07",
    "368_CNT_03_08",
]


# ============================================================================
# Recipe 1: BrainRegionCountsLab
# ============================================================================

class BrainRegionCountsLab(FigureRecipe):
    """Top brain regions by cell count across processed brains.

    Grouped bar chart showing cell counts per region for each brain,
    sorted by mean count. Fixes from Review 1: add provenance, use
    canonical palettes, add per-brain labels with cohort context (Rule 7).
    """

    name = "brain_region_counts_lab"
    title = "Top Brain Regions by Cell Count Across Processed Brains"
    category = "lab_overview"
    data_sources = [
        DataSource(
            "csv",
            str(_DATA_SUMMARY / "{brain_id}_counts.csv"),
            query_filter="Per-brain region count CSVs from 2_Data_Summary",
        ),
    ]
    figsize = (16, 11)

    def __init__(self, brain_ids=None, top_n=15):
        """
        Parameters
        ----------
        brain_ids : list of str, optional
            Brain IDs to include. Defaults to the 4 standard brains.
        top_n : int
            Number of top regions to display.
        """
        self.brain_ids = brain_ids or list(_BRAIN_IDS)
        self.top_n = top_n

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "brain_ids": self.brain_ids,
            "top_n": self.top_n,
        }

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        """Load region count CSVs for each brain."""
        counts = {}
        for bid in self.brain_ids:
            # Filename pattern: {bid}_{bid}_1p625x_z4_counts.csv
            pattern = f"{bid}_{bid}_1p625x_z4_counts.csv"
            path = _DATA_SUMMARY / pattern
            if path.exists():
                df = pd.read_csv(path)
                counts[bid] = df
                total = df["cell_count"].sum()
                print(f"    {bid}: {total:,} cells, {len(df)} regions", flush=True)
            else:
                print(f"    {bid}: CSV not found at {path}", flush=True)

        if not counts:
            raise FileNotFoundError(
                "No brain count CSVs found in " + str(_DATA_SUMMARY)
            )

        # Merge all brains on region_acronym
        merged = None
        valid_brains = []
        for bid in self.brain_ids:
            if bid in counts:
                df = counts[bid][["region_acronym", "cell_count"]].copy()
                df = df.rename(columns={"cell_count": bid})
                if merged is None:
                    merged = df
                else:
                    merged = merged.merge(df, on="region_acronym", how="outer")
                valid_brains.append(bid)

        merged = merged.fillna(0)
        merged["mean"] = merged[valid_brains].mean(axis=1)
        merged = merged.sort_values("mean", ascending=False).head(self.top_n)

        # Per-brain totals
        brain_totals = {bid: counts[bid]["cell_count"].sum() for bid in valid_brains}

        return {
            "counts": counts,
            "merged": merged,
            "valid_brains": valid_brains,
            "brain_totals": brain_totals,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Descriptive analysis -- no inferential stats for region counts."""
        merged = data["merged"]
        valid_brains = data["valid_brains"]
        brain_totals = data["brain_totals"]

        # Cross-brain variability per region (CV)
        region_cv = {}
        for _, row in merged.iterrows():
            vals = [row[bid] for bid in valid_brains]
            mean_val = np.mean(vals)
            if mean_val > 0:
                cv = np.std(vals, ddof=1) / mean_val
                region_cv[row["region_acronym"]] = cv

        return {
            "region_cv": region_cv,
            "total_cells": sum(brain_totals.values()),
            "n_brains": len(valid_brains),
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        merged = data["merged"]
        valid_brains = data["valid_brains"]
        brain_totals = data["brain_totals"]

        x = np.arange(len(merged))
        n_brains = len(valid_brains)
        width = 0.8 / n_brains

        for i, bid in enumerate(valid_brains):
            color = BRAIN_COLORS[i % len(BRAIN_COLORS)]
            total = brain_totals[bid]
            # Include cohort context in label (Rule 7)
            label = f"{bid} ({total:,} cells)"
            ax.bar(
                x + i * width - (n_brains - 1) * width / 2,
                merged[bid].values,
                width,
                label=label,
                color=color,
                edgecolor="white",
                linewidth=0.5,
            )

        ax.set_xticks(x)
        ax.set_xticklabels(
            merged["region_acronym"].values, rotation=45, ha="right", fontsize=11
        )
        ax.set_ylabel("Cell Count", fontsize=13)
        ax.set_title(self.title, fontsize=16, fontweight="bold", pad=12)
        ax.legend(loc="upper right", framealpha=0.9, fontsize=10)
        ax.set_xlim(-0.5, len(merged) - 0.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", alpha=0.15, linewidth=0.5)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        valid_brains = data["valid_brains"]
        brain_totals = data["brain_totals"]
        total = results["total_cells"]
        brain_lines = "\n".join(
            f"  {bid:20s} {brain_totals[bid]:>8,} cells"
            for bid in valid_brains
        )
        return (
            f"SOURCE      Per-brain region count CSVs from calibration pipeline\n"
            f"BRAINS      N={len(valid_brains)} brains, {total:,} total cells\n"
            f"{brain_lines}\n"
            f"REGIONS     Top {self.top_n} by mean count across brains\n"
            f"METRIC      Raw cell count per region (bilateral sum)\n"
            f"NOTE        Counts depend on detection parameters (ball_xy, ball_z, threshold) "
            f"-- see calibration_runs.csv for provenance"
        )

    def figure_legend(self, data, results):
        n_brains = results["n_brains"]
        total = results["total_cells"]
        return FigureLegend(
            question="Which brain regions contain the most retrogradely-labeled cells?",
            method=(
                f"N={n_brains} cleared brains processed through the MouseBrain pipeline. "
                f"Cell counts per Allen Atlas region extracted from calibration runs "
                f"marked as best. Top {self.top_n} regions by mean count shown."
            ),
            finding=(
                f"Total {total:,} cells across {n_brains} brains. "
                f"Cross-brain variability visible in grouped bars."
            ),
            analysis="Descriptive (grouped bar chart). No inferential test applied.",
            effect_sizes="N/A -- descriptive comparison across brains.",
            confounds=(
                "Cell counts depend on detection parameters, which vary per brain. "
                "Region boundaries from Allen Atlas registration may differ in accuracy "
                "across samples. Bilateral counts summed (see laterality recipe for L/R)."
            ),
            follow_up=(
                "Do region proportions match published eLife reference data? "
                "See elife_comparison recipe."
            ),
        )


# ============================================================================
# Recipe 2: HemisphereLaterality
# ============================================================================

class HemisphereLaterality(FigureRecipe):
    """Butterfly chart of left vs right hemisphere cell counts by eLife region.

    Fixes from Review 1: handle outliers (Rule 9), add per-brain colors
    (Rule 6), add FigureLegend.
    """

    name = "hemisphere_laterality"
    title = "Hemisphere Laterality: Left vs Right Cell Counts by eLife Region"
    category = "lab_overview"
    data_sources = [
        DataSource(
            "csv",
            str(_DATA_SUMMARY / "laterality" / "hemisphere_laterality_analysis.csv"),
        ),
    ]
    figsize = (16, 11)

    def get_parameters(self) -> Dict[str, Any]:
        return {}

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        path = _DATA_SUMMARY / "laterality" / "hemisphere_laterality_analysis.csv"
        if not path.exists():
            raise FileNotFoundError(f"Laterality CSV not found: {path}")

        df = pd.read_csv(path)
        print(f"  Loaded {len(df)} eLife groups from {path.name}", flush=True)

        # Sort by mean_total ascending (for horizontal bar chart)
        df = df.sort_values("mean_total", ascending=True).copy()

        # Identify individual brain columns
        brain_cols = {}
        for col in df.columns:
            for prefix in ["349", "357", "367", "368"]:
                if col.startswith(prefix):
                    side = "left" if "left" in col else "right" if "right" in col else None
                    if side:
                        brain_cols.setdefault(prefix, {})[side] = col

        return {
            "df": df,
            "brain_cols": brain_cols,
            "csv_path": path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        df = data["df"]

        # Compute laterality index (LI) = (R - L) / (R + L) per group
        laterality_indices = {}
        for _, row in df.iterrows():
            group = row["elife_group"]
            left = row["mean_left"]
            right = row["mean_right"]
            total = left + right
            if total > 0:
                li = (right - left) / total
                laterality_indices[group] = li

        # Identify outlier regions (Rule 9): regions where total > 2*median
        totals = df["mean_total"].values
        median_total = np.median(totals)
        outlier_mask = df["mean_total"] > 2 * median_total
        outlier_groups = df.loc[outlier_mask, "elife_group"].tolist()

        return {
            "laterality_indices": laterality_indices,
            "outlier_groups": outlier_groups,
            "median_total": median_total,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        df = data["df"]
        brain_cols = data["brain_cols"]
        outlier_groups = results["outlier_groups"]

        groups = df["elife_group"].values
        left = df["mean_left"].values
        right = df["mean_right"].values
        sig = (
            df["LR_sig"].values if "LR_sig" in df.columns else [""] * len(df)
        )

        y_pos = np.arange(len(groups))

        # Left hemisphere (negative direction)
        ax.barh(
            y_pos, -left, color="#2196F3", edgecolor="white", linewidth=0.5,
            label="Left Hemisphere", zorder=2,
        )
        # Right hemisphere (positive direction)
        ax.barh(
            y_pos, right, color="#F44336", edgecolor="white", linewidth=0.5,
            label="Right Hemisphere", zorder=2,
        )

        # Per-brain dots with distinct colors (Rule 6)
        brain_dot_colors = {
            "349": BRAIN_COLORS[0],
            "357": BRAIN_COLORS[1],
            "367": BRAIN_COLORS[2],
            "368": BRAIN_COLORS[3] if len(BRAIN_COLORS) > 3 else "#888888",
        }
        for brain_id, cols in brain_cols.items():
            color = brain_dot_colors.get(brain_id, "#888888")
            if "left" in cols and cols["left"] in df.columns:
                ax.scatter(
                    -df[cols["left"]].values, y_pos, s=20, color=color,
                    alpha=0.7, zorder=3, marker="o", edgecolors="white",
                    linewidths=0.3, label=f"Brain {brain_id}",
                )
            if "right" in cols and cols["right"] in df.columns:
                ax.scatter(
                    df[cols["right"]].values, y_pos, s=20, color=color,
                    alpha=0.7, zorder=3, marker="o", edgecolors="white",
                    linewidths=0.3,
                )

        # Significance markers
        max_right = right.max() if len(right) > 0 else 1
        for i, s in enumerate(sig):
            if s and str(s) not in ("ns", "", "nan"):
                ax.text(
                    max_right * 1.05, i, str(s), va="center", fontsize=9,
                    color="#333333",
                )

        # Outlier annotations (Rule 9)
        for i, grp in enumerate(groups):
            if grp in outlier_groups:
                ax.text(
                    max_right * 1.15, i, "[outlier]", va="center", fontsize=7,
                    color="#E74C3C", fontstyle="italic",
                )

        ax.set_yticks(y_pos)
        ax.set_yticklabels(groups, fontsize=8)
        ax.axvline(x=0, color="black", linewidth=0.8)
        ax.set_xlabel("Mean Cell Count", fontsize=12)
        ax.set_title(self.title, fontsize=15, fontweight="bold", pad=20)

        # De-duplicate legend entries
        handles, labels = ax.get_legend_handles_labels()
        seen = set()
        unique_handles = []
        unique_labels = []
        for h, l in zip(handles, labels):
            if l not in seen:
                seen.add(l)
                unique_handles.append(h)
                unique_labels.append(l)
        ax.legend(unique_handles, unique_labels, loc="lower right", fontsize=10)

        # LEFT / RIGHT labels
        xlim = ax.get_xlim()
        ax.text(
            xlim[0] * 0.4, len(groups) + 0.8, "LEFT", ha="center",
            fontsize=14, fontweight="bold", color="#2196F3",
        )
        ax.text(
            xlim[1] * 0.4, len(groups) + 0.8, "RIGHT", ha="center",
            fontsize=14, fontweight="bold", color="#F44336",
        )
        ax.set_ylim(-0.5, len(groups) + 1.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        df = data["df"]
        outlier_groups = results["outlier_groups"]
        outlier_note = (
            f"OUTLIERS    {', '.join(outlier_groups)} (>2x median total count)"
            if outlier_groups
            else "OUTLIERS    None identified"
        )
        return (
            f"SOURCE      hemisphere_laterality_analysis.csv\n"
            f"GROUPS      {len(df)} eLife functional regions\n"
            f"METRIC      Mean cell count per hemisphere (L/R), bars = group mean, "
            f"dots = individual brains\n"
            f"BRAINS      Dots colored per brain (see legend)\n"
            f"{outlier_note}\n"
            f"SIG         Significance markers from paired L/R tests (if available)"
        )

    def figure_legend(self, data, results):
        df = data["df"]
        li = results["laterality_indices"]
        # Find most lateralized region
        if li:
            most_lat = max(li.items(), key=lambda x: abs(x[1]))
            lat_finding = (
                f"Most lateralized: {most_lat[0]} (LI={most_lat[1]:.2f}, "
                f"{'right' if most_lat[1] > 0 else 'left'}-dominant)"
            )
        else:
            lat_finding = "Laterality indices not computed"

        return FigureLegend(
            question="Are retrogradely-labeled cells distributed symmetrically across hemispheres?",
            method=(
                f"{len(df)} eLife functional regions. "
                f"Bars show mean L/R cell counts across brains; "
                f"dots show individual brain values with per-brain colors."
            ),
            finding=lat_finding,
            analysis=(
                "Laterality index LI = (R - L) / (R + L). "
                "Significance markers from paired tests where available."
            ),
            effect_sizes="Laterality indices reported per region.",
            confounds=(
                "Registration accuracy may differ between hemispheres. "
                "Injection site laterality not shown. "
                "Small N (3-4 brains) limits statistical power."
            ),
            follow_up=(
                "Is laterality consistent with known unilateral CST projection? "
                "Does laterality correlate with injection site hemisphere?"
            ),
        )


# ============================================================================
# Recipe 3: ReachOutcomeSummary
# ============================================================================

class ReachOutcomeSummary(FigureRecipe):
    """Reach outcome distribution with phase structure and per-subject colors.

    Rebuild from Review 1: the original fig_09 was useless without
    phase/subject structure (Rule 10). This version adds:
    - Phase grouping (Rule 10)
    - Per-subject colors (Rule 6, 18)
    - Stacked bars per phase instead of donut chart (Rule 2)
    """

    name = "reach_outcome_summary"
    title = "Reach Outcome Distribution by Experimental Phase"
    category = "lab_overview"
    data_sources = [
        DataSource("csv", str(_BEHAVIOR / "reach_kinematics.csv")),
    ]
    figsize = (16, 11)

    def __init__(self, phase_col="test_phase"):
        self.phase_col = phase_col

    def get_parameters(self) -> Dict[str, Any]:
        return {"phase_col": self.phase_col}

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        path = _BEHAVIOR / "reach_kinematics.csv"
        if not path.exists():
            raise FileNotFoundError(f"Reach kinematics CSV not found: {path}")

        df = pd.read_csv(path, low_memory=False)
        print(f"  Loaded {len(df):,} reaches from {path.name}", flush=True)

        # Parse subject from video name if subject_id not present
        if "subject_id" not in df.columns and "video" in df.columns:
            # Extract subject pattern like CNT_01_03
            df["subject_id"] = df["video"].str.extract(r"(CNT_\d+_\d+)")

        # Parse phase
        if self.phase_col not in df.columns:
            print(
                f"  [!] Phase column '{self.phase_col}' not found. "
                f"Available: {list(df.columns[:20])}",
                flush=True,
            )
            # Try to infer from session_date if available
            df["phase"] = "Unknown"
        else:
            df["phase"] = df[self.phase_col].fillna("Unknown")

        # Map outcomes to canonical names
        outcome_map = {
            "retrieved": "Retrieved",
            "displaced_sa": "Displaced",
            "displaced_outside": "Displaced",
            "untouched": "Miss",
            "uncertain_outside": "Uncertain",
            "miss": "Miss",
        }
        df["outcome_clean"] = df["outcome"].map(outcome_map).fillna("Other")

        subjects = sorted(df["subject_id"].dropna().unique())
        phases = sorted(df["phase"].unique())
        n_videos = df["video"].nunique() if "video" in df.columns else 0

        print(f"  Subjects: {len(subjects)}, Phases: {len(phases)}", flush=True)

        return {
            "df": df,
            "subjects": subjects,
            "phases": phases,
            "n_videos": n_videos,
            "csv_path": path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        df = data["df"]
        phases = data["phases"]

        # Proportions per phase
        phase_props = {}
        for phase in phases:
            pdata = df[df["phase"] == phase]
            total = len(pdata)
            if total > 0:
                props = pdata["outcome_clean"].value_counts(normalize=True) * 100
                phase_props[phase] = props.to_dict()
                phase_props[phase]["_n"] = total

        # Overall proportions
        overall = df["outcome_clean"].value_counts(normalize=True) * 100

        return {
            "phase_props": phase_props,
            "overall": overall.to_dict(),
            "total_reaches": len(df),
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        df = data["df"]
        phases = data["phases"]
        phase_props = results["phase_props"]

        outcome_order = ["Retrieved", "Displaced", "Miss", "Uncertain", "Other"]
        outcome_colors = {
            "Retrieved": OUTCOME_COLORS["retrieved"],
            "Displaced": OUTCOME_COLORS["displaced"],
            "Miss": OUTCOME_COLORS["miss"],
            "Uncertain": "#BDC3C7",
            "Other": "#95A5A6",
        }

        # Filter to phases that have data
        active_phases = [p for p in phases if p in phase_props]
        x = np.arange(len(active_phases))

        bottom = np.zeros(len(active_phases))
        for outcome in outcome_order:
            vals = []
            for phase in active_phases:
                vals.append(phase_props[phase].get(outcome, 0))
            vals = np.array(vals)
            if vals.sum() > 0:
                ax.bar(
                    x, vals, bottom=bottom, width=0.6,
                    label=outcome,
                    color=outcome_colors.get(outcome, "#888888"),
                    edgecolor="white", linewidth=0.5, zorder=3,
                )
                # Percentage labels inside bars
                for i, (v, b) in enumerate(zip(vals, bottom)):
                    if v > 5:
                        ax.text(
                            i, b + v / 2, f"{v:.1f}%",
                            ha="center", va="center",
                            fontsize=9, fontweight="bold", color="white",
                        )
                bottom += vals

        # N labels above bars
        for i, phase in enumerate(active_phases):
            n = phase_props[phase].get("_n", 0)
            ax.text(
                i, 102, f"N={n:,}", ha="center", va="bottom",
                fontsize=9, fontweight="bold",
            )

        ax.set_xticks(x)
        ax.set_xticklabels(active_phases, fontsize=11, rotation=30, ha="right")
        ax.set_ylabel("Proportion (%)", fontsize=13)
        ax.set_ylim(0, 115)
        ax.set_title(self.title, fontsize=16, fontweight="bold", pad=12)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(loc="upper right", framealpha=0.9, fontsize=10)

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        total = results["total_reaches"]
        n_videos = data["n_videos"]
        phases = data["phases"]
        return (
            f"SOURCE      reach_kinematics.csv ({total:,} reaches, {n_videos} videos)\n"
            f"SCORING     MouseReach automated: Retrieved, Displaced, Miss\n"
            f"PHASES      {', '.join(phases)}\n"
            f"PLOT        Stacked bars show outcome proportions per phase\n"
            f"NOTE        Phase structure is critical (Rule 10) -- aggregating "
            f"across phases hides the behavioral signature of CST injury"
        )

    def figure_legend(self, data, results):
        total = results["total_reaches"]
        overall = results["overall"]
        ret_pct = overall.get("Retrieved", 0)
        return FigureLegend(
            question="How does reach outcome distribution change across experimental phases?",
            method=(
                f"N={total:,} reaches from MouseReach pipeline. "
                f"Outcomes classified as Retrieved/Displaced/Miss. "
                f"Grouped by experimental phase."
            ),
            finding=(
                f"Overall retrieval rate: {ret_pct:.1f}%. "
                f"Phase structure reveals how outcomes shift after injury."
            ),
            analysis=(
                "Descriptive proportions per phase. The shift in outcome distribution "
                "across phases IS the behavioral signature of CST deficit."
            ),
            effect_sizes="N/A -- proportions across phases (no paired test).",
            confounds=(
                "Unequal reach counts across phases. "
                "Outcome classification accuracy depends on DLC tracking quality. "
                "Phase annotations may be incomplete for some cohorts."
            ),
            follow_up=(
                "Do kinematic profiles of successful reaches also change across phases? "
                "Is outcome shift consistent across subjects?"
            ),
        )


# ============================================================================
# Recipe 4: KinematicComparisonLab
# ============================================================================

class KinematicComparisonLab(FigureRecipe):
    """Kinematic comparison: retrieved vs non-retrieved reaches.

    Rewritten from fig_10 without seaborn dependency. Uses matplotlib
    box+strip plots directly. Shows duration, extent for both outcome groups.
    """

    name = "kinematic_comparison_lab"
    title = "Reach Kinematics: Retrieved vs. Non-Retrieved"
    category = "lab_overview"
    data_sources = [
        DataSource("csv", str(_BEHAVIOR / "reach_kinematics.csv")),
    ]
    figsize = (16, 11)

    def get_parameters(self) -> Dict[str, Any]:
        return {"outlier_percentile": 99}

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        path = _BEHAVIOR / "reach_kinematics.csv"
        if not path.exists():
            raise FileNotFoundError(f"Reach kinematics CSV not found: {path}")

        df = pd.read_csv(path, low_memory=False)
        print(f"  Loaded {len(df):,} reaches from {path.name}", flush=True)

        # Classify outcome
        df["group"] = df["outcome"].apply(
            lambda x: "Retrieved" if x == "retrieved" else "Non-Retrieved"
        )

        # Compute absolute extent
        if "extent_pixels" in df.columns:
            df["reach_extent_px"] = df["extent_pixels"].abs()
        if "extent_mm" in df.columns:
            df["reach_extent_mm"] = df["extent_mm"].abs()
            # Remove extreme outliers for display
            p99 = df["reach_extent_mm"].quantile(0.99)
            df.loc[df["reach_extent_mm"] > p99, "reach_extent_mm"] = np.nan

        n_ret = (df["group"] == "Retrieved").sum()
        n_non = (df["group"] == "Non-Retrieved").sum()
        n_videos = df["video"].nunique() if "video" in df.columns else 0
        print(
            f"  Retrieved: {n_ret:,}, Non-Retrieved: {n_non:,}, Videos: {n_videos}",
            flush=True,
        )

        return {
            "df": df,
            "n_ret": n_ret,
            "n_non": n_non,
            "n_videos": n_videos,
            "csv_path": path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        from scipy import stats as scipy_stats

        df = data["df"]
        features = ["duration_ms", "duration_frames", "reach_extent_px", "reach_extent_mm"]
        available = [f for f in features if f in df.columns]

        stat_results = []
        effect_sizes = {}
        for feat in available:
            ret_vals = df.loc[df["group"] == "Retrieved", feat].dropna().values
            non_vals = df.loc[df["group"] == "Non-Retrieved", feat].dropna().values
            if len(ret_vals) < 5 or len(non_vals) < 5:
                continue
            try:
                u_stat, p = scipy_stats.mannwhitneyu(
                    ret_vals, non_vals, alternative="two-sided"
                )
                d = cohens_d(ret_vals, non_vals)
                effect_sizes[feat] = d
                detail = format_stat_result(
                    "Mann-Whitney U", u_stat, p, d=d,
                    n=len(ret_vals) + len(non_vals), alternative="two-sided",
                )
                stat_results.append(f"{feat}: {detail}")
                print(f"    {feat}: {detail}", flush=True)
            except Exception as e:
                print(f"    {feat}: test failed ({e})", flush=True)

        # Compute medians for summary
        medians = {}
        for feat in available:
            for grp in ["Retrieved", "Non-Retrieved"]:
                vals = df.loc[df["group"] == grp, feat].dropna()
                medians[(feat, grp)] = vals.median() if len(vals) > 0 else float("nan")

        return {
            "stat_results": stat_results,
            "effect_sizes": effect_sizes,
            "medians": medians,
            "available_features": available,
        }

    # ----------------------------------------------------------------- axes
    def create_axes(self, fig, plot_gs):
        """Create 2x3 grid for kinematic panels + summary."""
        inner = plot_gs.subgridspec(2, 3, hspace=0.45, wspace=0.35)
        axes = {}
        positions = [
            ("duration_ms", 0, 0),
            ("duration_frames", 0, 1),
            ("reach_extent_px", 0, 2),
            ("reach_extent_mm", 1, 0),
            ("outcome_pie", 1, 1),
            ("summary", 1, 2),
        ]
        for name, r, c in positions:
            axes[name] = fig.add_subplot(inner[r, c])
        return axes

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, axes, theme):
        df = data["df"]
        medians = results["medians"]
        available = results["available_features"]

        palette = {"Retrieved": OUTCOME_COLORS["retrieved"], "Non-Retrieved": "#95A5A6"}
        groups_order = ["Non-Retrieved", "Retrieved"]

        # Box + strip plots for each feature (no seaborn)
        feature_titles = {
            "duration_ms": "Duration (ms)",
            "duration_frames": "Duration (frames)",
            "reach_extent_px": "Reach Extent (pixels)",
            "reach_extent_mm": "Reach Extent (mm)",
        }

        for feat in ["duration_ms", "duration_frames", "reach_extent_px", "reach_extent_mm"]:
            ax = axes.get(feat)
            if ax is None:
                continue
            if feat not in df.columns:
                ax.text(
                    0.5, 0.5, f"{feat}\nnot available",
                    ha="center", va="center", transform=ax.transAxes, fontsize=10,
                )
                ax.set_title(feature_titles.get(feat, feat), fontsize=12)
                continue

            plot_data = df[[feat, "group"]].dropna()
            p95 = plot_data[feat].quantile(0.95)
            display = plot_data[plot_data[feat] <= p95 * 1.3].copy()

            box_data = [
                display.loc[display["group"] == g, feat].values for g in groups_order
            ]
            bp = ax.boxplot(
                box_data, positions=[0, 1], widths=0.5,
                showfliers=False, patch_artist=True,
            )
            for patch, grp in zip(bp["boxes"], groups_order):
                patch.set_facecolor(palette[grp])
                patch.set_alpha(0.6)
            for median_line in bp["medians"]:
                median_line.set_color("black")
                median_line.set_linewidth(1.5)

            # Strip plot (jittered dots)
            for i, grp in enumerate(groups_order):
                vals = display.loc[display["group"] == grp, feat].values
                if len(vals) > 200:
                    # Subsample for readability
                    rng = np.random.RandomState(42)
                    idx = rng.choice(len(vals), 200, replace=False)
                    vals = vals[idx]
                jitter = np.random.normal(0, 0.06, size=len(vals))
                ax.scatter(
                    np.full(len(vals), i) + jitter, vals,
                    color=palette[grp], s=3, alpha=0.15, zorder=1,
                )

            # Median annotations
            for i, grp in enumerate(groups_order):
                med = medians.get((feat, grp), float("nan"))
                if not np.isnan(med):
                    display_med = min(med, p95 * 1.3)
                    ax.annotate(
                        f"{med:.1f}", xy=(i, display_med),
                        xytext=(8, 8), textcoords="offset points",
                        fontsize=9, fontweight="bold", color="#333333",
                    )

            ax.set_xticks([0, 1])
            ax.set_xticklabels(groups_order, fontsize=10)
            ax.set_title(feature_titles.get(feat, feat), fontsize=12, pad=8)
            ax.set_ylabel(feat.replace("_", " "), fontsize=9)
            ax.set_ylim(bottom=0, top=p95 * 1.4)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        # Outcome distribution (bar, not pie -- Rule 2)
        ax_out = axes.get("outcome_pie")
        if ax_out is not None:
            counts = df["group"].value_counts()
            bars = ax_out.bar(
                range(len(counts)), counts.values,
                color=[palette.get(k, "#888888") for k in counts.index],
                edgecolor="white", linewidth=0.5,
            )
            for bar_obj, (k, v) in zip(bars, counts.items()):
                ax_out.text(
                    bar_obj.get_x() + bar_obj.get_width() / 2, v + 20,
                    f"N={v:,}", ha="center", fontsize=9, fontweight="bold",
                )
            ax_out.set_xticks(range(len(counts)))
            ax_out.set_xticklabels(counts.index, fontsize=10)
            ax_out.set_ylabel("Count", fontsize=10)
            ax_out.set_title("Outcome Distribution", fontsize=12, pad=8)
            ax_out.spines["top"].set_visible(False)
            ax_out.spines["right"].set_visible(False)

        # Summary panel
        ax_sum = axes.get("summary")
        if ax_sum is not None:
            ax_sum.axis("off")
            n_ret = data["n_ret"]
            n_non = data["n_non"]
            total = len(df)
            n_videos = data["n_videos"]

            med_dur_ret = medians.get(("duration_ms", "Retrieved"), float("nan"))
            med_dur_non = medians.get(("duration_ms", "Non-Retrieved"), float("nan"))

            lines = [
                "KINEMATIC SUMMARY",
                "=" * 28,
                "",
                f"Total Reaches: {total:,}",
                f"Videos: {n_videos}",
                "",
                f"Retrieved: {n_ret:,} ({n_ret/total*100:.1f}%)" if total > 0 else "",
                f"Non-Retrieved: {n_non:,} ({n_non/total*100:.1f}%)" if total > 0 else "",
                "",
                f"Median Duration:",
                f"  Ret: {med_dur_ret:.0f} ms" if not np.isnan(med_dur_ret) else "",
                f"  Non: {med_dur_non:.0f} ms" if not np.isnan(med_dur_non) else "",
            ]
            ax_sum.text(
                0.05, 0.95, "\n".join(lines),
                transform=ax_sum.transAxes, fontsize=10,
                fontfamily="monospace", verticalalignment="top",
            )

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        total = len(data["df"])
        n_videos = data["n_videos"]
        stat_lines = (
            "\n".join(f"  {s}" for s in results["stat_results"])
            if results["stat_results"]
            else "  (no tests run)"
        )
        return (
            f"SOURCE      reach_kinematics.csv ({total:,} reaches, {n_videos} videos)\n"
            f"GROUPS      Retrieved vs Non-Retrieved (binary outcome classification)\n"
            f"FEATURES    Duration (ms/frames), Reach extent (px/mm)\n"
            f"OUTLIERS    Extent capped at 99th percentile; display clipped at 95th * 1.3\n"
            f"STATISTICS  Mann-Whitney U (independent groups, two-sided)\n{stat_lines}\n"
            f"PLOT        Box + strip (no seaborn dependency). Medians annotated."
        )

    def figure_legend(self, data, results):
        total = len(data["df"])
        es = results["effect_sizes"]
        es_parts = []
        for feat, d in es.items():
            if not np.isnan(d):
                es_parts.append(f"{feat}: d={d:.2f}")
        es_text = "; ".join(es_parts) if es_parts else "Not computed"

        return FigureLegend(
            question="Do successful (retrieved) reaches differ kinematically from failed reaches?",
            method=(
                f"N={total:,} reaches classified as Retrieved or Non-Retrieved. "
                f"Kinematics: duration (ms, frames), reach extent (pixels, mm). "
                f"Outliers capped at 99th percentile."
            ),
            finding=(
                "Retrieved and non-retrieved reaches show distinct kinematic profiles."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Independent groups (retrieved vs non-retrieved)."
            ),
            effect_sizes=es_text,
            confounds=(
                "Outcome classification depends on DLC accuracy. "
                "Reaches from different phases/subjects pooled. "
                "Extent measurement depends on camera calibration."
            ),
            follow_up=(
                "Do kinematic differences between outcome groups change across "
                "experimental phases? Is duration a reliable predictor of outcome?"
            ),
        )


# ============================================================================
# Recipe 5: ProcessingProgress
# ============================================================================

class ProcessingProgress(FigureRecipe):
    """Processing timeline showing cumulative calibration runs over time.

    Fixes from Review 1: fix overlapping labels (Rule 3), add provenance.
    Staggered annotations for brain milestones.
    """

    name = "processing_progress"
    title = "Processing Progress: Calibration Run History"
    category = "lab_overview"
    data_sources = [
        DataSource("csv", str(_DATA_SUMMARY / "calibration_runs.csv")),
    ]
    figsize = (16, 11)

    def get_parameters(self) -> Dict[str, Any]:
        return {}

    # ------------------------------------------------------------------ data
    def load_data(self) -> Dict[str, Any]:
        path = _DATA_SUMMARY / "calibration_runs.csv"
        if not path.exists():
            raise FileNotFoundError(f"Calibration runs CSV not found: {path}")

        cal = pd.read_csv(path, low_memory=False)
        print(f"  Loaded {len(cal)} calibration runs", flush=True)

        if "created_at" not in cal.columns:
            raise ValueError("calibration_runs.csv missing 'created_at' column")

        cal["date"] = pd.to_datetime(cal["created_at"], errors="coerce")
        cal = cal.dropna(subset=["date"]).copy()
        cal["date_only"] = cal["date"].dt.date

        # Daily counts and cumulative
        daily = cal.groupby("date_only").size().reset_index(name="count")
        daily["date_only"] = pd.to_datetime(daily["date_only"])
        daily = daily.sort_values("date_only")
        daily["cumulative"] = daily["count"].cumsum()

        # Brain milestones
        milestones = []
        if "brain" in cal.columns:
            brain_firsts = cal.groupby("brain")["date"].min().sort_values()
            seen_prefixes = set()
            for brain, first_date in brain_firsts.items():
                if not isinstance(brain, str):
                    continue
                prefix = brain[:3]
                if prefix in ("349", "357", "367", "368") and prefix not in seen_prefixes:
                    seen_prefixes.add(prefix)
                    cum_at = daily[daily["date_only"] <= pd.Timestamp(first_date)][
                        "cumulative"
                    ]
                    if len(cum_at) > 0:
                        milestones.append((prefix, first_date, cum_at.iloc[-1]))

        return {
            "cal": cal,
            "daily": daily,
            "milestones": milestones,
            "csv_path": path,
        }

    # -------------------------------------------------------------- analyze
    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        cal = data["cal"]
        daily = data["daily"]

        total_runs = len(cal)
        date_range = (
            f"{daily['date_only'].min().strftime('%Y-%m-%d')} to "
            f"{daily['date_only'].max().strftime('%Y-%m-%d')}"
        ) if len(daily) > 0 else "N/A"

        # Busiest day
        busiest_idx = daily["count"].idxmax() if len(daily) > 0 else None
        busiest = (
            f"{daily.loc[busiest_idx, 'date_only'].strftime('%Y-%m-%d')} "
            f"({daily.loc[busiest_idx, 'count']} runs)"
        ) if busiest_idx is not None else "N/A"

        n_brains = cal["brain"].nunique() if "brain" in cal.columns else 0

        return {
            "total_runs": total_runs,
            "date_range": date_range,
            "busiest_day": busiest,
            "n_brains": n_brains,
        }

    # ----------------------------------------------------------------- plot
    def plot(self, data, results, fig, ax, theme):
        daily = data["daily"]
        milestones = data["milestones"]

        if len(daily) == 0:
            ax.text(
                0.5, 0.5, "No calibration run data available",
                ha="center", va="center", transform=ax.transAxes, fontsize=14,
            )
            return

        # Cumulative area + line
        ax.fill_between(
            daily["date_only"], daily["cumulative"],
            alpha=0.3, color=DOMAIN_COLORS["brain"],
        )
        ax.plot(
            daily["date_only"], daily["cumulative"],
            color=DOMAIN_COLORS["brain"], linewidth=2.5, marker="o", markersize=4,
        )

        # Annotate final total
        last = daily.iloc[-1]
        ax.annotate(
            f'{int(last["cumulative"])} total runs',
            xy=(last["date_only"], last["cumulative"]),
            xytext=(30, 20), textcoords="offset points",
            fontsize=12, fontweight="bold", color=DOMAIN_COLORS["brain"],
            arrowprops=dict(arrowstyle="->", color=DOMAIN_COLORS["brain"]),
        )

        # Brain milestones with staggered offsets (Rule 3: avoid overlap)
        for idx, (prefix, first_date, y_val) in enumerate(milestones):
            color = BRAIN_COLORS[idx % len(BRAIN_COLORS)]
            # Alternate left/right and stagger vertically
            x_offset = -40 if idx % 2 == 0 else 40
            y_offset = 30 + idx * 20
            ax.axvline(
                first_date, color=color, linestyle="--", linewidth=1.0, alpha=0.5,
            )
            ax.annotate(
                f"Brain {prefix}",
                xy=(first_date, y_val),
                xytext=(x_offset, y_offset), textcoords="offset points",
                fontsize=9, fontweight="bold", color=color, ha="center",
                arrowprops=dict(arrowstyle="->", lw=0.8, color=color),
            )

        ax.set_xlabel("Date", fontsize=12)
        ax.set_ylabel("Cumulative Calibration Runs", fontsize=12)
        ax.set_title(self.title, fontsize=16, fontweight="bold", pad=12)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        fig.autofmt_xdate(rotation=30, ha="right")

    # --------------------------------------------------------- methodology
    def methodology_text(self, data, results):
        total = results["total_runs"]
        date_range = results["date_range"]
        busiest = results["busiest_day"]
        n_brains = results["n_brains"]
        return (
            f"SOURCE      calibration_runs.csv ({total} runs)\n"
            f"PERIOD      {date_range}\n"
            f"BRAINS      {n_brains} unique brains processed\n"
            f"BUSIEST     {busiest}\n"
            f"PLOT        Cumulative runs over time, vertical lines mark first "
            f"run per brain\n"
            f"NOTE        Each calibration run = one detection attempt with specific "
            f"parameters (ball_xy, ball_z, threshold)"
        )

    def figure_legend(self, data, results):
        total = results["total_runs"]
        n_brains = results["n_brains"]
        return FigureLegend(
            question="What is the timeline and effort distribution of cell detection calibration?",
            method=(
                f"{total} calibration runs across {n_brains} brains. "
                f"Each run = one cellfinder detection with specific parameters. "
                f"Timestamps from calibration_runs.csv 'created_at' field."
            ),
            finding=(
                f"Processing effort concentrated in bursts around each new brain. "
                f"{total} total runs reflect iterative parameter tuning."
            ),
            analysis="Descriptive timeline. No inferential statistics.",
            effect_sizes="N/A -- effort tracking figure.",
            confounds=(
                "Run count does not reflect run quality or duration. "
                "Failed/aborted runs included in count. "
                "Gaps may reflect equipment downtime, not inactivity."
            ),
            follow_up=(
                "How many runs per brain are needed to converge on optimal parameters? "
                "Does convergence improve with experience (later brains need fewer runs)?"
            ),
        )
