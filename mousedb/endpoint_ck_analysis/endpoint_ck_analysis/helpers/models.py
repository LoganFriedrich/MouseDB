"""Linear mixed models for phase effects on kinematic features.

Motivation: a simple repeated-measures ANOVA treats each subject's per-phase
mean as one observation, discarding the hundreds of reaches per session and
the multiple sessions per subject per phase. An LMM with nested random
intercepts for session-within-subject uses the raw reach-level data and
partitions variance across three nested levels:

    reach (residual) -> session -> subject -> phase (fixed effect)

The nested session random intercept accounts for the fact that reaches
within one session are more similar to each other than reaches across
sessions (same motivation, rig calibration, pellet batch) -- ignoring that
would inflate effective N.

Small-sample caveat: statsmodels uses a chi-square Wald approximation for
the omnibus test, not Satterthwaite or Kenward-Roger. At small N this is mildly
anti-conservative. For a rigorous small-sample test, pymer4 (R's lme4
wrapper) would give Kenward-Roger df -- worth noting in the paper.

Functions:
    fit_phase_lmm            - fit one LMM for one feature, return a result dict
    run_phase_lmm_for_features - loop wrapper with FDR correction across features
"""
from __future__ import annotations

import warnings
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from statsmodels.formula.api import mixedlm
from statsmodels.stats.multitest import multipletests

from ..config import FDR_ALPHA


def fit_phase_lmm(df: pd.DataFrame, feature: str) -> Dict[str, Any]:
    """Fit one LMM for one kinematic feature.

    Model:
        feature ~ C(phase_group)
        random intercept: subject_id
        nested random intercept: session_date within subject_id

    Expects ``df`` to have columns 'subject_id', 'session_date', 'phase_group',
    and the target ``feature``. ``phase_group`` should be an ordered Categorical
    with the desired reference level as the first category.

    Returns a dict:
        feature     - the feature name
        result      - fitted MixedLMResults (or None if fitting failed)
        phase_p     - omnibus Wald chi-square p-value for the phase term
        converged   - True if the optimizer reported convergence
        n_reaches   - rows used in the fit
        n_subjects  - unique subjects in the fit
        error       - None on success, else a truncated error message
    """
    # Subset to only the columns we need and drop rows where this feature is NaN.
    # Doing the dropna per-feature lets features with missing values still
    # contribute what data they have, rather than having one bad feature ruin
    # everyone's N.
    subset = df[["subject_id", "session_date", "phase_group", feature]].dropna()

    # Guard against pathological cases where the model can't possibly fit:
    #   - feature has zero or one unique value (no variance to model)
    #   - fewer than 2 subjects (can't estimate subject-level random variance)
    #   - fewer than 2 phase levels present (nothing to contrast)
    if (subset[feature].nunique() < 2
            or subset["subject_id"].nunique() < 2
            or subset["phase_group"].nunique() < 2):
        return {
            "feature": feature,
            "result": None,
            "phase_p": np.nan,
            "converged": False,
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": "insufficient_variance",
        }

    try:
        with warnings.catch_warnings():  # Suppress convergence chatter - we check converged flag below
            warnings.simplefilter("ignore")
            # Q('{feature}') quotes the feature name so special chars (hyphens, parens) don't break patsy.
            # C(phase_group) forces phase to be treated as categorical with dummy coding; the first category
            #   of an ordered Categorical serves as the reference level so coefficients read as "phase X - reference".
            # groups='subject_id' is the outer random-intercept group; each mouse gets its own baseline offset.
            # vc_formula={'session': '0 + C(session_date)'} adds a nested variance component for session.
            #   The '0 +' means "no extra intercept offset, just a variance component per session_date level",
            #   which is statsmodels' slightly awkward syntax for what lme4 writes as (1|subject_id/session_date).
            model = mixedlm(
                formula=f"Q('{feature}') ~ C(phase_group)",
                data=subset,
                groups="subject_id",
                vc_formula={"session": "0 + C(session_date)"},
            )
            # reml=True uses restricted maximum likelihood (less biased variance estimates for small samples).
            # method='lbfgs' is a robust optimizer for well-conditioned problems.
            result = model.fit(reml=True, method="lbfgs", disp=False)

        # Omnibus Wald test for "does phase matter at all, pooling over all levels?"
        wald_table = result.wald_test_terms().table
        phase_p = wald_table.loc["C(phase_group)", "P>chi2"]

        return {
            "feature": feature,
            "result": result,
            "phase_p": phase_p,
            "converged": result.converged,
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": None,
        }
    except Exception as e:
        # Catch-all for numerical failures (singular matrix, optimization failures, etc).
        # Truncate the error message so the results table stays readable.
        return {
            "feature": feature,
            "result": None,
            "phase_p": np.nan,
            "converged": False,
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": str(e)[:100],
        }


def run_phase_lmm_for_features(
    df: pd.DataFrame,
    features: Iterable[str],
    fdr_alpha: Optional[float] = None,
) -> pd.DataFrame:
    """Run ``fit_phase_lmm`` once per feature, return a FDR-corrected results table.

    Args:
        df: Reach-level DataFrame with subject_id, session_date, phase_group, and every feature.
        features: Iterable of feature column names to test.
        fdr_alpha: FDR-BH alpha for multiple-testing correction. Defaults to
            ``config.FDR_ALPHA``.

    Returns:
        A DataFrame with one row per feature, columns: feature, phase_p,
        phase_p_adj, converged, n_reaches, n_subjects, error. Rows are sorted
        by phase_p_adj ascending so the strongest effects float to the top.

    The returned DataFrame drops the fitted model objects (which are kept in
    the ``feature_results`` attribute on the DataFrame via ``.attrs`` for
    post-hoc contrasts).
    """
    if fdr_alpha is None:
        fdr_alpha = FDR_ALPHA

    raw_results = [fit_phase_lmm(df, f) for f in features]

    # Pull out everything except the fitted-model column for the summary table.
    summary_rows = [{k: v for k, v in r.items() if k != "result"} for r in raw_results]
    results_df = pd.DataFrame(summary_rows)

    # FDR-BH correction on the converged features only.
    valid_mask = results_df["converged"] & results_df["phase_p"].notna()
    results_df["phase_p_adj"] = np.nan
    if valid_mask.any():
        _, p_adj, _, _ = multipletests(results_df.loc[valid_mask, "phase_p"], method="fdr_bh")
        results_df.loc[valid_mask, "phase_p_adj"] = p_adj

    results_df = results_df.sort_values("phase_p_adj").reset_index(drop=True)

    # Stash fitted models in .attrs (survives groupby/merge on recent pandas) so post-hocs can find them.
    results_df.attrs["feature_results"] = {r["feature"]: r for r in raw_results}
    results_df.attrs["fdr_alpha"] = fdr_alpha
    return results_df


# ---------------------------------------------------------------------------
# Nested LMM comparison (model selection)
# ---------------------------------------------------------------------------


def _fit_one_nested_lmm(
    df: pd.DataFrame,
    formula: str,
    groups: str,
    vc_formula: Optional[Dict[str, str]] = None,
) -> Optional[object]:
    """Helper: fit a single LMM; return MixedLMResults or None on failure."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = mixedlm(formula, data=df, groups=groups, vc_formula=vc_formula)
            return model.fit(reml=False, method="lbfgs", disp=False)
    except Exception:
        return None


def compare_nested_lmms(
    df: pd.DataFrame,
    target_feature: str,
    model_specs: list,
    groups: str = "subject_id",
    vc_formula: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """Fit a sequence of nested LMMs and return an AIC/BIC/LRT comparison table.

    Args:
        df: reach-level dataframe.
        target_feature: the dependent variable column.
        model_specs: list of ``(name, extra_rhs)`` tuples. Each model fits
            ``Q('<target_feature>') ~ C(phase_group) + <extra_rhs>``. Use an
            empty string for the baseline (phase-only) model.

            Example::

                [('baseline', ''),
                 ('+top_priors', 'Q("Corticospinal_both") + Q("Red Nucleus_both")'),
                 ('+all_priors', '...')]
        groups: outer random-intercept group column.
        vc_formula: variance components dict for nested random effects.

    Returns:
        DataFrame with one row per model; columns include aic, bic, loglik,
        ``p_vs_prior`` (LRT against the previous model in ``model_specs``),
        and ``converged``. Smaller AIC/BIC == better-fitting model.
        A small ``p_vs_prior`` means the added terms improved fit beyond
        what the preceding model already captured.
    """
    if vc_formula is None:
        vc_formula = {}
    from scipy.stats import chi2 as _chi2  # local import to keep top-of-file tidy

    results = []
    prev_result = None
    for name, extra_rhs in model_specs:
        rhs = "C(phase_group)"
        if extra_rhs:
            rhs = f"{rhs} + {extra_rhs}"
        formula = f"Q('{target_feature}') ~ {rhs}"
        fit = _fit_one_nested_lmm(df, formula, groups, vc_formula)
        if fit is None:
            results.append({
                "name": name, "formula": formula,
                "n_params": np.nan, "loglik": np.nan,
                "aic": np.nan, "bic": np.nan,
                "chi2_vs_prior": np.nan, "p_vs_prior": np.nan,
                "converged": False,
            })
            prev_result = None
            continue

        chi2 = p_lrt = np.nan
        if prev_result is not None:
            try:
                ll_full = fit.llf
                ll_reduced = prev_result.llf
                df_diff = len(fit.params) - len(prev_result.params)
                if df_diff > 0:
                    chi2 = 2 * (ll_full - ll_reduced)
                    p_lrt = 1 - _chi2.cdf(chi2, df_diff)
            except Exception:
                pass

        results.append({
            "name": name,
            "formula": formula,
            "n_params": len(fit.params),
            "loglik": float(fit.llf),
            "aic": float(fit.aic),
            "bic": float(fit.bic),
            "chi2_vs_prior": float(chi2) if not np.isnan(chi2) else np.nan,
            "p_vs_prior": float(p_lrt) if not np.isnan(p_lrt) else np.nan,
            "converged": bool(getattr(fit, "converged", False)),
        })
        prev_result = fit

    return pd.DataFrame(results)
