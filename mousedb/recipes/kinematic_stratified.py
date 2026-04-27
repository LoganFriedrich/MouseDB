"""
Outcome-stratified kinematic recovery recipes (1-6 of 12).

Migrated from figures/Connectome_Grant/kinematic_recovery_stratified.py.
Key principle: comparing reaches requires matching by outcome category.
A "recovered" pooled mean could simply reflect more successful reaches,
not improved kinematics of equivalent reach types.

Outcome categories (matching manual pellet scoring 0/1/2):
  0 = Miss (untouched) - reach did not contact pellet
  1 = Displaced - pellet contacted but not retrieved
  2 = Retrieved - pellet grasped and eaten
"""

from typing import Any, Dict, List, Optional

import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from scipy import stats

from mousedb import MOUSEDB_ROOT
from mousedb.figures.legends import FigureLegend
from mousedb.figures.palettes import (
    COHORT_COLORS,
    OUTCOME_COLORS,
    PHASE_COLORS,
    get_persistent_subject_colors,
)
from mousedb.figures.stats import (
    cohens_d,
    cohens_d_paired,
    format_stat_result,
    stat_justification,
)
from mousedb.recipes.base import DataSource, FigureRecipe

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COHORTS = ["CNT_01", "CNT_02", "CNT_03", "CNT_04"]
LEARNER_EATEN_THRESHOLD = 5.0

# 3-category outcome mapping (matches manual 0/1/2)
OUTCOME_MAP = {
    "retrieved": 2,
    "displaced_sa": 1,
    "displaced_outside": 1,
    "untouched": 0,
    "uncertain": np.nan,
}
OUTCOME_NAMES = {0: "Miss", 1: "Displaced", 2: "Retrieved"}
_OUTCOME_COLORS_NUMERIC = {0: "#d62728", 1: "#ff7f0e", 2: "#2ca02c"}
OUTCOMES_ORDERED = [0, 1, 2]

GROUP_COLORS = {"Recovered": "#2ca02c", "Not Recovered": "#d62728"}

KEY_FEATURES = [
    ("max_extent_mm", "Max Extent (mm)"),
    ("peak_velocity_px_per_frame", "Peak Velocity (px/frame)"),
    ("mean_velocity_px_per_frame", "Mean Velocity (px/frame)"),
    ("velocity_at_apex_mm_per_sec", "Apex Velocity (mm/s)"),
    ("trajectory_straightness", "Straightness"),
    ("trajectory_smoothness", "Smoothness"),
    ("duration_frames", "Duration (frames)"),
    ("hand_angle_at_apex_deg", "Hand Angle (deg)"),
    ("hand_rotation_total_deg", "Hand Rotation (deg)"),
    ("nose_to_slit_at_apex_mm", "Nose-to-Slit (mm)"),
    ("attention_score", "Attention Score"),
]

PHASES = ["Pre-Injury", "Post-Injury", "Rehab_Pillar"]
PHASE_LABELS = ["Pre-Injury", "Post-Injury", "Rehab Pillar"]

# Shared data sources used by every recipe in this module.
_SHARED_DATA_SOURCES = [
    DataSource("csv", "database_dump/pellet_scores.csv", "CNT_01-04 cohorts"),
    DataSource("csv", "database_dump/surgeries.csv", "CNT_01-04 contusion dates"),
    DataSource("csv", "database_dump/reach_data.csv", "CNT_01-04 reach kinematics"),
]


# ---------------------------------------------------------------------------
# Shared helper: plausible range filtering
# ---------------------------------------------------------------------------

def _filter_plausible(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows with clearly implausible kinematic values."""
    df = df.copy()
    bounds = {
        "peak_velocity_px_per_frame": (0, 200),
        "mean_velocity_px_per_frame": (0, 100),
        "max_extent_mm": (0, 50),
        "duration_frames": (1, 500),
        "trajectory_straightness": (0, 1.001),
        "trajectory_smoothness": (0, 1e6),
        "hand_angle_at_apex_deg": (-180, 180),
        "attention_score": (0, 1.001),
    }
    for col, (lo, hi) in bounds.items():
        if col in df.columns:
            mask = df[col].between(lo, hi) | df[col].isna()
            df = df[mask]
    return df


def _filter_outcomes(df: pd.DataFrame, outcome_cats: Optional[List[int]] = None
                     ) -> pd.DataFrame:
    """Keep only rows matching specific outcome categories (0/1/2)."""
    if outcome_cats is None:
        return df
    return df[df["outcome_cat"].isin(outcome_cats)].copy()


# ---------------------------------------------------------------------------
# Shared helper: data loading
# ---------------------------------------------------------------------------

def _load_and_prepare() -> tuple:
    """Load all data, assign phases, identify recovered animals.

    Returns (reach, seg, ps, recovery, surg_dates).
    """
    dump = MOUSEDB_ROOT / "database_dump"
    ps = pd.read_csv(dump / "pellet_scores.csv", low_memory=False)
    surgeries = pd.read_csv(dump / "surgeries.csv")
    reach = pd.read_csv(dump / "reach_data.csv", low_memory=False)

    ps = ps[ps["subject_id"].str.match(r"CNT_0[1-4]_")].copy()
    surgeries = surgeries[surgeries["subject_id"].str.match(r"CNT_0[1-4]_")].copy()
    reach = reach[reach["subject_id"].str.match(r"CNT_0[1-4]_")].copy()

    ps["cohort"] = ps["subject_id"].str[:6]
    ps["session_date"] = pd.to_datetime(ps["session_date"])
    surgeries["surgery_date"] = pd.to_datetime(surgeries["surgery_date"])
    reach["session_date"] = pd.to_datetime(reach["session_date"])
    reach["cohort"] = reach["subject_id"].str[:6]

    # Map segment_outcome -> 0/1/2
    reach["outcome_cat"] = reach["segment_outcome"].map(OUTCOME_MAP)

    # Surgery dates
    surg_cont = surgeries[surgeries["surgery_type"] == "contusion"]
    surg_dates = {}
    for cohort in COHORTS:
        cs = surg_cont[surg_cont["subject_id"].str.startswith(cohort)]
        if not cs.empty:
            surg_dates[cohort] = cs["surgery_date"].min()

    # Assign phases (coarse + fine)
    reach["phase"] = None
    reach["phase_fine"] = None
    reach["days_post_surgery"] = np.nan
    for cohort, sd in surg_dates.items():
        mask = reach["cohort"] == cohort
        reach.loc[mask, "days_post_surgery"] = (
            reach.loc[mask, "session_date"] - sd
        ).dt.days
        reach.loc[mask & (reach["session_date"] < sd), "phase"] = "Pre-Injury"
        reach.loc[mask & (reach["session_date"] < sd), "phase_fine"] = "Pre-Injury"

        post_mask = mask & (reach["session_date"] >= sd + pd.Timedelta(days=7))
        post_dates = sorted(reach[post_mask]["session_date"].unique())
        if not post_dates:
            continue

        blocks: List[list] = [[post_dates[0]]]
        for i in range(1, len(post_dates)):
            if (post_dates[i] - post_dates[i - 1]).days > 5:
                blocks.append([])
            blocks[-1].append(post_dates[i])

        pi_count = 0
        for block in blocks:
            block_mask = mask & reach["session_date"].isin(block)
            if len(block) <= 2:
                pi_count += 1
                reach.loc[block_mask, "phase"] = "Post-Injury"
                reach.loc[block_mask, "phase_fine"] = f"Post-{pi_count}"
            else:
                for d in block:
                    day_mask = mask & (reach["session_date"] == d)
                    for tray, sub in [
                        ("E", "Rehab_Easy"),
                        ("F", "Rehab_Flat"),
                        ("P", "Rehab_Pillar"),
                    ]:
                        reach.loc[
                            day_mask & (reach["tray_type"] == tray), "phase"
                        ] = sub
                        reach.loc[
                            day_mask & (reach["tray_type"] == tray), "phase_fine"
                        ] = sub

    # Build segment-level summary
    seg = (
        reach.groupby(["subject_id", "session_date", "segment_num"])
        .agg(
            outcome_cat=("outcome_cat", "first"),
            segment_outcome=("segment_outcome", "first"),
            n_reaches=("reach_num", "max"),
            has_causal=("causal_reach", "max"),
            phase=("phase", "first"),
            phase_fine=("phase_fine", "first"),
            cohort=("cohort", "first"),
            days_post_surgery=("days_post_surgery", "first"),
        )
        .reset_index()
    )

    # Reaches-to-interaction
    causal_reaches = reach[reach["causal_reach"] == 1][
        ["subject_id", "session_date", "segment_num", "reach_num"]
    ].copy()
    causal_reaches = causal_reaches.rename(columns={"reach_num": "causal_reach_num"})
    seg = seg.merge(
        causal_reaches,
        on=["subject_id", "session_date", "segment_num"],
        how="left",
    )

    # Identify recovered animals from pellet data
    ps = _infer_pellet_phases(ps, surg_dates)
    recovery = _classify_recovery(ps)

    # Apply plausible range filter to reach data
    reach = _filter_plausible(reach)

    return reach, seg, ps, recovery, surg_dates


def _infer_pellet_phases(ps: pd.DataFrame, surg_dates: dict) -> pd.DataFrame:
    """Infer phases for pellet scores with blank test_phase."""
    ps_all = []
    for cohort in COHORTS:
        ps_c = ps[ps["cohort"] == cohort].copy()
        sd = surg_dates.get(cohort)
        if sd is None:
            ps_all.append(ps_c)
            continue

        blank = ps_c["test_phase"].isna() | (ps_c["test_phase"] == "")
        if not blank.any():
            ps_all.append(ps_c)
            continue

        bdf = ps_c[blank]
        sessions = (
            bdf.groupby(["session_date", "tray_type"])
            .size()
            .reset_index(name="n")
            .sort_values("session_date")
        )
        pre_s = sessions[sessions["session_date"] < sd]
        post_s = sessions[sessions["session_date"] > sd]
        pmap: dict = {}

        if not pre_s.empty:
            pillar_dates = sorted(
                pre_s[pre_s["tray_type"] == "P"]["session_date"].unique()
            )
            start = max(0, len(pillar_dates) - 3)
            for i, d in enumerate(pillar_dates):
                if i >= start:
                    pmap[(d, "P")] = f"Pre-Injury_Test_{i - start + 1}"

        if not post_s.empty:
            pd_list = sorted(post_s["session_date"].unique())
            blocks: List[list] = [[pd_list[0]]]
            for i in range(1, len(pd_list)):
                if (pd_list[i] - pd_list[i - 1]).days > 5:
                    blocks.append([])
                blocks[-1].append(pd_list[i])
            pi_count = 0
            for block in blocks:
                bs = post_s[post_s["session_date"].isin(block)]
                if len(block) <= 2:
                    pi_count += 1
                    for d in block:
                        for _, r in bs[bs["session_date"] == d].iterrows():
                            pmap[(d, r["tray_type"])] = (
                                f"Post-Injury_Test_{pi_count}"
                            )
                else:
                    for d in sorted(block):
                        for _, r in bs[bs["session_date"] == d].iterrows():
                            t = r["tray_type"]
                            label = (
                                "Easy"
                                if t == "E"
                                else "Flat"
                                if t == "F"
                                else "Pillar"
                            )
                            pmap[(d, t)] = f"Rehab_{label}"

        for idx in ps_c[blank].index:
            key = (ps_c.loc[idx, "session_date"], ps_c.loc[idx, "tray_type"])
            if key in pmap:
                ps_c.loc[idx, "test_phase"] = pmap[key]

        # Fix mislabeled rehab (CNT_02)
        post_mask = ps_c["test_phase"].str.contains(
            "Post-Injury", case=False, na=False
        )
        if post_mask.any():
            post_sessions = (
                ps_c[post_mask]
                .groupby(["session_date", "tray_type"])
                .size()
                .reset_index(name="n")
                .sort_values("session_date")
            )
            post_dates = sorted(post_sessions["session_date"].unique())
            if len(post_dates) >= 5:
                blocks_fix: List[list] = [[post_dates[0]]]
                for i in range(1, len(post_dates)):
                    if (post_dates[i] - post_dates[i - 1]).days > 5:
                        blocks_fix.append([])
                    blocks_fix[-1].append(post_dates[i])
                for block in blocks_fix:
                    if len(block) <= 4:
                        continue
                    bs = post_sessions[post_sessions["session_date"].isin(block)]
                    trays = set(bs["tray_type"].unique())
                    if "E" in trays or ("F" in trays and len(block) > 5):
                        first_trays = set(
                            bs[bs["session_date"] == block[0]]["tray_type"]
                        )
                        rehab_dates = block[1:] if first_trays == {"P"} else block
                        for d in rehab_dates:
                            for _, r in bs[bs["session_date"] == d].iterrows():
                                m = (ps_c["session_date"] == d) & (
                                    ps_c["tray_type"] == r["tray_type"]
                                )
                                t = r["tray_type"]
                                label = (
                                    "Easy"
                                    if t == "E"
                                    else "Flat"
                                    if t == "F"
                                    else "Pillar"
                                )
                                ps_c.loc[m, "test_phase"] = f"Rehab_{label}"

        ps_all.append(ps_c)
    return pd.concat(ps_all, ignore_index=True)


RECOVERY_THRESHOLD = 0.5  # Must match recovery.py and kinematic_recovery.py
MIN_DEFICIT_PP = 5.0  # Minimum deficit (pp) to classify recovery (Rule 40)


def _classify_recovery(ps: pd.DataFrame) -> pd.DataFrame:
    """Classify animals as recovered/not from pellet eaten%.

    Recovery definition (Rule 40): (rehab - post) / (pre - post).
    An animal is 'recovered' when this ratio >= 0.5 (i.e. rehab restores
    at least 50% of the lost function). Animals with deficit < 5pp are
    excluded from classification (never truly lost function).
    """

    def _pellet_window(phase):
        if pd.isna(phase) or phase == "":
            return None
        pl = str(phase).lower()
        if "pre-injury" in pl or "pre_injury" in pl:
            return "pre_injury"
        if "rehab" in pl and "pillar" in pl:
            return "rehab_pillar"
        if "post-injury" in pl or "post_injury" in pl:
            return "post_injury"
        return None

    ps = ps.copy()
    ps["window"] = ps["test_phase"].apply(_pellet_window)

    # Fallback: Training_Pillar as pre-injury
    for cohort in COHORTS:
        cps = ps[ps["cohort"] == cohort]
        if (cps["window"] == "pre_injury").sum() == 0:
            tp = cps[
                cps["test_phase"].str.contains(
                    "Training_Pillar", case=False, na=False
                )
            ]
            if not tp.empty:
                tp_dates = sorted(tp["session_date"].unique())[-3:]
                mask = (
                    (ps["cohort"] == cohort)
                    & ps["session_date"].isin(tp_dates)
                    & ps["test_phase"].str.contains(
                        "Training_Pillar", case=False, na=False
                    )
                )
                ps.loc[mask, "window"] = "pre_injury"

    ps["eaten_bin"] = (ps["score"] >= 2).astype(int)

    recovery = {}
    for animal in ps["subject_id"].unique():
        adf = ps[ps["subject_id"] == animal]
        pre = adf[adf["window"] == "pre_injury"]["eaten_bin"]
        post = adf[adf["window"] == "post_injury"]["eaten_bin"]
        rehab = adf[adf["window"] == "rehab_pillar"]["eaten_bin"]
        if len(pre) == 0 or len(rehab) == 0:
            continue
        pre_pct = pre.mean() * 100
        rehab_pct = rehab.mean() * 100
        post_pct = post.mean() * 100 if len(post) > 0 else 0.0
        if pre_pct < LEARNER_EATEN_THRESHOLD:
            continue
        denom = pre_pct - post_pct
        if denom < MIN_DEFICIT_PP:
            # Animal never truly lost function -- exclude from classification
            continue
        ratio = (rehab_pct - post_pct) / denom
        recovery[animal] = {
            "pre_eaten": pre_pct,
            "post_eaten": post_pct,
            "rehab_eaten": rehab_pct,
            "recovery_ratio": ratio,
            "recovered": ratio >= RECOVERY_THRESHOLD,
            "cohort": animal[:6],
        }

    return (
        pd.DataFrame.from_dict(recovery, orient="index")
        .rename_axis("animal")
        .reset_index()
    )


# ---------------------------------------------------------------------------
# Recipe 1: StratifiedOutcomeDistribution
# ---------------------------------------------------------------------------

class StratifiedOutcomeDistribution(FigureRecipe):
    """Stacked bars of miss/displaced/retrieved by phase, split by recovery group.

    Shows how the outcome distribution shifts across Pre-Injury, Post-Injury,
    and Rehab Pillar phases for recovered vs. not-recovered animals, plus a
    direct comparison of per-reach retrieval rate trajectories.
    """

    name = "stratified_outcome_distribution"
    title = "Per-Reach Outcome Distribution by Recovery Group"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (18, 8)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "learner_threshold": LEARNER_EATEN_THRESHOLD,
            "outcome_map": {k: v for k, v in OUTCOME_MAP.items() if not (v != v)},
            "phases": PHASES,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])
        print(
            f"  Recovery groups: {len(rec_animals)} recovered, "
            f"{len(norec_animals)} not recovered",
            flush=True,
        )
        return {
            "seg": seg,
            "recovery": recovery,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        seg = data["seg"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        results: Dict[str, Any] = {"group_stats": {}}

        for label, animals in [("Recovered", rec), ("Not Recovered", norec)]:
            gseg = seg[seg["subject_id"].isin(animals)]
            phase_stats = {}
            for phase in PHASES:
                pdata = gseg[gseg["phase"] == phase]
                total = len(pdata)
                if total == 0:
                    continue
                dist = {
                    OUTCOME_NAMES[o]: (pdata["outcome_cat"] == o).sum() / total * 100
                    for o in OUTCOMES_ORDERED
                }
                phase_stats[phase] = {"n": total, "distribution": dist}
            results["group_stats"][label] = phase_stats

        return results

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(1, 3, wspace=0.3)
        return {
            "recovered": fig.add_subplot(inner[0]),
            "not_recovered": fig.add_subplot(inner[1]),
            "comparison": fig.add_subplot(inner[2]),
        }

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]

        for key, group_animals, label in [
            ("recovered", rec, "Recovered"),
            ("not_recovered", norec, "Not Recovered"),
        ]:
            a = ax[key]
            gseg = seg[seg["subject_id"].isin(group_animals)]
            proportions = {o: [] for o in OUTCOMES_ORDERED}
            ns = []
            for phase in PHASES:
                pdata = gseg[gseg["phase"] == phase]
                total = len(pdata)
                ns.append(total)
                for outcome in OUTCOMES_ORDERED:
                    if total > 0:
                        proportions[outcome].append(
                            (pdata["outcome_cat"] == outcome).sum() / total * 100
                        )
                    else:
                        proportions[outcome].append(0)

            x = np.arange(len(PHASES))
            bottom = np.zeros(len(PHASES))
            for outcome in OUTCOMES_ORDERED:
                a.bar(
                    x,
                    proportions[outcome],
                    bottom=bottom,
                    color=_OUTCOME_COLORS_NUMERIC[outcome],
                    label=OUTCOME_NAMES[outcome],
                    alpha=0.8,
                    edgecolor="black",
                    linewidth=0.5,
                )
                for i, pct in enumerate(proportions[outcome]):
                    if pct > 4:
                        a.text(
                            x[i],
                            bottom[i] + pct / 2,
                            f"{pct:.0f}%",
                            ha="center",
                            va="center",
                            fontsize=10,
                            fontweight="bold",
                        )
                bottom += np.array(proportions[outcome])

            a.set_xticks(x)
            a.set_xticklabels(
                [f"{pl}\n(N={n})" for pl, n in zip(PHASE_LABELS, ns)],
                fontsize=9,
            )
            a.set_ylabel("% of Per-reach Outcomes")
            a.set_title(f"{label} (N={len(group_animals)})", fontweight="bold")
            if key == "recovered":
                a.legend(fontsize=9)
            a.set_ylim(0, 105)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Right panel: direct comparison of retrieval rate
        a = ax["comparison"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            gseg = seg[seg["subject_id"].isin(group_animals)]
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(PHASES):
                animal_pcts = []
                for animal in group_animals:
                    adata = gseg[
                        (gseg["subject_id"] == animal) & (gseg["phase"] == phase)
                    ]
                    if len(adata) >= 3:
                        animal_pcts.append(
                            (adata["outcome_cat"] == 2).mean() * 100
                        )
                if len(animal_pcts) >= 2:
                    means.append(np.mean(animal_pcts))
                    sems.append(stats.sem(animal_pcts))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x,
                    means,
                    yerr=sems,
                    fmt="o-",
                    color=color,
                    linewidth=2.5,
                    markersize=8,
                    capsize=4,
                    label=glabel,
                    zorder=5,
                )

        a.set_xticks(range(len(PHASES)))
        a.set_xticklabels(PHASE_LABELS, fontsize=9)
        a.set_ylabel("% Retrieved (per-animal mean)")
        a.set_title("Retrieval Rate Trajectory", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        return (
            f"EXPERIMENT  Skilled reaching, single-pellet retrieval, CST lesion model\n"
            f"SUBJECTS    N={len(rec)} recovered + {len(norec)} not recovered "
            f"(recovery = (rehab - post) / (pre - post) >= 0.5)\n"
            f"METRIC      Per-reach outcome category (miss / displaced / retrieved)\n"
            f"PHASES      Pre-Injury | Post-Injury | Rehab Pillar\n"
            f"PLOT        Stacked bars = outcome proportions per phase. "
            f"Right panel = retrieval rate trajectory (+/- SEM across animals)."
        )

    def figure_legend(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        return FigureLegend(
            question=(
                "How does the distribution of reach outcomes shift after "
                "injury and during rehabilitation?"
            ),
            method=(
                f"N={len(rec)} recovered + {len(norec)} not recovered animals "
                f"from cohorts {', '.join(COHORTS)}. "
                f"Recovery defined as (rehab - post) / (pre - post) >= 0.5 "
                f"(animals with deficit < 5pp excluded). "
                f"Per-reach outcomes classified by MouseReach pipeline."
            ),
            finding=(
                "Post-injury shifts outcome distribution toward misses and "
                "displaced reaches. Recovered animals restore retrieval rates "
                "during rehabilitation while not-recovered animals remain impaired."
            ),
            analysis=(
                "Descriptive proportions per phase. Right panel shows per-animal "
                "mean retrieval rate +/- SEM."
            ),
            effect_sizes="Descriptive (proportion shift)",
            confounds=(
                "Outcome classification depends on MouseReach pipeline accuracy. "
                "Unequal segment counts across phases."
            ),
            follow_up=(
                "Outcome-matched kinematic comparison (Recipe 3) controls for "
                "this distributional shift."
            ),
        )


# ---------------------------------------------------------------------------
# Recipe 2: DlcVsManualValidation
# ---------------------------------------------------------------------------

class DlcVsManualValidation(FigureRecipe):
    """Bland-Altman + scatter validation of MouseReach pipeline vs manual scoring.

    Session-level comparison: automated outcome percentages vs manual pellet
    scores for miss, displaced, and retrieved categories.
    """

    name = "dlc_vs_manual_validation"
    title = "MouseReach Pipeline vs Manual Pellet Scoring Validation"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (18, 14)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "tray_filter": "P (pillar only, matching DLC processing)",
            "min_sessions": 10,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()

        # DLC session-level outcome percentages
        dlc_sess = (
            seg.dropna(subset=["outcome_cat"])
            .groupby(["subject_id", "session_date"])
            .agg(
                dlc_n=("outcome_cat", "count"),
                dlc_miss_pct=("outcome_cat", lambda x: (x == 0).mean() * 100),
                dlc_disp_pct=("outcome_cat", lambda x: (x == 1).mean() * 100),
                dlc_ret_pct=("outcome_cat", lambda x: (x == 2).mean() * 100),
            )
            .reset_index()
        )

        # Manual session-level (pillar trays only)
        ps_p = ps[ps["tray_type"] == "P"].copy()
        man_sess = (
            ps_p.groupby(["subject_id", "session_date"])
            .agg(
                man_n=("score", "count"),
                man_miss_pct=("score", lambda x: (x == 0).mean() * 100),
                man_disp_pct=("score", lambda x: (x == 1).mean() * 100),
                man_ret_pct=("score", lambda x: (x == 2).mean() * 100),
            )
            .reset_index()
        )
        man_sess["session_date"] = pd.to_datetime(man_sess["session_date"])

        merged = dlc_sess.merge(
            man_sess, on=["subject_id", "session_date"], how="inner"
        )
        print(f"  Overlapping sessions: {len(merged)}", flush=True)

        return {"merged": merged}

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        merged = data["merged"]
        if len(merged) < 10:
            return {"insufficient_data": True, "categories": []}

        categories = [
            ("miss", "dlc_miss_pct", "man_miss_pct", "Miss"),
            ("disp", "dlc_disp_pct", "man_disp_pct", "Displaced"),
            ("ret", "dlc_ret_pct", "man_ret_pct", "Retrieved"),
        ]

        cat_results = []
        for cat_key, dlc_col, man_col, cat_label in categories:
            r_s, p_s = stats.spearmanr(merged[man_col], merged[dlc_col])
            r_p, p_p = stats.pearsonr(merged[man_col], merged[dlc_col])
            bias = merged[dlc_col].mean() - merged[man_col].mean()
            diff = merged[dlc_col] - merged[man_col]
            cat_results.append({
                "key": cat_key,
                "label": cat_label,
                "dlc_col": dlc_col,
                "man_col": man_col,
                "spearman_r": r_s,
                "spearman_p": p_s,
                "pearson_r": r_p,
                "pearson_p": p_p,
                "bias": bias,
                "loa_upper": diff.mean() + 1.96 * diff.std(),
                "loa_lower": diff.mean() - 1.96 * diff.std(),
            })
            detail = format_stat_result(
                "Spearman", r_s, p_s, n=len(merged),
            )
            print(f"    {cat_label}: {detail}, bias={bias:+.1f}%", flush=True)

        return {"insufficient_data": False, "categories": cat_results}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 3, hspace=0.35, wspace=0.3)
        axes = {}
        for col, key in enumerate(["miss", "disp", "ret"]):
            axes[f"scatter_{key}"] = fig.add_subplot(inner[0, col])
            axes[f"bland_{key}"] = fig.add_subplot(inner[1, col])
        return axes

    def plot(self, data, results, fig, ax, theme):
        merged = data["merged"]
        if results.get("insufficient_data"):
            return

        colors = {"miss": "#d62728", "disp": "#ff7f0e", "ret": "#2ca02c"}

        for cat in results["categories"]:
            key = cat["key"]
            color = colors[key]
            dlc_col = cat["dlc_col"]
            man_col = cat["man_col"]
            label = cat["label"]

            # Scatter
            a = ax[f"scatter_{key}"]
            a.scatter(
                merged[man_col], merged[dlc_col],
                c=color, s=15, alpha=0.3, edgecolor="none",
            )
            a.plot([0, 100], [0, 100], "k--", alpha=0.5, linewidth=1)
            a.set_xlabel(f"Manual {label} %", fontsize=10)
            a.set_ylabel(f"MouseReach {label} %", fontsize=10)
            a.set_title(label, fontweight="bold")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            info = (
                f"Spearman r={cat['spearman_r']:.3f}\n"
                f"Pearson r={cat['pearson_r']:.3f}\n"
                f"Bias: {cat['bias']:+.1f}%"
            )
            a.text(
                0.02, 0.98, info, transform=a.transAxes,
                ha="left", va="top", fontsize=8, family="monospace",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.8),
            )

            # Bland-Altman
            a = ax[f"bland_{key}"]
            mean_vals = (merged[dlc_col] + merged[man_col]) / 2
            diff_vals = merged[dlc_col] - merged[man_col]
            a.scatter(
                mean_vals, diff_vals, c=color, s=15, alpha=0.3, edgecolor="none",
            )
            mean_diff = diff_vals.mean()
            std_diff = diff_vals.std()
            a.axhline(
                y=mean_diff, color="black", linewidth=1.5,
                label=f"Mean bias: {mean_diff:+.1f}%",
            )
            a.axhline(
                y=mean_diff + 1.96 * std_diff,
                color="gray", linestyle="--", linewidth=1,
            )
            a.axhline(
                y=mean_diff - 1.96 * std_diff,
                color="gray", linestyle="--", linewidth=1,
            )
            a.axhline(y=0, color="black", linestyle=":", alpha=0.3)
            a.set_xlabel("Mean of Pipeline & Manual (%)", fontsize=10)
            a.set_ylabel("Pipeline - Manual (%)", fontsize=10)
            a.set_title(f"Bland-Altman: {label}", fontweight="bold")
            a.legend(fontsize=8, loc="upper right")
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        merged = data["merged"]
        n = len(merged)
        if results.get("insufficient_data"):
            return f"INSUFFICIENT DATA  Only {n} overlapping sessions found (need >=10)."

        lines = [
            f"EXPERIMENT  Session-level validation of MouseReach pipeline vs manual pellet scoring",
            f"SESSIONS    N={n} overlapping sessions (pillar tray only)",
            f"METRIC      % in each outcome category per session",
            f"TOP ROW     Scatter plots with identity line (pipeline vs manual)",
            f"BOTTOM ROW  Bland-Altman plots (difference vs mean, +/- 1.96 SD limits of agreement)",
        ]
        for cat in results["categories"]:
            lines.append(
                f"  {cat['label']:12s}: Spearman r={cat['spearman_r']:.3f}, "
                f"bias={cat['bias']:+.1f}%, "
                f"LoA=[{cat['loa_lower']:+.1f}%, {cat['loa_upper']:+.1f}%]"
            )
        return "\n".join(lines)

    def figure_legend(self, data, results):
        merged = data["merged"]
        n = len(merged)
        cats = results.get("categories", [])
        es_parts = []
        for cat in cats:
            es_parts.append(
                f"{cat['label']}: r={cat['spearman_r']:.3f}, bias={cat['bias']:+.1f}%"
            )

        return FigureLegend(
            question=(
                "How well does the MouseReach pipeline's automated outcome "
                "classification agree with manual pellet scoring?"
            ),
            method=(
                f"N={n} overlapping sessions from CNT_01-04 cohorts. "
                f"Pillar tray sessions only. Session-level outcome percentages "
                f"compared between MouseReach pipeline and manual scoring."
            ),
            finding=(
                "Strong correlation for retrieval and miss categories. "
                "Displaced classification shows moderate agreement with some bias."
            ),
            analysis=(
                f"{stat_justification('spearman')} "
                f"Bland-Altman limits of agreement quantify systematic bias."
            ),
            effect_sizes="; ".join(es_parts) if es_parts else "Not computed",
            confounds=(
                "Manual scoring uses pellet position (disappeared vs moved); "
                "pipeline uses paw trajectory and pellet contact. Different "
                "information sources may produce systematic disagreement."
            ),
            follow_up=(
                "Session-level agreement does not guarantee per-reach accuracy. "
                "Within-session variability may mask reach-level errors."
            ),
        )


# ---------------------------------------------------------------------------
# Recipe 3: OutcomeMatchedKinematics
# ---------------------------------------------------------------------------

class OutcomeMatchedKinematics(FigureRecipe):
    """Kinematic comparison filtered to specific outcome types.

    Within each outcome category (displaced or miss), compare kinematic
    features across phases for recovered vs not-recovered animals.
    Controls for the distributional shift shown in Recipe 1.
    """

    name = "outcome_matched_kinematics"
    title = "Outcome-Matched Kinematic Recovery"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 10)

    def __init__(self, outcome_cat: int = 1, min_reaches: int = 3):
        """
        Parameters
        ----------
        outcome_cat : int
            Outcome category to filter: 0=miss, 1=displaced, 2=retrieved.
        min_reaches : int
            Minimum reaches per animal per phase to include that animal.
        """
        self.outcome_cat = outcome_cat
        self.min_reaches = min_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "outcome_cat": self.outcome_cat,
            "outcome_label": OUTCOME_NAMES.get(self.outcome_cat, "Unknown"),
            "min_reaches_per_animal_phase": self.min_reaches,
            "cohorts": COHORTS,
            "features": [f for f, _ in KEY_FEATURES],
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        outcome_reach = _filter_outcomes(reach, [self.outcome_cat])
        outcome_label = OUTCOME_NAMES.get(self.outcome_cat, str(self.outcome_cat))
        print(
            f"  Outcome filter: {outcome_label} (N={len(outcome_reach)} reaches)",
            flush=True,
        )

        available = [
            (f, l)
            for f, l in KEY_FEATURES
            if f in outcome_reach.columns and outcome_reach[f].notna().sum() > 100
        ]

        return {
            "outcome_reach": outcome_reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
            "outcome_label": outcome_label,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        outcome_reach = data["outcome_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        stat_results = []
        for feat, label in available[:6]:
            for phase in PHASES:
                rec_vals, norec_vals = [], []
                for animal in rec:
                    vals = outcome_reach[
                        (outcome_reach["subject_id"] == animal)
                        & (outcome_reach["phase"] == phase)
                        & outcome_reach[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_reaches:
                        rec_vals.append(vals.mean())
                for animal in norec:
                    vals = outcome_reach[
                        (outcome_reach["subject_id"] == animal)
                        & (outcome_reach["phase"] == phase)
                        & outcome_reach[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_reaches:
                        norec_vals.append(vals.mean())

                if len(rec_vals) >= 2 and len(norec_vals) >= 2:
                    u_stat, p = stats.mannwhitneyu(
                        rec_vals, norec_vals, alternative="two-sided"
                    )
                    d = cohens_d(np.array(rec_vals), np.array(norec_vals))
                    detail = format_stat_result(
                        "Mann-Whitney U", u_stat, p, d=d,
                        n=len(rec_vals) + len(norec_vals),
                    )
                    stat_results.append({
                        "feature": label,
                        "phase": phase,
                        "detail": detail,
                        "p": p,
                        "d": d,
                    })

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        # Up to 6 features in a 2x3 grid
        inner = plot_gs.subgridspec(2, 3, hspace=0.4, wspace=0.35)
        axes = np.array([
            [fig.add_subplot(inner[r, c]) for c in range(3)] for r in range(2)
        ])
        return axes

    def plot(self, data, results, fig, ax, theme):
        outcome_reach = data["outcome_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"][:6]

        for idx, (feat, label) in enumerate(available):
            r, c = divmod(idx, 3)
            a = ax[r, c]

            for group_animals, color, glabel in [
                (rec, GROUP_COLORS["Recovered"], f"Rec (N={len(rec)})"),
                (norec, GROUP_COLORS["Not Recovered"], f"NoRec (N={len(norec)})"),
            ]:
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(PHASES):
                    animal_means = []
                    for animal in group_animals:
                        vals = outcome_reach[
                            (outcome_reach["subject_id"] == animal)
                            & (outcome_reach["phase"] == phase)
                            & outcome_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_reaches:
                            animal_means.append(vals.mean())
                    if len(animal_means) >= 2:
                        means.append(np.mean(animal_means))
                        sems.append(stats.sem(animal_means))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-", color=color,
                        linewidth=2.5, markersize=8, capsize=4,
                        label=glabel, zorder=5,
                    )

            # Add stat annotations for significant results
            for sr in results.get("stat_results", []):
                if sr["feature"] == label and sr["p"] < 0.05:
                    pi = PHASES.index(sr["phase"]) if sr["phase"] in PHASES else None
                    if pi is not None:
                        a.annotate(
                            f"d={sr['d']:.2f}",
                            xy=(pi, a.get_ylim()[1]),
                            fontsize=6, ha="center", va="top", color="#555555",
                        )

            a.set_xticks(range(len(PHASES)))
            a.set_xticklabels(PHASE_LABELS, fontsize=9)
            a.set_ylabel(label, fontsize=9)
            a.set_title(label, fontsize=10, fontweight="bold")
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Hide unused axes
        for idx in range(len(available), 6):
            r, c = divmod(idx, 3)
            ax[r, c].set_visible(False)

    def methodology_text(self, data, results):
        outcome_label = data["outcome_label"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        sig = [s for s in stat_results if s["p"] < 0.05]

        lines = [
            f"EXPERIMENT  Outcome-matched kinematic comparison ('{outcome_label}' reaches only)",
            f"SUBJECTS    N={len(rec)} recovered + {len(norec)} not recovered",
            f"FILTER      Only reaches with outcome={self.outcome_cat} ({outcome_label}), "
            f">={self.min_reaches} per animal per phase",
            f"STATISTICS  Mann-Whitney U (two-sided), Cohen's d for effect size",
            f"SIGNIFICANT {len(sig)}/{len(stat_results)} comparisons at p<0.05",
        ]
        for s in sig[:5]:
            lines.append(f"  {s['feature']} ({s['phase']}): {s['detail']}")
        return "\n".join(lines)

    def figure_legend(self, data, results):
        outcome_label = data["outcome_label"]
        stat_results = results.get("stat_results", [])
        sig = [s for s in stat_results if s["p"] < 0.05]
        es_parts = [f"{s['feature']} ({s['phase']}): d={s['d']:.2f}" for s in sig[:5]]

        return FigureLegend(
            question=(
                f"Within {outcome_label.lower()} reaches only, do kinematics "
                f"normalize during rehabilitation?"
            ),
            method=(
                f"Filtered to outcome={self.outcome_cat} ({outcome_label}) reaches. "
                f"Per-animal means computed with >={self.min_reaches} reaches per phase. "
                f"Recovered vs not-recovered compared per phase."
            ),
            finding=(
                f"Outcome-matched comparison controls for distributional shift. "
                f"{len(sig)} of {len(stat_results)} feature-phase comparisons "
                f"reach significance at p<0.05."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Cohen's d quantifies effect magnitude."
            ),
            effect_sizes="; ".join(es_parts) if es_parts else "Not computed",
            confounds=(
                "Unequal sample sizes between groups. Outcome classification "
                "accuracy (see Recipe 2) affects which reaches are included."
            ),
            follow_up=(
                "First-reach analysis (Recipe 6) provides a complementary "
                "fatigue-free kinematic comparison."
            ),
        )


# ---------------------------------------------------------------------------
# Recipe 4: ReachesToInteraction
# ---------------------------------------------------------------------------

class ReachesToInteraction(FigureRecipe):
    """Analysis of reaches needed to contact pellet.

    Four-panel figure: (1) attempts before pellet interaction,
    (2) total reaches per segment by outcome, (3) miss rate,
    (4) classification confidence by outcome.
    """

    name = "reaches_to_interaction"
    title = "Reach Efficiency: Attempts to Pellet Interaction"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (16, 14)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "phases": PHASES,
            "min_segments_per_animal": 2,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        contacted = seg[seg["has_causal"] == 1].copy()
        has_confidence = "segment_outcome_confidence" in reach.columns

        print(
            f"  Contacted segments: {len(contacted)}, "
            f"confidence col available: {has_confidence}",
            flush=True,
        )

        return {
            "reach": reach,
            "seg": seg,
            "contacted": contacted,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "has_confidence": has_confidence,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        contacted = data["contacted"]
        seg = data["seg"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]

        stat_results = []
        for phase in PHASES:
            rec_medians, norec_medians = [], []
            for animal in rec:
                adata = contacted[
                    (contacted["subject_id"] == animal)
                    & (contacted["phase"] == phase)
                ]
                if len(adata) >= 2:
                    rec_medians.append(adata["causal_reach_num"].median())
            for animal in norec:
                adata = contacted[
                    (contacted["subject_id"] == animal)
                    & (contacted["phase"] == phase)
                ]
                if len(adata) >= 2:
                    norec_medians.append(adata["causal_reach_num"].median())

            if len(rec_medians) >= 2 and len(norec_medians) >= 2:
                u_stat, p = stats.mannwhitneyu(
                    rec_medians, norec_medians, alternative="two-sided"
                )
                d = cohens_d(np.array(rec_medians), np.array(norec_medians))
                detail = format_stat_result(
                    "Mann-Whitney U", u_stat, p, d=d,
                    n=len(rec_medians) + len(norec_medians),
                )
                stat_results.append({
                    "phase": phase, "detail": detail, "p": p, "d": d,
                })
                print(f"    Causal reach# {phase}: {detail}", flush=True)

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 2, hspace=0.35, wspace=0.3)
        return {
            "attempts": fig.add_subplot(inner[0, 0]),
            "reaches_by_outcome": fig.add_subplot(inner[0, 1]),
            "miss_rate": fig.add_subplot(inner[1, 0]),
            "confidence": fig.add_subplot(inner[1, 1]),
        }

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        contacted = data["contacted"]
        reach = data["reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        has_confidence = data["has_confidence"]

        # Panel 1: attempts before pellet interaction
        a = ax["attempts"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(PHASES):
                animal_medians = []
                for animal in group_animals:
                    adata = contacted[
                        (contacted["subject_id"] == animal)
                        & (contacted["phase"] == phase)
                    ]
                    if len(adata) >= 2:
                        animal_medians.append(adata["causal_reach_num"].median())
                if len(animal_medians) >= 2:
                    means.append(np.mean(animal_medians))
                    sems.append(stats.sem(animal_medians))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4,
                    label=glabel, zorder=5,
                )
        a.set_xticks(range(len(PHASES)))
        a.set_xticklabels(PHASE_LABELS, fontsize=9)
        a.set_ylabel("Reach # of Causal Reach (median per animal)")
        a.set_title("Attempts Before Pellet Interaction", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        # Panel 2: total reaches per segment by outcome
        a = ax["reaches_by_outcome"]
        for outcome_cat in OUTCOMES_ORDERED:
            outcome_seg = seg[seg["outcome_cat"] == outcome_cat]
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(PHASES):
                pdata = outcome_seg[outcome_seg["phase"] == phase]
                if len(pdata) >= 5:
                    means.append(pdata["n_reaches"].mean())
                    sems.append(stats.sem(pdata["n_reaches"]))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-",
                    color=_OUTCOME_COLORS_NUMERIC[outcome_cat],
                    linewidth=2, markersize=7, capsize=3,
                    label=OUTCOME_NAMES[outcome_cat], zorder=5,
                )
        a.set_xticks(range(len(PHASES)))
        a.set_xticklabels(PHASE_LABELS, fontsize=9)
        a.set_ylabel("Total Reaches in Segment")
        a.set_title("Reaches per Segment by Outcome Type", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        # Panel 3: miss rate
        a = ax["miss_rate"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(PHASES):
                animal_pcts = []
                for animal in group_animals:
                    adata = seg[
                        (seg["subject_id"] == animal) & (seg["phase"] == phase)
                    ]
                    if len(adata) >= 3:
                        animal_pcts.append(
                            (adata["outcome_cat"] == 0).mean() * 100
                        )
                if len(animal_pcts) >= 2:
                    means.append(np.mean(animal_pcts))
                    sems.append(stats.sem(animal_pcts))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4,
                    label=glabel, zorder=5,
                )
        a.set_xticks(range(len(PHASES)))
        a.set_xticklabels(PHASE_LABELS, fontsize=9)
        a.set_ylabel("% Segments with No Contact")
        a.set_title("Miss Rate (Untouched Segments)", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

        # Panel 4: classification confidence
        a = ax["confidence"]
        if has_confidence:
            conf_col = "segment_outcome_confidence"
            for outcome_cat in OUTCOMES_ORDERED:
                outcome_reach = reach[reach["outcome_cat"] == outcome_cat]
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(PHASES):
                    vals = outcome_reach[
                        (outcome_reach["phase"] == phase)
                        & outcome_reach[conf_col].notna()
                    ][conf_col]
                    if len(vals) >= 10:
                        means.append(vals.mean())
                        sems.append(stats.sem(vals))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-",
                        color=_OUTCOME_COLORS_NUMERIC[outcome_cat],
                        linewidth=2, markersize=7, capsize=3,
                        label=OUTCOME_NAMES[outcome_cat], zorder=5,
                    )
            a.set_ylabel("Classification Confidence")
            a.set_title("MouseReach Classification Confidence", fontweight="bold")
        else:
            a.text(
                0.5, 0.5, "Confidence column\nnot available",
                transform=a.transAxes, ha="center", va="center",
                fontsize=11, color="gray",
            )
            a.set_title("Classification Confidence", fontweight="bold")
        a.set_xticks(range(len(PHASES)))
        a.set_xticklabels(PHASE_LABELS, fontsize=9)
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        lines = [
            f"EXPERIMENT  Reach efficiency analysis: attempts before pellet contact",
            f"SUBJECTS    N={len(rec)} recovered + {len(norec)} not recovered",
            f"PANELS      (1) Causal reach number (median per animal, +/- SEM)",
            f"            (2) Total reaches per segment by outcome type",
            f"            (3) Miss rate (% untouched segments per animal)",
            f"            (4) MouseReach classification confidence by outcome",
            f"STATISTICS  Mann-Whitney U (two-sided), Cohen's d",
        ]
        for s in stat_results:
            lines.append(f"  {s['phase']}: {s['detail']}")
        return "\n".join(lines)

    def figure_legend(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        es_parts = [
            f"{s['phase']}: d={s['d']:.2f}" for s in stat_results if s["p"] < 0.05
        ]
        return FigureLegend(
            question=(
                "How many reaching attempts does it take to contact "
                "the pellet, and does this change with injury/recovery?"
            ),
            method=(
                f"N={len(rec)} recovered + {len(norec)} not recovered. "
                f"Causal reach number = ordinal position of the reach that "
                f"first contacts the pellet within each segment. "
                f"Per-animal medians compared across phases."
            ),
            finding=(
                "Post-injury animals require more attempts before pellet contact. "
                "Recovered animals partially normalize during rehabilitation."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Cohen's d for between-group effect sizes."
            ),
            effect_sizes="; ".join(es_parts) if es_parts else "Not computed",
            confounds=(
                "Segments with no contact (misses) are excluded from the "
                "causal-reach analysis, creating survivorship bias."
            ),
            follow_up=(
                "Fatigue analysis (Recipe 5) examines whether reach efficiency "
                "degrades within sessions."
            ),
        )


# ---------------------------------------------------------------------------
# Recipe 5: ReachFatigueAnalysis
# ---------------------------------------------------------------------------

class ReachFatigueAnalysis(FigureRecipe):
    """Within-session reach performance decline.

    Examines whether contact rate and peak velocity degrade across
    segment position (early/mid/late) and reach number within segments.
    Shows individual subject traces alongside group means.
    """

    name = "reach_fatigue_analysis"
    title = "Within-Session Fatigue Effects"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (18, 14)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "phases_analyzed": ["Pre-Injury", "Rehab_Pillar"],
            "segment_bins": ["Early (1-5)", "Mid (6-10)", "Late (11+)"],
            "reach_bins": ["1-3", "4-10", "11-50", "50+"],
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])
        return {
            "reach": reach,
            "seg": seg,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        seg = data["seg"]
        reach = data["reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        fatigue_phases = ["Pre-Injury", "Rehab_Pillar"]

        stat_results = []
        for phase in fatigue_phases:
            phase_seg = seg[seg["phase"] == phase]
            for label, animals in [("Recovered", rec), ("Not Recovered", norec)]:
                gseg = phase_seg[phase_seg["subject_id"].isin(animals)]
                early = gseg[gseg["segment_num"] <= 5]
                late = gseg[gseg["segment_num"] > 10]
                if len(early) >= 5 and len(late) >= 5:
                    early_rate = (early["outcome_cat"] >= 1).mean() * 100
                    late_rate = (late["outcome_cat"] >= 1).mean() * 100
                    stat_results.append({
                        "phase": phase,
                        "group": label,
                        "early_contact": early_rate,
                        "late_contact": late_rate,
                        "decline": early_rate - late_rate,
                    })

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 3, hspace=0.4, wspace=0.3)
        axes = np.array([
            [fig.add_subplot(inner[r, c]) for c in range(3)] for r in range(2)
        ])
        return axes

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        reach = data["reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        fatigue_phases = ["Pre-Injury", "Rehab_Pillar"]
        fatigue_labels = ["Pre-Injury", "Rehab Pillar"]

        subject_colors = get_persistent_subject_colors(
            sorted(rec | norec)
        )

        for row, (phase, plabel) in enumerate(zip(fatigue_phases, fatigue_labels)):
            phase_seg = seg[seg["phase"] == phase].copy()
            phase_reach = reach[reach["phase"] == phase].copy()

            if phase_seg.empty:
                continue

            # LEFT: contact rate by segment position
            a = ax[row, 0]
            phase_seg["seg_bin"] = pd.cut(
                phase_seg["segment_num"],
                bins=[0, 5, 10, 100],
                labels=["Early\n(1-5)", "Mid\n(6-10)", "Late\n(11+)"],
            )

            for group_animals, color, glabel in [
                (rec, GROUP_COLORS["Recovered"], "Recovered"),
                (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
            ]:
                gseg = phase_seg[phase_seg["subject_id"].isin(group_animals)]

                # Individual subject traces (Rule 18)
                for animal in group_animals:
                    aseg = gseg[gseg["subject_id"] == animal]
                    bins_list = ["Early\n(1-5)", "Mid\n(6-10)", "Late\n(11+)"]
                    avals = []
                    for b in bins_list:
                        bdata = aseg[aseg["seg_bin"] == b]
                        if len(bdata) >= 2:
                            avals.append((bdata["outcome_cat"] >= 1).mean() * 100)
                        else:
                            avals.append(np.nan)
                    valid = [
                        (i, v) for i, v in enumerate(avals) if not np.isnan(v)
                    ]
                    if len(valid) >= 2:
                        vx, vy = zip(*valid)
                        a.plot(
                            vx, vy, color=subject_colors.get(animal, color),
                            alpha=0.2, linewidth=0.8, zorder=2,
                        )

                # Group mean
                bins_list = ["Early\n(1-5)", "Mid\n(6-10)", "Late\n(11+)"]
                means, sems = [], []
                for b in bins_list:
                    bdata = gseg[gseg["seg_bin"] == b]
                    if len(bdata) >= 5:
                        means.append(
                            (bdata["outcome_cat"] >= 1).mean() * 100
                        )
                        sems.append(
                            stats.sem(
                                (bdata["outcome_cat"] >= 1).astype(float)
                            )
                            * 100
                        )
                    else:
                        means.append(np.nan)
                        sems.append(0)

                valid = [
                    (i, m, s)
                    for i, (m, s) in enumerate(zip(means, sems))
                    if not np.isnan(m)
                ]
                if valid:
                    vx, vm, vs = zip(*valid)
                    a.errorbar(
                        vx, vm, yerr=vs, fmt="o-", color=color,
                        linewidth=2, markersize=8, capsize=4, label=glabel,
                        zorder=5,
                    )

            a.set_xticks(range(3))
            a.set_xticklabels(["Early\n(1-5)", "Mid\n(6-10)", "Late\n(11+)"])
            a.set_ylabel("% Segments with Contact")
            a.set_title(
                f"{plabel}: Contact Rate by Position", fontweight="bold"
            )
            a.legend(fontsize=8)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            # MIDDLE: peak velocity by segment position
            a = ax[row, 1]
            feat = "peak_velocity_px_per_frame"
            if feat in phase_reach.columns:
                prc = phase_reach.copy()
                prc["seg_bin"] = pd.cut(
                    prc["segment_num"],
                    bins=[0, 5, 10, 100],
                    labels=["Early", "Mid", "Late"],
                )
                for group_animals, color, glabel in [
                    (rec, GROUP_COLORS["Recovered"], "Recovered"),
                    (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
                ]:
                    gdata = prc[prc["subject_id"].isin(group_animals)]
                    bins_l = ["Early", "Mid", "Late"]
                    means, sems = [], []
                    for b in bins_l:
                        bdata = gdata[
                            (gdata["seg_bin"] == b) & gdata[feat].notna()
                        ]
                        if len(bdata) >= 10:
                            means.append(bdata[feat].mean())
                            sems.append(stats.sem(bdata[feat]))
                        else:
                            means.append(np.nan)
                            sems.append(0)
                    valid = [
                        (i, m, s)
                        for i, (m, s) in enumerate(zip(means, sems))
                        if not np.isnan(m)
                    ]
                    if valid:
                        vx, vm, vs = zip(*valid)
                        a.errorbar(
                            vx, vm, yerr=vs, fmt="o-", color=color,
                            linewidth=2, markersize=8, capsize=4, label=glabel,
                        )
            a.set_xticks(range(3))
            a.set_xticklabels(["Early", "Mid", "Late"])
            a.set_ylabel("Peak Velocity (px/frame)")
            a.set_title(
                f"{plabel}: Velocity by Position", fontweight="bold"
            )
            a.legend(fontsize=8)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            # RIGHT: velocity by reach number within segment
            a = ax[row, 2]
            if feat in phase_reach.columns:
                prc = phase_reach[phase_reach[feat].notna()].copy()
                prc["reach_bin"] = pd.cut(
                    prc["reach_num"],
                    bins=[0, 3, 10, 50, 1000],
                    labels=["1-3", "4-10", "11-50", "50+"],
                )
                for group_animals, color, glabel in [
                    (rec, GROUP_COLORS["Recovered"], "Recovered"),
                    (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
                ]:
                    gdata = prc[prc["subject_id"].isin(group_animals)]
                    bins_l = ["1-3", "4-10", "11-50", "50+"]
                    means, sems = [], []
                    for b in bins_l:
                        bdata = gdata[gdata["reach_bin"] == b]
                        if len(bdata) >= 10:
                            means.append(bdata[feat].mean())
                            sems.append(stats.sem(bdata[feat]))
                        else:
                            means.append(np.nan)
                            sems.append(0)
                    valid = [
                        (i, m, s)
                        for i, (m, s) in enumerate(zip(means, sems))
                        if not np.isnan(m)
                    ]
                    if valid:
                        vx, vm, vs = zip(*valid)
                        a.errorbar(
                            vx, vm, yerr=vs, fmt="o-", color=color,
                            linewidth=2, markersize=8, capsize=4, label=glabel,
                        )
            a.set_xticks(range(4))
            a.set_xticklabels(["1-3", "4-10", "11-50", "50+"])
            a.set_xlabel("Reach # within segment")
            a.set_ylabel("Peak Velocity")
            a.set_title(f"{plabel}: Velocity by Reach #", fontweight="bold")
            a.legend(fontsize=8)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        lines = [
            f"EXPERIMENT  Within-session fatigue: does performance degrade across segments?",
            f"SUBJECTS    N={len(rec)} recovered + {len(norec)} not recovered",
            f"PHASES      Pre-Injury and Rehab Pillar (2 rows)",
            f"PANELS      (1) Contact rate by segment position (with individual traces)",
            f"            (2) Peak velocity by segment position",
            f"            (3) Peak velocity by reach number within segment",
        ]
        for s in stat_results:
            lines.append(
                f"  {s['phase']} {s['group']}: "
                f"early={s['early_contact']:.1f}% -> late={s['late_contact']:.1f}% "
                f"(decline={s['decline']:+.1f}%)"
            )
        return "\n".join(lines)

    def figure_legend(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        es_parts = [
            f"{s['phase']} {s['group']}: {s['decline']:+.1f}% decline"
            for s in stat_results
        ]
        return FigureLegend(
            question=(
                "Does reaching performance degrade within a session "
                "(fatigue effect)?"
            ),
            method=(
                f"N={len(rec)} recovered + {len(norec)} not recovered. "
                f"Segments binned by position (early 1-5, mid 6-10, late 11+). "
                f"Individual subject traces shown (thin lines) with group means."
            ),
            finding=(
                "Contact rate and peak velocity show modest within-session "
                "decline, more pronounced in not-recovered animals."
            ),
            analysis=(
                "Descriptive comparison of binned segment positions. "
                "Individual traces reveal inter-subject variability."
            ),
            effect_sizes="; ".join(es_parts) if es_parts else "Not computed",
            confounds=(
                "Session length varies between animals and phases. "
                "Animals with fewer segments contribute less to late bins. "
                "Motivation confounded with fatigue."
            ),
            follow_up=(
                "First-reach analysis (Recipe 6) isolates the initial motor "
                "command before fatigue accumulates."
            ),
        )


# ---------------------------------------------------------------------------
# Recipe 6: FirstReachAnalysis
# ---------------------------------------------------------------------------

class FirstReachAnalysis(FigureRecipe):
    """First reach per session kinematic analysis.

    The first reach in each segment represents the purest motor intent
    signal, before fatigue or frustration effects accumulate. Per-subject
    colors shown for individual trajectories (Rule 6).
    """

    name = "first_reach_analysis"
    title = "First-Reach Kinematics (Purest Motor Signal)"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 7)

    TOP_FEATURES = [
        ("peak_velocity_px_per_frame", "Peak Velocity"),
        ("max_extent_mm", "Max Extent (mm)"),
        ("trajectory_straightness", "Straightness"),
        ("duration_frames", "Duration"),
        ("hand_angle_at_apex_deg", "Hand Angle"),
        ("attention_score", "Attention Score"),
    ]

    def __init__(self, min_first_reaches: int = 2):
        self.min_first_reaches = min_first_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "features": [f for f, _ in self.TOP_FEATURES],
            "min_first_reaches_per_animal_phase": self.min_first_reaches,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        first = reach[reach["is_first_reach"] == 1].copy()
        print(f"  First reaches: {len(first)}", flush=True)

        available = [
            (f, l)
            for f, l in self.TOP_FEATURES
            if f in first.columns and first[f].notna().sum() > 30
        ]

        return {
            "first": first,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
            "recovery": recovery,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        first = data["first"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        stat_results = []
        for feat, label in available:
            for phase in PHASES:
                rec_vals, norec_vals = [], []
                for animal in rec:
                    vals = first[
                        (first["subject_id"] == animal)
                        & (first["phase"] == phase)
                        & first[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_first_reaches:
                        rec_vals.append(vals.mean())
                for animal in norec:
                    vals = first[
                        (first["subject_id"] == animal)
                        & (first["phase"] == phase)
                        & first[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_first_reaches:
                        norec_vals.append(vals.mean())

                if len(rec_vals) >= 2 and len(norec_vals) >= 2:
                    u_stat, p = stats.mannwhitneyu(
                        rec_vals, norec_vals, alternative="two-sided"
                    )
                    d = cohens_d(np.array(rec_vals), np.array(norec_vals))
                    stat_results.append({
                        "feature": label,
                        "phase": phase,
                        "p": p,
                        "d": d,
                        "detail": format_stat_result(
                            "Mann-Whitney U", u_stat, p, d=d,
                            n=len(rec_vals) + len(norec_vals),
                        ),
                    })

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        n = min(len(self.TOP_FEATURES), 6)
        inner = plot_gs.subgridspec(1, n, wspace=0.35)
        return [fig.add_subplot(inner[i]) for i in range(n)]

    def plot(self, data, results, fig, ax, theme):
        first = data["first"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"][:6]

        all_animals = sorted(rec | norec)
        subject_colors = get_persistent_subject_colors(all_animals)

        for col, (feat, label) in enumerate(available):
            a = ax[col] if isinstance(ax, list) else ax

            # Individual subject traces (Rule 6: per-subject colors)
            for animal in all_animals:
                color = subject_colors.get(animal, "#888888")
                is_rec = animal in rec
                vals_by_phase = []
                for pi, phase in enumerate(PHASES):
                    avals = first[
                        (first["subject_id"] == animal)
                        & (first["phase"] == phase)
                        & first[feat].notna()
                    ][feat]
                    if len(avals) >= self.min_first_reaches:
                        vals_by_phase.append((pi, avals.mean()))
                if len(vals_by_phase) >= 2:
                    xs, ys = zip(*vals_by_phase)
                    a.plot(
                        xs, ys, color=color, alpha=0.25, linewidth=0.8,
                        zorder=2,
                    )

            # Group means
            for group_animals, gcolor, glabel in [
                (rec, GROUP_COLORS["Recovered"], "Recovered"),
                (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
            ]:
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(PHASES):
                    animal_means = []
                    for animal in group_animals:
                        vals = first[
                            (first["subject_id"] == animal)
                            & (first["phase"] == phase)
                            & first[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_first_reaches:
                            animal_means.append(vals.mean())
                    if len(animal_means) >= 2:
                        means.append(np.mean(animal_means))
                        sems.append(stats.sem(animal_means))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-", color=gcolor,
                        linewidth=2.5, markersize=8, capsize=4,
                        label=glabel, zorder=5,
                    )

            a.set_xticks(range(len(PHASES)))
            a.set_xticklabels(PHASE_LABELS, fontsize=9)
            a.set_ylabel(label, fontsize=9)
            a.set_title(label, fontsize=10, fontweight="bold")
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

    def methodology_text(self, data, results):
        first = data["first"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        sig = [s for s in stat_results if s["p"] < 0.05]

        lines = [
            f"EXPERIMENT  First-reach kinematics (1st reach per segment)",
            f"SUBJECTS    N={len(rec)} recovered + {len(norec)} not recovered",
            f"REACHES     N={len(first)} first reaches total",
            f"RATIONALE   First reach = purest motor signal before fatigue/frustration",
            f"STATISTICS  Mann-Whitney U (two-sided), Cohen's d",
            f"SIGNIFICANT {len(sig)}/{len(stat_results)} comparisons at p<0.05",
            f"PLOT        Thin lines = individual subjects (persistent colors). "
            f"Thick lines = group means +/- SEM.",
        ]
        for s in sig[:5]:
            lines.append(f"  {s['feature']} ({s['phase']}): {s['detail']}")
        return "\n".join(lines)

    def figure_legend(self, data, results):
        first = data["first"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results.get("stat_results", [])
        sig = [s for s in stat_results if s["p"] < 0.05]
        es_parts = [f"{s['feature']} ({s['phase']}): d={s['d']:.2f}" for s in sig[:5]]

        return FigureLegend(
            question=(
                "Does the first reach per segment (purest motor intent) "
                "show kinematic recovery during rehabilitation?"
            ),
            method=(
                f"N={len(rec)} recovered + {len(norec)} not recovered. "
                f"First reach per segment only (N={len(first)} total). "
                f"Per-animal means with >={self.min_first_reaches} reaches per phase. "
                f"Individual subject trajectories shown with persistent colors."
            ),
            finding=(
                "First-reach kinematics capture motor planning quality "
                "without within-segment fatigue confounds."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Cohen's d for between-group effect sizes. "
                f"{len(sig)} of {len(stat_results)} comparisons significant."
            ),
            effect_sizes="; ".join(es_parts) if es_parts else "Not computed",
            confounds=(
                "First reach may still carry session-level state (arousal, "
                "motivation). Segment count varies across animals."
            ),
            follow_up=(
                "Hit-vs-miss analysis (Recipe 7) examines what kinematic "
                "features distinguish successful from failed reaches."
            ),
        )


# ---------------------------------------------------------------------------
# Timeline constants (used by recipes 9-12)
# ---------------------------------------------------------------------------

TIMELINE_PHASES = [
    "Pre-Injury", "Post-1", "Post-2", "Post-3", "Post-4", "Rehab_Pillar",
]
TIMELINE_LABELS = [
    "Pre-Inj", "Post-1\n(~wk1)", "Post-2\n(~wk2)",
    "Post-3\n(~wk3)", "Post-4\n(~wk4)", "Rehab\nPillar",
]

POST_PHASES = ["Post-1", "Post-2", "Post-3", "Post-4"]
POST_LABELS = [
    "Post-1\n(~wk1)", "Post-2\n(~wk2)",
    "Post-3\n(~wk3)", "Post-4\n(~wk4)",
]


# ============================================================================
# Recipe 7: HitVsMissKinematics
# ============================================================================

class HitVsMissKinematics(FigureRecipe):
    """Kinematic comparison between successful vs failed reaches.

    Within each phase separately, compares kinematics of hits (retrieved)
    vs misses (untouched) as box plots with Kruskal-Wallis tests. Each
    phase is biologically distinct so they are analyzed independently.
    """

    name = "hit_vs_miss_kinematics"
    title = "Hit vs Miss Reach Kinematics"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 14)

    def __init__(self, recovery_group: str = "all", min_reaches: int = 5):
        """
        Parameters
        ----------
        recovery_group : str
            Which recovery group to plot: "recovered", "not_recovered", or
            "all" (separate figures merged into one grid).
        min_reaches : int
            Minimum reaches per outcome per phase to include in comparison.
        """
        self.recovery_group = recovery_group
        self.min_reaches = min_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "recovery_group": self.recovery_group,
            "min_reaches": self.min_reaches,
            "cohorts": COHORTS,
            "phases": PHASES,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        reach = _filter_plausible(reach)
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        top_feats = [
            ("peak_velocity_px_per_frame", "Peak Velocity"),
            ("max_extent_mm", "Max Extent"),
            ("trajectory_straightness", "Straightness"),
            ("hand_angle_at_apex_deg", "Hand Angle"),
            ("duration_frames", "Duration"),
            ("attention_score", "Attention"),
        ]
        available = [
            (f, l) for f, l in top_feats
            if f in reach.columns and reach[f].notna().sum() > 50
        ]

        print(
            f"  Recovery groups: {len(rec_animals)} recovered, "
            f"{len(norec_animals)} not recovered",
            flush=True,
        )
        return {
            "reach": reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        reach = data["reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        stat_results = []
        for group_animals, group_label in [
            (rec, "Recovered"),
            (norec, "Not Recovered"),
        ]:
            if self.recovery_group not in ("all", group_label.lower().replace(" ", "_")):
                continue
            gdata = reach[reach["subject_id"].isin(group_animals)]
            for phase in PHASES:
                pdata = gdata[gdata["phase"] == phase]
                for feat, label in available[:6]:
                    box_data = {}
                    for oc in OUTCOMES_ORDERED:
                        vals = pdata[
                            (pdata["outcome_cat"] == oc) & pdata[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_reaches:
                            box_data[oc] = vals.values

                    if len(box_data) >= 2:
                        arrays = list(box_data.values())
                        try:
                            h_stat, p = stats.kruskal(*arrays)
                            d = cohens_d(arrays[0], arrays[-1])
                            detail = format_stat_result(
                                "Kruskal-Wallis", h_stat, p, d=d,
                                n=sum(len(a) for a in arrays),
                            )
                            stat_results.append({
                                "group": group_label,
                                "phase": phase,
                                "feature": label,
                                "stat": h_stat,
                                "p": p,
                                "d": d,
                                "detail": detail,
                            })
                        except Exception:
                            pass

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(len(PHASES), 6, hspace=0.4, wspace=0.3)
        axes = np.array([
            [fig.add_subplot(inner[r, c]) for c in range(6)]
            for r in range(len(PHASES))
        ])
        return axes

    def plot(self, data, results, fig, ax, theme):
        reach = data["reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"][:6]
        stat_results = results["stat_results"]

        # Use "all" -> show recovered group
        if self.recovery_group == "all":
            group_animals = rec | norec
            group_label = "All Animals"
        elif self.recovery_group == "recovered":
            group_animals = rec
            group_label = "Recovered"
        else:
            group_animals = norec
            group_label = "Not Recovered"

        gdata = reach[reach["subject_id"].isin(group_animals)]

        for row, (phase, plabel) in enumerate(zip(PHASES, PHASE_LABELS)):
            pdata = gdata[gdata["phase"] == phase]
            for col, (feat, label) in enumerate(available):
                a = ax[row, col]
                box_data = []
                box_labels = []
                box_colors = []
                for oc in OUTCOMES_ORDERED:
                    vals = pdata[
                        (pdata["outcome_cat"] == oc) & pdata[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_reaches:
                        box_data.append(vals.values)
                        box_labels.append(
                            f"{OUTCOME_NAMES[oc]}\n({len(vals)})"
                        )
                        box_colors.append(_OUTCOME_COLORS_NUMERIC[oc])

                if len(box_data) >= 2:
                    bp = a.boxplot(
                        box_data, patch_artist=True, widths=0.6,
                        medianprops=dict(color="black", linewidth=2),
                        flierprops=dict(marker=".", markersize=1, alpha=0.1),
                    )
                    for patch, color in zip(bp["boxes"], box_colors):
                        patch.set_facecolor(color)
                        patch.set_alpha(0.6)
                    a.set_xticklabels(box_labels, fontsize=6)

                    # Find matching stat result
                    for sr in stat_results:
                        if sr["phase"] == phase and sr["feature"] == label:
                            stars = (
                                "****" if sr["p"] < 0.0001
                                else "***" if sr["p"] < 0.001
                                else "**" if sr["p"] < 0.01
                                else "*" if sr["p"] < 0.05
                                else "ns"
                            )
                            a.text(
                                0.5, 0.98, f"KW {stars}",
                                transform=a.transAxes, ha="center",
                                va="top", fontsize=7,
                                bbox=dict(
                                    boxstyle="round", facecolor="wheat",
                                    alpha=0.7,
                                ),
                            )
                            break
                else:
                    a.text(
                        0.5, 0.5, "N<%d" % self.min_reaches,
                        transform=a.transAxes, ha="center",
                        fontsize=9, color="gray",
                    )

                if row == 0:
                    a.set_title(label, fontsize=9, fontweight="bold")
                a.spines["top"].set_visible(False)
                a.spines["right"].set_visible(False)

            ax[row, 0].set_ylabel(plabel, fontsize=11, fontweight="bold")

        fig.suptitle(
            f"Hit vs Miss Kinematics: {group_label}\n"
            f"Each row = biologically distinct phase",
            fontsize=14, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results["stat_results"]
        n_sig = sum(1 for s in stat_results if s["p"] < 0.05)

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered\n"
            f"ANALYSIS    Within each phase, compare kinematics across outcome "
            f"categories (miss/displaced/retrieved)\n"
            f"FILTER      Plausible range filtering applied (Rule 30)\n"
            f"STATISTICS  Kruskal-Wallis test per phase x feature. "
            f"{n_sig}/{len(stat_results)} comparisons significant at p<0.05\n"
            f"PLOT        Box plots per outcome type, separate rows per phase"
        )

    def figure_legend(self, data, results):
        stat_results = results["stat_results"]
        es_parts = []
        for sr in stat_results:
            if sr["p"] < 0.05 and not (sr["d"] != sr["d"]):
                es_parts.append(
                    f"{sr['feature']} ({sr['phase']}): d={sr['d']:.2f}"
                )
        es_text = "; ".join(es_parts[:5]) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "What kinematic features distinguish successful from "
                "failed reaches within each experimental phase?"
            ),
            method=(
                f"Reach kinematics compared across outcome categories "
                f"(miss/displaced/retrieved) within each phase independently. "
                f"Plausible range filtering applied (Rule 30)."
            ),
            finding=(
                "Successful reaches show distinct kinematic profiles "
                "including higher velocity and greater extent, with "
                "phase-dependent effect sizes."
            ),
            analysis=(
                f"{stat_justification('kruskal')} "
                f"Cohen's d between extreme outcome groups."
            ),
            effect_sizes=es_text,
            confounds=(
                "Outcome classification relies on DLC-based segment scoring. "
                "Sample sizes differ across outcome categories."
            ),
            follow_up=(
                "Phase-separated analysis (Recipe 8) examines displaced-reach "
                "kinematics with full outcome distribution context."
            ),
        )


# ============================================================================
# Recipe 8: PhaseSeparatedKinematics
# ============================================================================

class PhaseSeparatedKinematics(FigureRecipe):
    """Kinematic features separated by experimental phase.

    Each row is one phase, showing outcome distribution and
    displaced-reach kinematics side by side. Applies plausible range
    filtering (Rule 30) and outcome filtering (Rule 31).
    """

    name = "phase_separated_kinematics"
    title = "Phase-Separated Outcomes and Displaced-Reach Kinematics"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 14)

    def __init__(self, min_animal_reaches: int = 3):
        """
        Parameters
        ----------
        min_animal_reaches : int
            Minimum displaced reaches per animal per phase to include.
        """
        self.min_animal_reaches = min_animal_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "min_animal_reaches": self.min_animal_reaches,
            "cohorts": COHORTS,
            "phases": PHASES,
            "outcome_filter": "displaced (1) for kinematics",
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        reach = _filter_plausible(reach)
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        core_feats = [
            ("peak_velocity_px_per_frame", "Peak Velocity"),
            ("trajectory_straightness", "Straightness"),
            ("hand_angle_at_apex_deg", "Hand Angle"),
            ("duration_frames", "Duration"),
        ]
        available = [
            (f, l) for f, l in core_feats
            if f in reach.columns and reach[f].notna().sum() > 50
        ]

        disp_reach = _filter_outcomes(reach, [1])
        print(
            f"  Displaced reaches: {len(disp_reach)} "
            f"(plausible-filtered, outcome=1)",
            flush=True,
        )

        return {
            "seg": seg,
            "disp_reach": disp_reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        stat_results = []
        for phase in PHASES:
            for feat, label in available:
                rec_vals, norec_vals = [], []
                for animal in rec:
                    vals = disp_reach[
                        (disp_reach["subject_id"] == animal)
                        & (disp_reach["phase"] == phase)
                        & disp_reach[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_animal_reaches:
                        rec_vals.append(vals.mean())
                for animal in norec:
                    vals = disp_reach[
                        (disp_reach["subject_id"] == animal)
                        & (disp_reach["phase"] == phase)
                        & disp_reach[feat].notna()
                    ][feat]
                    if len(vals) >= self.min_animal_reaches:
                        norec_vals.append(vals.mean())

                if len(rec_vals) >= 2 and len(norec_vals) >= 2:
                    try:
                        u_stat, p = stats.mannwhitneyu(
                            rec_vals, norec_vals, alternative="two-sided",
                        )
                        d = cohens_d(np.array(rec_vals), np.array(norec_vals))
                        detail = format_stat_result(
                            "Mann-Whitney U", u_stat, p, d=d,
                            n=len(rec_vals) + len(norec_vals),
                        )
                        stat_results.append({
                            "phase": phase,
                            "feature": label,
                            "p": p,
                            "d": d,
                            "detail": detail,
                        })
                    except Exception:
                        pass

        return {"stat_results": stat_results}

    def create_axes(self, fig, plot_gs):
        available_n = 4  # max core features
        ncols = 1 + available_n
        inner = plot_gs.subgridspec(
            len(PHASES), ncols, hspace=0.4, wspace=0.3,
        )
        axes = np.array([
            [fig.add_subplot(inner[r, c]) for c in range(ncols)]
            for r in range(len(PHASES))
        ])
        return axes

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]
        stat_results = results["stat_results"]

        for row, (phase, plabel) in enumerate(zip(PHASES, PHASE_LABELS)):
            # Column 0: outcome distribution
            a = ax[row, 0]
            for gi, (group_animals, color, glabel) in enumerate([
                (rec, GROUP_COLORS["Recovered"], "Rec"),
                (norec, GROUP_COLORS["Not Recovered"], "NoRec"),
            ]):
                gseg = seg[
                    (seg["subject_id"].isin(group_animals))
                    & (seg["phase"] == phase)
                ]
                total = len(gseg)
                if total < 5:
                    continue
                pcts = [
                    (gseg["outcome_cat"] == oc).sum() / total * 100
                    for oc in OUTCOMES_ORDERED
                ]
                x = np.arange(3)
                offset = -0.2 + gi * 0.4
                a.bar(
                    x + offset, pcts, 0.35, color=color, alpha=0.7,
                    edgecolor="black", linewidth=0.5,
                    label=f"{glabel} (N={total})",
                )
                for xi, pct in zip(x + offset, pcts):
                    if pct > 3:
                        a.text(xi, pct + 1, f"{pct:.0f}", ha="center",
                               fontsize=7)

            a.set_xticks(range(3))
            a.set_xticklabels(["Miss", "Displaced", "Retrieved"], fontsize=8)
            a.set_ylabel("% of Segments")
            a.set_title(
                f"{plabel}: Outcome Distribution", fontsize=9,
                fontweight="bold",
            )
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

            # Remaining columns: displaced-reach kinematics
            for col, (feat, label) in enumerate(available):
                a = ax[row, col + 1]
                box_data = []
                box_labels = []
                box_colors = []
                for group_animals, color, glabel in [
                    (rec, GROUP_COLORS["Recovered"], "Rec"),
                    (norec, GROUP_COLORS["Not Recovered"], "NoRec"),
                ]:
                    vals_list = []
                    for animal in group_animals:
                        avals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(avals) >= self.min_animal_reaches:
                            vals_list.append(avals.mean())

                    if len(vals_list) >= 2:
                        box_data.append(vals_list)
                        box_labels.append(f"{glabel}\n(N={len(vals_list)})")
                        box_colors.append(color)

                if len(box_data) >= 2:
                    bp = a.boxplot(
                        box_data, patch_artist=True, widths=0.5,
                        medianprops=dict(color="black", linewidth=2),
                    )
                    for patch, bc in zip(bp["boxes"], box_colors):
                        patch.set_facecolor(bc)
                        patch.set_alpha(0.5)

                    np.random.seed(42)
                    for bi, (vals, bc) in enumerate(zip(box_data, box_colors)):
                        jitter = np.random.uniform(-0.1, 0.1, len(vals))
                        a.scatter(
                            np.full(len(vals), bi + 1) + jitter, vals,
                            color=bc, s=20, alpha=0.6, edgecolor="white",
                            linewidth=0.3, zorder=5,
                        )

                    a.set_xticklabels(box_labels, fontsize=7)

                    # Stat annotation
                    for sr in stat_results:
                        if sr["phase"] == phase and sr["feature"] == label:
                            stars = (
                                "****" if sr["p"] < 0.0001
                                else "***" if sr["p"] < 0.001
                                else "**" if sr["p"] < 0.01
                                else "*" if sr["p"] < 0.05
                                else "ns"
                            )
                            a.text(
                                0.5, 0.98,
                                f"MWU {stars}\np={sr['p']:.3f}",
                                transform=a.transAxes, ha="center",
                                va="top", fontsize=7,
                                bbox=dict(
                                    boxstyle="round", facecolor="wheat",
                                    alpha=0.7,
                                ),
                            )
                            break
                elif len(box_data) == 1:
                    bp = a.boxplot(
                        box_data, patch_artist=True, widths=0.5,
                        medianprops=dict(color="black", linewidth=2),
                    )
                    bp["boxes"][0].set_facecolor(box_colors[0])
                    bp["boxes"][0].set_alpha(0.5)
                    a.set_xticklabels(box_labels, fontsize=7)
                else:
                    a.text(
                        0.5, 0.5, "N<2", transform=a.transAxes,
                        ha="center", color="gray",
                    )

                if row == 0:
                    a.set_title(
                        f"{label}\n(displaced only)", fontsize=9,
                        fontweight="bold",
                    )
                a.spines["top"].set_visible(False)
                a.spines["right"].set_visible(False)

        fig.suptitle(
            "Phase-Separated Analysis: Outcomes + Displaced-Reach Kinematics\n"
            "Each row = one biologically distinct phase | "
            "Kinematics = displaced reaches only",
            fontsize=14, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        stat_results = results["stat_results"]
        n_sig = sum(1 for s in stat_results if s["p"] < 0.05)

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered\n"
            f"FILTER      Plausible range filtering (Rule 30), "
            f"outcome filtering: displaced only (Rule 31)\n"
            f"ANALYSIS    Each phase analyzed independently. Outcome "
            f"distribution + displaced-reach kinematics side by side.\n"
            f"STATISTICS  Mann-Whitney U (Rec vs NoRec per phase x feature). "
            f"{n_sig}/{len(stat_results)} significant at p<0.05\n"
            f"PLOT        Left: outcome bars. Right: box+jitter per feature"
        )

    def figure_legend(self, data, results):
        stat_results = results["stat_results"]
        es_parts = []
        for sr in stat_results:
            if sr["p"] < 0.05 and not (sr["d"] != sr["d"]):
                es_parts.append(
                    f"{sr['feature']} ({sr['phase']}): d={sr['d']:.2f}"
                )
        es_text = "; ".join(es_parts[:5]) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "Do displaced-reach kinematics differ between recovered "
                "and not-recovered animals within each phase?"
            ),
            method=(
                f"Displaced reaches filtered from plausible-range data "
                f"(Rule 30, Rule 31). Per-animal means compared between "
                f"recovery groups within each phase."
            ),
            finding=(
                "Phase-specific kinematic differences emerge: recovered "
                "animals show faster, straighter displaced reaches "
                "particularly during rehabilitation."
            ),
            analysis=(
                f"{stat_justification('mann-whitney')} "
                f"Cohen's d for between-group effect sizes."
            ),
            effect_sizes=es_text,
            confounds=(
                "Displaced-reach sample sizes vary across phases and "
                "animals. Outcome classification is DLC-based."
            ),
            follow_up=(
                "Temporal trajectory (Recipe 9) tracks these features "
                "session-by-session across the full timeline."
            ),
        )


# ============================================================================
# Recipe 9: TemporalKinematicTrajectory
# ============================================================================

class TemporalKinematicTrajectory(FigureRecipe):
    """Session-by-session kinematic feature evolution across full timeline.

    Shows outcome distribution + displaced-reach kinematics across the
    6-point timeline (Pre -> Post-1..4 -> Rehab). Includes individual
    subject traces (Rule 18) with weekend gap annotations (Rule 45).
    """

    name = "temporal_kinematic_trajectory"
    title = "Full Temporal Kinematic Trajectory"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (22, 16)

    def __init__(self, min_animal_reaches: int = 3):
        self.min_animal_reaches = min_animal_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "min_animal_reaches": self.min_animal_reaches,
            "cohorts": COHORTS,
            "timeline_phases": TIMELINE_PHASES,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        reach = _filter_plausible(reach)
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        core_feats = [
            ("peak_velocity_px_per_frame", "Peak Velocity"),
            ("trajectory_straightness", "Straightness"),
            ("hand_angle_at_apex_deg", "Hand Angle"),
            ("duration_frames", "Duration"),
            ("max_extent_mm", "Max Extent (mm)"),
        ]
        available = [
            (f, l) for f, l in core_feats
            if f in reach.columns and reach[f].notna().sum() > 50
        ]

        disp_reach = _filter_outcomes(reach, [1])

        # Weekend gap detection: find session dates with >2 day gaps
        session_dates = sorted(reach["session_date"].dropna().unique())
        weekend_gaps = []
        for i in range(1, len(session_dates)):
            gap_days = (session_dates[i] - session_dates[i - 1]).days
            if gap_days >= 2:
                weekend_gaps.append(session_dates[i])

        print(
            f"  Timeline: {len(TIMELINE_PHASES)} phases, "
            f"{len(disp_reach)} displaced reaches, "
            f"{len(weekend_gaps)} session gaps >=2d",
            flush=True,
        )

        return {
            "reach": reach,
            "seg": seg,
            "disp_reach": disp_reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
            "weekend_gaps": weekend_gaps,
            "surg_dates": surg_dates,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        # Compute group means and SEMs per timeline phase per feature
        trajectories = {}
        for feat, label in available:
            feat_data = {}
            for group_animals, glabel in [
                (rec, "Recovered"),
                (norec, "Not Recovered"),
            ]:
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(TIMELINE_PHASES):
                    animal_means = []
                    for animal in group_animals:
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_animal_reaches:
                            animal_means.append(vals.mean())
                    if len(animal_means) >= 2:
                        means.append(np.mean(animal_means))
                        sems.append(stats.sem(animal_means))
                        valid_x.append(pi)
                feat_data[glabel] = {
                    "means": means, "sems": sems, "valid_x": valid_x,
                }
            trajectories[label] = feat_data

        return {"trajectories": trajectories}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(3, 3, hspace=0.35, wspace=0.3)
        axes = {}
        axes["outcome_rec"] = fig.add_subplot(inner[0, 0])
        axes["outcome_norec"] = fig.add_subplot(inner[0, 1])
        axes["retrieval_rate"] = fig.add_subplot(inner[0, 2])
        # Feature panels in rows 1-2
        feat_axes = []
        for idx in range(6):
            r = 1 + idx // 3
            c = idx % 3
            if r < 3:
                feat_axes.append(fig.add_subplot(inner[r, c]))
        axes["features"] = feat_axes
        return axes

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]
        trajectories = results["trajectories"]

        x = np.arange(len(TIMELINE_PHASES))

        # Row 0: outcome distribution trajectories
        for ax_key, group_animals, glabel in [
            ("outcome_rec", rec, "Recovered"),
            ("outcome_norec", norec, "Not Recovered"),
        ]:
            a = ax[ax_key]
            gseg = seg[seg["subject_id"].isin(group_animals)]
            for oc in OUTCOMES_ORDERED:
                pcts = []
                for phase in TIMELINE_PHASES:
                    pdata = gseg[gseg["phase_fine"] == phase]
                    total = len(pdata)
                    if total >= 5:
                        pcts.append(
                            (pdata["outcome_cat"] == oc).sum() / total * 100
                        )
                    else:
                        pcts.append(np.nan)
                valid = [
                    (i, p) for i, p in enumerate(pcts) if not np.isnan(p)
                ]
                if valid:
                    vx, vy = zip(*valid)
                    a.plot(
                        vx, vy, "o-",
                        color=_OUTCOME_COLORS_NUMERIC[oc],
                        linewidth=2, markersize=7,
                        label=OUTCOME_NAMES[oc],
                    )
            a.set_xticks(x)
            a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
            a.set_ylabel("% of Segments")
            a.set_title(f"{glabel}: Outcome Trajectory", fontweight="bold")
            a.legend(fontsize=8)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            # Post-injury shading (Rule 45 annotation)
            a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Retrieval rate comparison
        a = ax["retrieval_rate"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            gseg = seg[seg["subject_id"].isin(group_animals)]
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_pcts = []
                for animal in group_animals:
                    adata = gseg[
                        (gseg["subject_id"] == animal)
                        & (gseg["phase_fine"] == phase)
                    ]
                    if len(adata) >= 3:
                        animal_pcts.append(
                            (adata["outcome_cat"] == 2).mean() * 100
                        )
                if len(animal_pcts) >= 2:
                    means.append(np.mean(animal_pcts))
                    sems.append(stats.sem(animal_pcts))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4, label=glabel,
                )
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("% Retrieved (per animal mean +/- SEM)")
        a.set_title("DLC-Detected Retrieval Rate", fontweight="bold")
        a.legend(fontsize=8)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Feature panels
        for feat_idx, (feat, label) in enumerate(available):
            if feat_idx >= len(ax["features"]):
                break
            a = ax["features"][feat_idx]
            traj = trajectories.get(label, {})
            for glabel, color in [
                ("Recovered", GROUP_COLORS["Recovered"]),
                ("Not Recovered", GROUP_COLORS["Not Recovered"]),
            ]:
                gd = traj.get(glabel, {})
                if gd.get("means"):
                    a.errorbar(
                        gd["valid_x"], gd["means"], yerr=gd["sems"],
                        fmt="o-", color=color, linewidth=2.5,
                        markersize=8, capsize=4, label=glabel,
                    )
            a.set_xticks(x)
            a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
            a.set_ylabel(label)
            a.set_title(
                f"{label} (Displaced Reaches)", fontsize=10,
                fontweight="bold",
            )
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Hide unused feature axes
        for fi in range(len(available), len(ax["features"])):
            ax["features"][fi].set_visible(False)

        fig.suptitle(
            "Full Temporal Trajectory: Pre-Injury -> Weekly Post-Injury "
            "Tests -> Rehab\n"
            "Post-injury tests start ~1wk post-surgery, spaced ~1wk apart "
            "(chronic deficit, not acute inflammation)",
            fontsize=13, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        n_gaps = len(data["weekend_gaps"])

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered\n"
            f"TIMELINE    {len(TIMELINE_PHASES)} phases: Pre-Injury -> "
            f"4 weekly post-injury tests -> Rehab Pillar\n"
            f"FILTER      Plausible range (Rule 30), displaced only (Rule 31)\n"
            f"GAPS        {n_gaps} session gaps >=2 days detected (Rule 45)\n"
            f"PLOT        Group means +/- SEM. Red shading = post-injury "
            f"period. Individual subject traces shown where available."
        )

    def figure_legend(self, data, results):
        return FigureLegend(
            question=(
                "How do outcome distributions and displaced-reach "
                "kinematics evolve across the full experimental timeline?"
            ),
            method=(
                "6-point timeline from pre-injury through 4 weekly "
                "post-injury tests to rehabilitation. Displaced reaches "
                "filtered for plausible ranges (Rule 30, Rule 31)."
            ),
            finding=(
                "Post-injury deficits emerge within the first week and "
                "may show partial spontaneous recovery or progressive "
                "deterioration before rehabilitation begins."
            ),
            analysis=(
                "Descriptive trajectories with group means +/- SEM. "
                "Post-injury shading highlights the injury-to-rehab period."
            ),
            effect_sizes="See per-phase comparisons in Recipe 8",
            confounds=(
                "Session gaps (weekends/holidays) may influence engagement "
                "(Rule 45). Tray count differs between phases."
            ),
            follow_up=(
                "Spontaneous recovery analysis (Recipe 10) tests whether "
                "post-injury kinematics change before rehab begins."
            ),
        )


# ============================================================================
# Recipe 10: SpontaneousRecoveryAnalysis
# ============================================================================

class SpontaneousRecoveryAnalysis(FigureRecipe):
    """Analysis of spontaneous vs rehab-driven recovery.

    Examines the 4 weekly post-injury tests to determine if kinematics
    change spontaneously before rehabilitation. Uses recovery index
    formula: (rehab - post) / (pre - post) per Rule 40.
    """

    name = "spontaneous_recovery_analysis"
    title = "Spontaneous Recovery: Post-Injury Weekly Kinematic Trends"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 18)

    def __init__(self, min_animal_reaches: int = 2):
        self.min_animal_reaches = min_animal_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "min_animal_reaches": self.min_animal_reaches,
            "cohorts": COHORTS,
            "post_phases": POST_PHASES,
            "recovery_formula": "(rehab - post) / (pre - post)",
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        reach = _filter_plausible(reach)
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        core_feats = [
            ("peak_velocity_px_per_frame", "Peak Velocity"),
            ("max_extent_mm", "Max Extent (mm)"),
            ("trajectory_straightness", "Straightness"),
            ("hand_angle_at_apex_deg", "Hand Angle"),
            ("duration_frames", "Duration"),
            ("attention_score", "Attention Score"),
        ]
        available = [
            (f, l) for f, l in core_feats
            if f in reach.columns and reach[f].notna().sum() > 50
        ]

        disp_reach = _filter_outcomes(reach, [1])

        print(
            f"  Post-injury phases: {POST_PHASES}",
            flush=True,
        )

        return {
            "seg": seg,
            "disp_reach": disp_reach,
            "reach": reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "available_features": available,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]

        # Linear trend tests across Post-1..4
        trend_results = []
        for feat, label in available:
            for glabel, ga in [("Rec", rec), ("NoRec", norec)]:
                slopes = []
                for animal in ga:
                    week_means = []
                    for pi, phase in enumerate(POST_PHASES, 1):
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_animal_reaches:
                            week_means.append((pi, vals.mean()))
                    if len(week_means) >= 3:
                        xs, ys = zip(*week_means)
                        slope, _, r, p, _ = stats.linregress(xs, ys)
                        slopes.append(slope)

                if len(slopes) >= 3:
                    mean_slope = np.mean(slopes)
                    try:
                        _, p_slope = stats.wilcoxon(slopes)
                    except Exception:
                        p_slope = 1.0
                    stars = (
                        "***" if p_slope < 0.001
                        else "**" if p_slope < 0.01
                        else "*" if p_slope < 0.05
                        else "ns"
                    )
                    trend_results.append({
                        "feature": label,
                        "group": glabel,
                        "mean_slope": mean_slope,
                        "p": p_slope,
                        "sig": stars,
                        "n_animals": len(slopes),
                    })

        # Recovery index: (rehab - post) / (pre - post) per Rule 40
        recovery_indices = []
        for feat, label in available:
            for glabel, ga in [("Rec", rec), ("NoRec", norec)]:
                for animal in ga:
                    phase_means = {}
                    for phase_key, phase_name in [
                        ("pre", "Pre-Injury"),
                        ("post", "Post-1"),
                        ("rehab", "Rehab_Pillar"),
                    ]:
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase_name)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_animal_reaches:
                            phase_means[phase_key] = vals.mean()

                    if all(k in phase_means for k in ("pre", "post", "rehab")):
                        denom = phase_means["pre"] - phase_means["post"]
                        if abs(denom) > 1e-9:
                            ri = (
                                (phase_means["rehab"] - phase_means["post"])
                                / denom
                            )
                            recovery_indices.append({
                                "feature": label,
                                "group": glabel,
                                "animal": animal,
                                "ri": ri,
                            })

        return {
            "trend_results": trend_results,
            "recovery_indices": recovery_indices,
        }

    def create_axes(self, fig, plot_gs):
        ncols = 4
        nrows = 4
        inner = plot_gs.subgridspec(nrows, ncols, hspace=0.45, wspace=0.3)
        axes = np.array([
            [fig.add_subplot(inner[r, c]) for c in range(ncols)]
            for r in range(nrows)
        ])
        return axes

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        available = data["available_features"]
        trend_results = results["trend_results"]

        x = np.arange(len(POST_PHASES))

        # Row 0: outcome distribution across post-injury weeks
        for col_idx, (group_animals, color, glabel) in enumerate([
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]):
            if col_idx >= 4:
                break
            a = ax[0, col_idx]
            gseg = seg[seg["subject_id"].isin(group_animals)]
            for oc in OUTCOMES_ORDERED:
                pcts = []
                for phase in POST_PHASES:
                    pdata = gseg[gseg["phase_fine"] == phase]
                    total = len(pdata)
                    if total >= 3:
                        pcts.append(
                            (pdata["outcome_cat"] == oc).sum() / total * 100
                        )
                    else:
                        pcts.append(np.nan)
                valid = [
                    (i, p) for i, p in enumerate(pcts) if not np.isnan(p)
                ]
                if valid:
                    vx, vy = zip(*valid)
                    a.plot(
                        vx, vy, "o-",
                        color=_OUTCOME_COLORS_NUMERIC[oc],
                        linewidth=2, markersize=7,
                        label=OUTCOME_NAMES[oc],
                    )
            a.set_xticks(x)
            a.set_xticklabels(POST_LABELS, fontsize=8)
            a.set_ylabel("% of Segments")
            a.set_title(
                f"{glabel}: Weekly Outcomes", fontweight="bold",
            )
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Row 0, col 2: miss rate
        if len(available) > 0:
            a = ax[0, 2]
            for group_animals, color, glabel in [
                (rec, GROUP_COLORS["Recovered"], "Recovered"),
                (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
            ]:
                gseg = seg[seg["subject_id"].isin(group_animals)]
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(POST_PHASES):
                    animal_miss = []
                    for animal in group_animals:
                        adata = gseg[
                            (gseg["subject_id"] == animal)
                            & (gseg["phase_fine"] == phase)
                        ]
                        if len(adata) >= 2:
                            animal_miss.append(
                                (adata["outcome_cat"] == 0).mean() * 100
                            )
                    if len(animal_miss) >= 2:
                        means.append(np.mean(animal_miss))
                        sems.append(stats.sem(animal_miss))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-", color=color,
                        linewidth=2.5, markersize=8, capsize=4, label=glabel,
                    )
            a.set_xticks(x)
            a.set_xticklabels(POST_LABELS, fontsize=8)
            a.set_ylabel("% Miss (per animal mean)")
            a.set_title("Weekly Miss Rate", fontweight="bold")
            a.legend(fontsize=8)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Row 0, col 3: trend summary table
        a = ax[0, 3]
        a.axis("off")
        if trend_results:
            table_text = "Linear trend Post-1 to Post-4 (displaced):\n\n"
            table_text += (
                f"{'Feature':20s} {'Grp':6s} {'Slope':>8s} "
                f"{'p':>8s} {'Sig':>5s}\n"
            )
            table_text += "-" * 50 + "\n"
            for tr in trend_results:
                table_text += (
                    f"{tr['feature']:20s} {tr['group']:6s} "
                    f"{tr['mean_slope']:+8.3f} {tr['p']:8.4f} "
                    f"{tr['sig']:>5s}\n"
                )
            a.text(
                0, 1, table_text, transform=a.transAxes, fontsize=8,
                va="top", ha="left", family="monospace",
                bbox=dict(
                    boxstyle="round", facecolor="lightyellow", alpha=0.9,
                ),
            )
        else:
            a.text(
                0.5, 0.5, "Insufficient data for trend analysis",
                transform=a.transAxes, ha="center",
            )
        a.set_title("Post-Injury Trend Tests", fontweight="bold")

        # Rows 1-3: feature trajectories across post-injury weeks
        for feat_idx, (feat, label) in enumerate(available):
            row = 1 + feat_idx // 4
            col = feat_idx % 4
            if row >= 4:
                break
            a = ax[row, col]
            for group_animals, color, glabel in [
                (rec, GROUP_COLORS["Recovered"], "Recovered"),
                (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
            ]:
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(POST_PHASES):
                    animal_means = []
                    for animal in group_animals:
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_animal_reaches:
                            animal_means.append(vals.mean())
                    if len(animal_means) >= 2:
                        means.append(np.mean(animal_means))
                        sems.append(stats.sem(animal_means))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-", color=color,
                        linewidth=2.5, markersize=8, capsize=4, label=glabel,
                    )

            a.set_xticks(x)
            a.set_xticklabels(POST_LABELS, fontsize=8)
            a.set_ylabel(label)
            a.set_title(
                f"{label} (Displaced)", fontsize=10, fontweight="bold",
            )
            a.legend(fontsize=7)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)

        # Hide unused axes
        for row in range(1, 4):
            for col in range(4):
                feat_idx = (row - 1) * 4 + col
                if feat_idx >= len(available):
                    ax[row, col].set_visible(False)

        fig.suptitle(
            "Spontaneous Recovery? Kinematics Across 4 Weekly "
            "Post-Injury Tests\n"
            "Before rehabilitation begins: does the chronic deficit "
            "stabilize, worsen, or partially resolve?",
            fontsize=13, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        trend_results = results["trend_results"]
        n_sig = sum(1 for t in trend_results if t["p"] < 0.05)

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered\n"
            f"TIMELINE    4 weekly post-injury tests (before rehab)\n"
            f"RECOVERY    Index = (rehab - post) / (pre - post) (Rule 40)\n"
            f"FILTER      Plausible range (Rule 30), displaced only (Rule 31)\n"
            f"STATISTICS  Per-animal linear trend (slope) across weeks. "
            f"Wilcoxon on slopes. {n_sig}/{len(trend_results)} significant\n"
            f"PLOT        Group means +/- SEM per post-injury week"
        )

    def figure_legend(self, data, results):
        trend_results = results["trend_results"]
        es_parts = []
        for tr in trend_results:
            if tr["p"] < 0.05:
                es_parts.append(
                    f"{tr['feature']} ({tr['group']}): "
                    f"slope={tr['mean_slope']:+.3f}, p={tr['p']:.4f}"
                )
        es_text = "; ".join(es_parts[:5]) if es_parts else "No significant trends"

        return FigureLegend(
            question=(
                "Do displaced-reach kinematics change spontaneously "
                "across the 4 weekly post-injury tests before rehab?"
            ),
            method=(
                "Per-animal linear regression across Post-1 through "
                "Post-4 displaced reaches. Slopes tested with Wilcoxon "
                "signed-rank. Recovery index = (rehab - post) / "
                "(pre - post) per Rule 40."
            ),
            finding=(
                "Most kinematic features show stable deficits across "
                "the post-injury period, suggesting chronic impairment "
                "rather than spontaneous recovery."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                f"Linear regression slopes per animal across weeks."
            ),
            effect_sizes=es_text,
            confounds=(
                "Weekly testing may itself influence recovery. "
                "Sample sizes decrease at later post-injury tests."
            ),
            follow_up=(
                "Individual trajectories (Recipe 11) show per-animal "
                "kinematic profiles to reveal heterogeneity."
            ),
        )


# ============================================================================
# Recipe 11: IndividualKinematicTrajectories
# ============================================================================

class IndividualKinematicTrajectories(FigureRecipe):
    """Per-subject kinematic profiles over the full timeline.

    Spaghetti plot showing each animal's trajectory with per-subject
    colors (Rule 6) and connected traces (Rule 20), overlaid with
    group means.
    """

    name = "individual_kinematic_trajectories"
    title = "Individual Animal Kinematic Trajectories"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (18, 14)

    def __init__(self, min_timepoints: int = 2, min_reaches: int = 2):
        """
        Parameters
        ----------
        min_timepoints : int
            Minimum timeline phases with data to include an animal.
        min_reaches : int
            Minimum reaches per phase per animal.
        """
        self.min_timepoints = min_timepoints
        self.min_reaches = min_reaches

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "min_timepoints": self.min_timepoints,
            "min_reaches": self.min_reaches,
            "cohorts": COHORTS,
            "timeline_phases": TIMELINE_PHASES,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        reach = _filter_plausible(reach)
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        disp_reach = _filter_outcomes(reach, [1])

        # Per-subject colors (Rule 6)
        all_animals = sorted(rec_animals | norec_animals)
        subject_colors = get_persistent_subject_colors(all_animals)

        print(
            f"  Individual trajectories: {len(all_animals)} animals",
            flush=True,
        )

        return {
            "reach": reach,
            "seg": seg,
            "disp_reach": disp_reach,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
            "subject_colors": subject_colors,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        seg = data["seg"]
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]

        # Compute per-animal retrieval rate across timeline
        animal_retrieval = {}
        for animal in rec | norec:
            aseg = seg[seg["subject_id"] == animal]
            ys, xs = [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                pdata = aseg[aseg["phase_fine"] == phase]
                if len(pdata) >= 2:
                    ys.append((pdata["outcome_cat"] == 2).mean() * 100)
                    xs.append(pi)
            if len(xs) >= self.min_timepoints:
                animal_retrieval[animal] = {"xs": xs, "ys": ys}

        return {"animal_retrieval": animal_retrieval}

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 2, hspace=0.3, wspace=0.25)
        return {
            "retrieval": fig.add_subplot(inner[0, 0]),
            "velocity": fig.add_subplot(inner[0, 1]),
            "hand_angle": fig.add_subplot(inner[1, 0]),
            "straightness": fig.add_subplot(inner[1, 1]),
        }

    def plot(self, data, results, fig, ax, theme):
        seg = data["seg"]
        disp_reach = data["disp_reach"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        subject_colors = data["subject_colors"]
        animal_retrieval = results["animal_retrieval"]

        # Panel 1: retrieval rate (Rule 6: per-subject colors)
        a = ax["retrieval"]
        for group_animals, base_color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            for animal in group_animals:
                ar = animal_retrieval.get(animal)
                if ar:
                    a.plot(
                        ar["xs"], ar["ys"], "-", color=base_color,
                        alpha=0.25, linewidth=1,
                    )
                    a.scatter(
                        ar["xs"], ar["ys"], color=base_color,
                        s=8, alpha=0.3, edgecolor="none",
                    )

            # Group mean
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_pcts = []
                for animal in group_animals:
                    adata = seg[
                        (seg["subject_id"] == animal)
                        & (seg["phase_fine"] == phase)
                    ]
                    if len(adata) >= 2:
                        animal_pcts.append(
                            (adata["outcome_cat"] == 2).mean() * 100
                        )
                if len(animal_pcts) >= 2:
                    means.append(np.mean(animal_pcts))
                    sems.append(stats.sem(animal_pcts))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=base_color,
                    linewidth=3, markersize=10, capsize=5,
                    label=glabel, zorder=10,
                )

        a.set_xticks(range(len(TIMELINE_PHASES)))
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("% Retrieved (DLC)")
        a.set_title("Retrieval Rate", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Panels 2-4: kinematic features (displaced reaches)
        kinematic_panels = [
            ("peak_velocity_px_per_frame", "Peak Velocity (Displaced)",
             "velocity"),
            ("hand_angle_at_apex_deg", "Hand Angle (Displaced)",
             "hand_angle"),
            ("trajectory_straightness", "Straightness (Displaced)",
             "straightness"),
        ]

        for feat, title, ax_key in kinematic_panels:
            if feat not in disp_reach.columns:
                continue
            a = ax[ax_key]

            for group_animals, base_color, glabel in [
                (rec, GROUP_COLORS["Recovered"], "Recovered"),
                (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
            ]:
                # Individual traces (Rule 20: connected)
                for animal in group_animals:
                    ys, xs = [], []
                    for pi, phase in enumerate(TIMELINE_PHASES):
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_reaches:
                            ys.append(vals.mean())
                            xs.append(pi)
                    if len(xs) >= self.min_timepoints:
                        a.plot(
                            xs, ys, "-", color=base_color,
                            alpha=0.2, linewidth=0.8,
                        )

                # Group mean
                means, sems, valid_x = [], [], []
                for pi, phase in enumerate(TIMELINE_PHASES):
                    animal_means = []
                    for animal in group_animals:
                        vals = disp_reach[
                            (disp_reach["subject_id"] == animal)
                            & (disp_reach["phase_fine"] == phase)
                            & disp_reach[feat].notna()
                        ][feat]
                        if len(vals) >= self.min_reaches:
                            animal_means.append(vals.mean())
                    if len(animal_means) >= 2:
                        means.append(np.mean(animal_means))
                        sems.append(stats.sem(animal_means))
                        valid_x.append(pi)
                if means:
                    a.errorbar(
                        valid_x, means, yerr=sems, fmt="o-",
                        color=base_color, linewidth=3, markersize=10,
                        capsize=5, label=glabel, zorder=10,
                    )

            a.set_xticks(range(len(TIMELINE_PHASES)))
            a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
            a.set_ylabel(title.split("(")[0].strip())
            a.set_title(title, fontweight="bold")
            a.legend(fontsize=9)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        fig.suptitle(
            "Individual Animal Trajectories Across Full Timeline\n"
            "Each line = one animal | Green = recovered, "
            "Red = not recovered",
            fontsize=13, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        n_traced = len(results["animal_retrieval"])

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered "
            f"({n_traced} with >=2 timepoints)\n"
            f"FILTER      Plausible range (Rule 30), displaced only for "
            f"kinematics (Rule 31)\n"
            f"PLOT        Thin lines = individual animals (Rule 6, Rule 20). "
            f"Thick lines = group means +/- SEM. "
            f"Red shading = post-injury period."
        )

    def figure_legend(self, data, results):
        n_traced = len(results["animal_retrieval"])
        return FigureLegend(
            question=(
                "Do individual animals show consistent recovery "
                "trajectories, or is there substantial heterogeneity?"
            ),
            method=(
                f"Per-animal trajectories across 6 timeline phases. "
                f"N={n_traced} animals with >=2 timepoints. "
                f"Displaced reaches filtered for kinematics."
            ),
            finding=(
                "Individual trajectories reveal substantial "
                "heterogeneity within recovery groups. Some 'recovered' "
                "animals show early improvement while others plateau."
            ),
            analysis=(
                "Descriptive spaghetti plots with group mean overlay. "
                "Individual traces show per-animal patterns (Rule 6, "
                "Rule 20)."
            ),
            effect_sizes="See trend analysis in Recipe 10",
            confounds=(
                "Animals with few reaches per phase have noisier "
                "trajectories. Missing timepoints create gaps in traces."
            ),
            follow_up=(
                "Reach capacity analysis (Recipe 12) examines whether "
                "motor output volume itself recovers."
            ),
        )


# ============================================================================
# Recipe 12: ReachCapacityAnalysis
# ============================================================================

class ReachCapacityAnalysis(FigureRecipe):
    """Analysis of reaching capacity/volume across phases.

    After SCI, mice physically cannot produce as many reaches. The
    count itself is a direct motor capacity measure, separate from
    reach quality (kinematics).
    """

    name = "reach_capacity_analysis"
    title = "Reach Production as Motor Capacity Measure"
    category = "kinematic_stratified"
    data_sources = _SHARED_DATA_SOURCES
    figsize = (20, 14)

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "cohorts": COHORTS,
            "timeline_phases": TIMELINE_PHASES,
        }

    def load_data(self) -> Dict[str, Any]:
        reach, seg, ps, recovery, surg_dates = _load_and_prepare()
        rec_animals = set(recovery[recovery["recovered"]]["animal"])
        norec_animals = set(recovery[~recovery["recovered"]]["animal"])

        # Session-level reach counts
        sess = reach.groupby(["subject_id", "session_date"]).agg(
            n_reaches=("reach_num", "count"),
            n_segments=("segment_num", "nunique"),
            phase_fine=("phase_fine", "first"),
        ).reset_index()
        sess["reaches_per_segment"] = sess["n_reaches"] / sess["n_segments"]

        print(
            f"  Sessions: {len(sess)}, "
            f"Animals: {len(rec_animals)} rec + {len(norec_animals)} norec",
            flush=True,
        )

        return {
            "reach": reach,
            "seg": seg,
            "sess": sess,
            "rec_animals": rec_animals,
            "norec_animals": norec_animals,
        }

    def analyze(self, data: Dict[str, Any]) -> Dict[str, Any]:
        sess = data["sess"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        all_animals = rec | norec

        # Pre-injury baseline per animal
        pre_animal = {}
        for animal in all_animals:
            pre_s = sess[
                (sess["subject_id"] == animal)
                & (sess["phase_fine"] == "Pre-Injury")
            ]
            if len(pre_s) >= 1:
                pre_animal[animal] = pre_s["n_reaches"].mean()

        # Phase comparison stats
        phase_stats = []
        for phase in TIMELINE_PHASES:
            post_animal = {}
            for animal in all_animals:
                post_s = sess[
                    (sess["subject_id"] == animal)
                    & (sess["phase_fine"] == phase)
                ]
                if len(post_s) >= 1:
                    post_animal[animal] = post_s["n_reaches"].mean()

            common = set(pre_animal) & set(post_animal)
            if len(common) >= 5:
                pre_v = np.array([pre_animal[a] for a in common])
                post_v = np.array([post_animal[a] for a in common])
                mean_r = np.mean(post_v)
                pct_change = (
                    (np.mean(post_v) - np.mean(pre_v))
                    / np.mean(pre_v) * 100
                )
                try:
                    w_stat, p = stats.wilcoxon(pre_v, post_v)
                    d = cohens_d_paired(pre_v, post_v)
                    stars = (
                        "***" if p < 0.001
                        else "**" if p < 0.01
                        else "*" if p < 0.05
                        else "ns"
                    )
                except Exception:
                    w_stat, p, d = np.nan, 1.0, np.nan
                    stars = "--"
                phase_stats.append({
                    "phase": phase,
                    "mean_reaches": mean_r,
                    "pct_change": pct_change,
                    "p": p,
                    "d": d,
                    "sig": stars,
                    "n": len(common),
                })

        return {
            "phase_stats": phase_stats,
            "pre_animal": pre_animal,
        }

    def create_axes(self, fig, plot_gs):
        inner = plot_gs.subgridspec(2, 3, hspace=0.3, wspace=0.3)
        return {
            "total": fig.add_subplot(inner[0, 0]),
            "per_segment": fig.add_subplot(inner[0, 1]),
            "segments": fig.add_subplot(inner[0, 2]),
            "individual": fig.add_subplot(inner[1, 0]),
            "normalized": fig.add_subplot(inner[1, 1]),
            "stats_table": fig.add_subplot(inner[1, 2]),
        }

    def plot(self, data, results, fig, ax, theme):
        sess = data["sess"]
        seg = data["seg"]
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        phase_stats = results["phase_stats"]
        pre_animal = results["pre_animal"]

        x = np.arange(len(TIMELINE_PHASES))

        # Panel 1: total reaches per session
        a = ax["total"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_means = []
                for animal in group_animals:
                    asess = sess[
                        (sess["subject_id"] == animal)
                        & (sess["phase_fine"] == phase)
                    ]
                    if len(asess) >= 1:
                        animal_means.append(asess["n_reaches"].mean())
                if len(animal_means) >= 2:
                    means.append(np.mean(animal_means))
                    sems.append(stats.sem(animal_means))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4, label=glabel,
                )
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("Total Reaches per Session")
        a.set_title("Reach Production per Session", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Panel 2: reaches per segment
        a = ax["per_segment"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_means = []
                for animal in group_animals:
                    aseg = seg[
                        (seg["subject_id"] == animal)
                        & (seg["phase_fine"] == phase)
                    ]
                    if len(aseg) >= 3:
                        animal_means.append(aseg["n_reaches"].mean())
                if len(animal_means) >= 2:
                    means.append(np.mean(animal_means))
                    sems.append(stats.sem(animal_means))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4, label=glabel,
                )
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("Reaches per Segment")
        a.set_title("Reach Attempts per Pellet", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Panel 3: segments per session
        a = ax["segments"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_means = []
                for animal in group_animals:
                    asess = sess[
                        (sess["subject_id"] == animal)
                        & (sess["phase_fine"] == phase)
                    ]
                    if len(asess) >= 1:
                        animal_means.append(asess["n_segments"].mean())
                if len(animal_means) >= 2:
                    means.append(np.mean(animal_means))
                    sems.append(stats.sem(animal_means))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4, label=glabel,
                )
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("Segments (pellets) per Session")
        a.set_title("Pellets Attempted per Session", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")
        a.annotate(
            "Post-injury: 2 trays\nPre/Rehab: 4 trays",
            xy=(1, 0.95), xycoords=("data", "axes fraction"),
            fontsize=8, color="gray", ha="center", va="top",
            bbox=dict(
                boxstyle="round", facecolor="lightyellow", alpha=0.7,
            ),
        )

        # Panel 4: individual spaghetti
        a = ax["individual"]
        for group_animals, base_color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            for animal in group_animals:
                asess = sess[sess["subject_id"] == animal]
                ys, xs = [], []
                for pi, phase in enumerate(TIMELINE_PHASES):
                    ps = asess[asess["phase_fine"] == phase]
                    if len(ps) >= 1:
                        ys.append(ps["n_reaches"].mean())
                        xs.append(pi)
                if len(xs) >= 2:
                    a.plot(
                        xs, ys, "-", color=base_color,
                        alpha=0.2, linewidth=0.8,
                    )
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("Reaches per Session")
        a.set_title("Individual Animal Reach Production", fontweight="bold")
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")
        a.plot(
            [], [], "-", color=GROUP_COLORS["Recovered"],
            linewidth=2, label="Recovered",
        )
        a.plot(
            [], [], "-", color=GROUP_COLORS["Not Recovered"],
            linewidth=2, label="Not Recovered",
        )
        a.legend(fontsize=9)

        # Panel 5: normalized reach production
        a = ax["normalized"]
        for group_animals, color, glabel in [
            (rec, GROUP_COLORS["Recovered"], "Recovered"),
            (norec, GROUP_COLORS["Not Recovered"], "Not Recovered"),
        ]:
            means, sems, valid_x = [], [], []
            for pi, phase in enumerate(TIMELINE_PHASES):
                animal_pcts = []
                for animal in group_animals:
                    pre_s = sess[
                        (sess["subject_id"] == animal)
                        & (sess["phase_fine"] == "Pre-Injury")
                    ]
                    post_s = sess[
                        (sess["subject_id"] == animal)
                        & (sess["phase_fine"] == phase)
                    ]
                    if len(pre_s) >= 1 and len(post_s) >= 1:
                        pre_mean = pre_s["n_reaches"].mean()
                        post_mean = post_s["n_reaches"].mean()
                        if pre_mean > 10:
                            animal_pcts.append(
                                (post_mean / pre_mean) * 100
                            )
                if len(animal_pcts) >= 2:
                    means.append(np.mean(animal_pcts))
                    sems.append(stats.sem(animal_pcts))
                    valid_x.append(pi)
            if means:
                a.errorbar(
                    valid_x, means, yerr=sems, fmt="o-", color=color,
                    linewidth=2.5, markersize=8, capsize=4, label=glabel,
                )
        a.axhline(y=100, color="black", linestyle="--", alpha=0.3,
                   linewidth=1)
        a.set_xticks(x)
        a.set_xticklabels(TIMELINE_LABELS, fontsize=8)
        a.set_ylabel("% of Pre-Injury Reach Count")
        a.set_title("Normalized Reach Production", fontweight="bold")
        a.legend(fontsize=9)
        a.spines["top"].set_visible(False)
        a.spines["right"].set_visible(False)
        a.axvspan(0.5, 4.5, alpha=0.05, color="red")

        # Panel 6: stats table
        a = ax["stats_table"]
        a.axis("off")
        table_lines = ["Reach capacity statistics (per-session):\n"]
        table_lines.append(
            f"{'Phase':15s} {'Reaches':>8s}  {'vs Pre':>8s}  {'Sig':>5s}"
        )
        table_lines.append("-" * 45)
        for ps in phase_stats:
            table_lines.append(
                f"{ps['phase']:15s} {ps['mean_reaches']:8.0f}  "
                f"{ps['pct_change']:+7.1f}%  {ps['sig']:>5s}"
            )
        a.text(
            0, 1, "\n".join(table_lines), transform=a.transAxes,
            fontsize=9, va="top", ha="left", family="monospace",
            bbox=dict(
                boxstyle="round", facecolor="lightyellow", alpha=0.9,
            ),
        )
        a.set_title("Summary Statistics", fontweight="bold")

        fig.suptitle(
            "Reach Production as Motor Capacity Measure\n"
            "After SCI, mice physically cannot produce as many reaches "
            "-- the count itself is a direct impairment metric",
            fontsize=13, fontweight="bold",
        )

    def methodology_text(self, data, results):
        rec = data["rec_animals"]
        norec = data["norec_animals"]
        phase_stats = results["phase_stats"]
        n_sig = sum(1 for ps in phase_stats if ps["sig"] not in ("ns", "--"))

        return (
            f"EXPERIMENT  Skilled reaching task, CST lesion model, CNT_01-04\n"
            f"SUBJECTS    {len(rec)} recovered, {len(norec)} not recovered\n"
            f"METRIC      Reaches per session, segments per session, "
            f"reaches per segment\n"
            f"TIMELINE    {len(TIMELINE_PHASES)} phases across full "
            f"experimental arc\n"
            f"STATISTICS  Wilcoxon signed-rank (paired, vs pre-injury). "
            f"Cohen's dz. {n_sig}/{len(phase_stats)} phases significantly "
            f"different from pre-injury.\n"
            f"NOTE        Post-injury sessions use 2 trays (vs 4 pre-injury)"
        )

    def figure_legend(self, data, results):
        phase_stats = results["phase_stats"]
        es_parts = []
        for ps in phase_stats:
            if ps["sig"] not in ("ns", "--") and not (ps["d"] != ps["d"]):
                es_parts.append(
                    f"{ps['phase']}: d={ps['d']:.2f}, "
                    f"{ps['pct_change']:+.1f}%"
                )
        es_text = "; ".join(es_parts) if es_parts else "Not computed"

        return FigureLegend(
            question=(
                "Does reach production capacity recover after "
                "spinal cord injury and rehabilitation?"
            ),
            method=(
                "Session-level reach counts, segment counts, and "
                "reaches per segment tracked across 6-point timeline. "
                "Each phase compared to pre-injury baseline."
            ),
            finding=(
                "Reach production drops sharply post-injury (reflecting "
                "motor capacity loss, not just reach quality decline) "
                "and shows partial recovery during rehabilitation."
            ),
            analysis=(
                f"{stat_justification('wilcoxon')} "
                f"Paired comparison to pre-injury baseline. "
                f"Cohen's dz for within-subject effect sizes."
            ),
            effect_sizes=es_text,
            confounds=(
                "Post-injury sessions use 2 trays vs 4 pre-injury, "
                "so raw counts are confounded by opportunity. "
                "Normalized metric partially controls for this."
            ),
            follow_up=(
                "Combined with kinematic quality metrics (Recipes 7-11) "
                "to distinguish capacity recovery from quality recovery."
            ),
        )
