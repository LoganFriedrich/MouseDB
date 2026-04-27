"""
Statistical utilities for Connectome figures.

Provides effect size calculations, stat result formatting, test
justification text, and baseline normalization with floor.

Rules enforced:
    26 - Effect sizes (Cohen's d) mandatory for every significant result
    28 - Baseline normalization needs a floor
    34 - Every analysis must justify its statistical approach
"""

import numpy as np
import pandas as pd


# =============================================================================
# Effect size calculations
# =============================================================================

def cohens_d(group1, group2):
    """Compute Cohen's d (pooled SD) for two independent groups.

    Parameters
    ----------
    group1, group2 : array-like
        Values for each group.

    Returns
    -------
    float : Cohen's d (positive means group1 > group2).
    """
    g1 = np.asarray(group1, dtype=float)
    g2 = np.asarray(group2, dtype=float)
    g1 = g1[~np.isnan(g1)]
    g2 = g2[~np.isnan(g2)]

    n1, n2 = len(g1), len(g2)
    if n1 < 2 or n2 < 2:
        return float("nan")

    var1 = np.var(g1, ddof=1)
    var2 = np.var(g2, ddof=1)
    pooled_sd = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))

    if pooled_sd == 0:
        return float("nan")

    return (np.mean(g1) - np.mean(g2)) / pooled_sd


def cohens_d_paired(pre, post):
    """Compute Cohen's d for paired/repeated measures (dz).

    Uses the SD of the differences as the denominator, which is
    appropriate for within-subject designs.

    Parameters
    ----------
    pre, post : array-like
        Paired measurements (same subjects, same order).

    Returns
    -------
    float : Cohen's dz (positive means pre > post).
    """
    pre = np.asarray(pre, dtype=float)
    post = np.asarray(post, dtype=float)

    # Remove pairs with NaN in either
    mask = ~(np.isnan(pre) | np.isnan(post))
    pre, post = pre[mask], post[mask]

    if len(pre) < 2:
        return float("nan")

    diffs = pre - post
    sd_diff = np.std(diffs, ddof=1)

    if sd_diff == 0:
        return float("nan")

    return np.mean(diffs) / sd_diff


def interpret_d(d):
    """Interpret Cohen's d magnitude.

    Parameters
    ----------
    d : float
        Absolute Cohen's d value.

    Returns
    -------
    str : "negligible", "small", "medium", "large", or "very large".
    """
    d_abs = abs(d)
    if d_abs < 0.2:
        return "negligible"
    elif d_abs < 0.5:
        return "small"
    elif d_abs < 0.8:
        return "medium"
    elif d_abs < 1.2:
        return "large"
    else:
        return "very large"


# =============================================================================
# Stat result formatting
# =============================================================================

def format_stat_result(test_name, stat, p, d=None, n=None,
                       alternative="two-sided"):
    """Format a complete stat result string.

    Parameters
    ----------
    test_name : str
        Name of the test (e.g., "Wilcoxon signed-rank").
    stat : float
        Test statistic.
    p : float
        P-value.
    d : float, optional
        Effect size (Cohen's d).
    n : int, optional
        Sample size.
    alternative : str
        "two-sided", "greater", or "less".

    Returns
    -------
    str : Formatted result string.

    Example
    -------
    >>> format_stat_result("Wilcoxon signed-rank", 12.0, 0.003, d=1.82, n=11, alternative="greater")
    "Wilcoxon signed-rank: W=12.0, p=0.003, d=1.82 (large) (n=11, one-sided greater)"
    """
    parts = [f"{test_name}:"]

    # Stat name varies by test
    stat_letter = _stat_letter(test_name)
    parts.append(f"{stat_letter}={stat:.1f},")
    parts.append(f"p={p:.4f},")

    if d is not None and not np.isnan(d):
        interp = interpret_d(d)
        parts.append(f"d={d:.2f} ({interp})")

    extras = []
    if n is not None:
        extras.append(f"n={n}")
    if alternative != "two-sided":
        extras.append(f"one-sided {alternative}")
    else:
        extras.append("two-sided")

    if extras:
        parts.append(f"({', '.join(extras)})")

    return " ".join(parts)


def _stat_letter(test_name):
    """Map test name to conventional statistic letter."""
    name = test_name.lower()
    if "wilcoxon" in name:
        return "W"
    elif "mann-whitney" in name or "mann whitney" in name:
        return "U"
    elif "t-test" in name or "t test" in name:
        return "t"
    elif "chi" in name:
        return "X2"
    elif "fisher" in name:
        return "OR"
    elif "kruskal" in name:
        return "H"
    elif "anova" in name or "f-test" in name:
        return "F"
    return "stat"


# =============================================================================
# Test justification
# =============================================================================

# Standard justification texts for common tests
_JUSTIFICATIONS = {
    "wilcoxon": (
        "Wilcoxon signed-rank: non-parametric paired test. Used because "
        "N<30 and normality cannot be assumed for percentage data. "
        "Appropriate for repeated measures on the same subjects."
    ),
    "mann-whitney": (
        "Mann-Whitney U: non-parametric test for independent groups. "
        "Used because normality cannot be assumed and/or sample sizes are small."
    ),
    "paired-t": (
        "Paired t-test: parametric test for paired observations. "
        "Assumes normality of differences. Used when N is sufficient "
        "and data are approximately normal."
    ),
    "chi-squared": (
        "Chi-squared test: tests association between categorical variables. "
        "Used when expected cell counts are >= 5."
    ),
    "fisher": (
        "Fisher's exact test: exact test for 2x2 contingency tables. "
        "Used when sample sizes are small or expected cell counts < 5."
    ),
    "kruskal-wallis": (
        "Kruskal-Wallis H: non-parametric one-way ANOVA. "
        "Used for comparing 3+ independent groups when normality is not assumed."
    ),
    "lmm": (
        "Linear mixed model: accounts for repeated measures by including "
        "subject as a random effect. Preferred for longitudinal data with "
        "unbalanced designs or missing observations."
    ),
}


def stat_justification(test_name):
    """Return a standard justification string for a statistical test.

    Parameters
    ----------
    test_name : str
        Test name or key (e.g., "wilcoxon", "Wilcoxon signed-rank",
        "chi-squared", "lmm").

    Returns
    -------
    str : Justification text, or generic message if test not recognized.
    """
    key = test_name.lower().replace(" ", "-").replace("_", "-")

    # Try exact match first
    if key in _JUSTIFICATIONS:
        return _JUSTIFICATIONS[key]

    # Try partial match
    for k, v in _JUSTIFICATIONS.items():
        if k in key or key in k:
            return v

    return (
        f"{test_name}: justification not pre-defined. "
        f"Document why this test was chosen, what assumptions it makes, "
        f"and whether those assumptions are met."
    )


# =============================================================================
# Baseline normalization
# =============================================================================

def normalize_to_baseline(df, baseline_phase, value_col,
                          min_baseline=5.0, subject_col="subject_id",
                          phase_col="timepoint"):
    """Normalize values to pre-injury baseline with floor.

    Subjects with baseline below min_baseline are excluded to prevent
    absurd ratios (e.g., 3% baseline -> 9% rehab = "300% recovery").

    Parameters
    ----------
    df : DataFrame
        Must contain subject_col, phase_col, and value_col.
    baseline_phase : str
        Name of the baseline phase (e.g., "Last 3", "Pre-Injury").
    value_col : str
        Column containing values to normalize.
    min_baseline : float
        Minimum baseline value for inclusion.
    subject_col : str
        Column identifying subjects.
    phase_col : str
        Column identifying phases/timepoints.

    Returns
    -------
    tuple of (DataFrame, dict)
        normalized_df : DataFrame with added '{value_col}_pct_baseline' column.
        report : dict with 'excluded_subjects', 'flagged_subjects' (>100% recovery),
                 'min_baseline_used', and counts.
    """
    # Get baseline values per subject
    baseline = df[df[phase_col] == baseline_phase].groupby(subject_col)[value_col].mean()

    # Exclude subjects below floor
    excluded = baseline[baseline < min_baseline].index.tolist()
    included = baseline[baseline >= min_baseline]

    # Normalize
    result = df[df[subject_col].isin(included.index)].copy()
    result[f"{value_col}_pct_baseline"] = result.apply(
        lambda row: (row[value_col] / included[row[subject_col]]) * 100
        if row[subject_col] in included.index else float("nan"),
        axis=1,
    )

    # Flag subjects exceeding 100% recovery (may indicate data issue)
    non_baseline = result[result[phase_col] != baseline_phase]
    flagged = []
    if len(non_baseline) > 0:
        max_recovery = non_baseline.groupby(subject_col)[f"{value_col}_pct_baseline"].max()
        flagged = max_recovery[max_recovery > 100].index.tolist()

    report = {
        "excluded_subjects": excluded,
        "excluded_count": len(excluded),
        "included_count": len(included),
        "min_baseline_used": min_baseline,
        "flagged_subjects": flagged,
        "flagged_count": len(flagged),
    }

    if excluded:
        print(
            f"  [!] Baseline normalization: {len(excluded)} subjects excluded "
            f"(baseline < {min_baseline}): {', '.join(str(s) for s in excluded)}",
            flush=True,
        )
    if flagged:
        print(
            f"  [!] {len(flagged)} subjects exceed 100% baseline recovery "
            f"(review for data issues): {', '.join(str(s) for s in flagged)}",
            flush=True,
        )

    return result, report
