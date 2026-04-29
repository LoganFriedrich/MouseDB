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
from __future__ import annotations  # postpone-annotation evaluation; lets us reference types without runtime cost

import warnings  # warnings: standard library; we silence convergence chatter from statsmodels' optimizer
from typing import Any, Dict, Iterable, List, Optional  # type-hint primitives; document expected argument shapes

import numpy as np  # numpy: arrays + np.nan sentinel
import pandas as pd  # pandas: dataframe library
from statsmodels.formula.api import mixedlm  # mixedlm: statsmodels' linear mixed model with patsy-style formula syntax
from statsmodels.stats.multitest import multipletests  # multipletests: multiple-testing correction (Benjamini-Hochberg etc.)

from ..config import FDR_ALPHA  # default FDR alpha pulled from package config so all notebooks share one value


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
    subset = df[["subject_id", "session_date", "phase_group", feature]].dropna()  # column subset then dropna: keep only rows with all four columns present

    # Guard against pathological cases where the model can't possibly fit:
    #   - feature has zero or one unique value (no variance to model)
    #   - fewer than 2 subjects (can't estimate subject-level random variance)
    #   - fewer than 2 phase levels present (nothing to contrast)
    if (subset[feature].nunique() < 2                                              # feature is constant -> nothing to model
            or subset["subject_id"].nunique() < 2                                  # need >= 2 subjects for a subject-level random effect
            or subset["phase_group"].nunique() < 2):                               # need >= 2 phases for a contrast
        return {                                                                   # early-return placeholder result so the caller can still build a uniform table
            "feature": feature,
            "result": None,                                                        # no fitted model
            "phase_p": np.nan,                                                     # NaN signals "no test ran"
            "converged": False,                                                    # explicit not-converged marker
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": "insufficient_variance",                                      # tag the reason so debugging is fast
        }

    try:
        with warnings.catch_warnings():                                            # suppress convergence chatter - we check the converged flag below; this avoids spamming the notebook
            warnings.simplefilter("ignore")                                        # ignore all warnings inside the block
            # Q('{feature}') quotes the feature name so special chars (hyphens, parens) don't break patsy.
            # C(phase_group) forces phase to be treated as categorical with dummy coding; the first category
            #   of an ordered Categorical serves as the reference level so coefficients read as "phase X - reference".
            # groups='subject_id' is the outer random-intercept group; each mouse gets its own baseline offset.
            # vc_formula={'session': '0 + C(session_date)'} adds a nested variance component for session.
            #   The '0 +' means "no extra intercept offset, just a variance component per session_date level",
            #   which is statsmodels' slightly awkward syntax for what lme4 writes as (1|subject_id/session_date).
            model = mixedlm(                                                       # construct the mixed-effects model object (not yet fit)
                formula=f"Q('{feature}') ~ C(phase_group)",                        # f-string interpolates the feature name into the formula
                data=subset,
                groups="subject_id",                                               # outer random-intercept group
                vc_formula={"session": "0 + C(session_date)"},                     # nested variance component for session
            )
            # reml=True uses restricted maximum likelihood (less biased variance estimates for small samples).
            # method='lbfgs' is a robust optimizer for well-conditioned problems.
            result = model.fit(reml=True, method="lbfgs", disp=False)              # disp=False suppresses optimizer iteration printout

        # Omnibus Wald test for "does phase matter at all, pooling over all levels?"
        wald_table = result.wald_test_terms().table                                # statsmodels returns a small DataFrame of Wald tests per term; .table extracts it
        # Statsmodels >=0.14 renamed the p-value column from "P>chi2" to "pvalue".
        # Try the new name first; fall back to the legacy name for older installs.
        if "pvalue" in wald_table.columns:                                         # statsmodels 0.14+: column is 'pvalue'
            phase_p = wald_table.loc["C(phase_group)", "pvalue"]
        else:                                                                       # legacy (<0.14): column was 'P>chi2'
            phase_p = wald_table.loc["C(phase_group)", "P>chi2"]
        phase_p = float(phase_p)                                                   # cast to plain Python float; statsmodels 0.14+ returns 0-d numpy arrays which FDR correction can't handle uniformly

        return {                                                                   # success result dict; mirrors the failure shape so the caller can build a uniform table
            "feature": feature,
            "result": result,                                                      # fitted model object kept for post-hoc contrasts
            "phase_p": phase_p,
            "converged": result.converged,
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": None,
        }
    except Exception as e:                                                         # broad except: numerical failures, singular matrices, optimizer crashes, etc.
        # Truncate the error message so the results table stays readable.
        return {
            "feature": feature,
            "result": None,
            "phase_p": np.nan,
            "converged": False,
            "n_reaches": len(subset),
            "n_subjects": subset["subject_id"].nunique(),
            "error": str(e)[:100],                                                 # slice to first 100 chars; full repr would clog the dataframe column
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
    if fdr_alpha is None:                                                          # caller didn't override; use the package default
        fdr_alpha = FDR_ALPHA

    raw_results = [fit_phase_lmm(df, f) for f in features]                         # list comprehension: fit one LMM per feature

    # Pull out everything except the fitted-model column for the summary table.
    summary_rows = [{k: v for k, v in r.items() if k != "result"} for r in raw_results]  # drop the bulky result object so the dataframe is lightweight
    results_df = pd.DataFrame(summary_rows)                                        # convert list-of-dicts to DataFrame

    # FDR-BH correction on the converged features only.
    valid_mask = results_df["converged"] & results_df["phase_p"].notna()           # boolean: only correct on rows that actually ran (converged + non-NaN p)
    results_df["phase_p_adj"] = np.nan                                             # initialize all rows to NaN; rows with valid_mask=False will keep this
    if valid_mask.any():                                                           # at least one feature converged
        _, p_adj, _, _ = multipletests(results_df.loc[valid_mask, "phase_p"], method="fdr_bh")  # multipletests returns (reject, pvals_corrected, alphacSidak, alphacBonf); we only want pvals
        results_df.loc[valid_mask, "phase_p_adj"] = p_adj                          # write corrected p-values back to the converged rows

    results_df = results_df.sort_values("phase_p_adj").reset_index(drop=True)       # smallest adjusted p first; reset_index drops the original (now-shuffled) row numbers

    # Stash the FDR alpha on .attrs for downstream labeling. We deliberately do NOT
    # stash the fitted model objects on .attrs: pandas serializes .attrs as JSON
    # when writing parquet, and statsmodels' MixedLMResultsWrapper is not
    # JSON-serializable. If post-hoc contrasts are ever needed, hold onto the
    # raw_results list directly in the calling notebook instead.
    results_df.attrs["fdr_alpha"] = fdr_alpha                                       # FDR alpha is a plain float -> safe for parquet metadata
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
        with warnings.catch_warnings():                                            # suppress convergence chatter (we just want the fit object or None)
            warnings.simplefilter("ignore")
            model = mixedlm(formula, data=df, groups=groups, vc_formula=vc_formula)  # construct mixedlm with caller-provided formula and grouping
            return model.fit(reml=False, method="lbfgs", disp=False)               # reml=False (i.e. ML) so log-likelihoods are comparable across nested models for LRT
    except Exception:                                                              # any failure -> None; caller treats this as "skip this model spec"
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
    if vc_formula is None:                                                         # default to no nested variance components if caller didn't pass any
        vc_formula = {}
    from scipy.stats import chi2 as _chi2  # local import to keep top-of-file tidy  # chi2 distribution used for the likelihood-ratio p-value

    results = []                                                                   # accumulator for one row per model spec
    prev_result = None                                                             # tracks the previous fit so we can compute LRT vs prior
    for name, extra_rhs in model_specs:                                            # iterate the nested-model sequence; tuple-unpack each spec
        rhs = "C(phase_group)"                                                     # baseline RHS: phase as categorical
        if extra_rhs:                                                              # extra terms supplied -> append with patsy '+'
            rhs = f"{rhs} + {extra_rhs}"                                           # f-string concat
        formula = f"Q('{target_feature}') ~ {rhs}"                                 # full patsy formula; Q() quotes the LHS in case it has special characters
        fit = _fit_one_nested_lmm(df, formula, groups, vc_formula)                 # delegate to the single-fit helper; returns None on failure
        if fit is None:                                                            # model didn't fit -> record placeholders and continue
            results.append({
                "name": name, "formula": formula,
                "n_params": np.nan, "loglik": np.nan,
                "aic": np.nan, "bic": np.nan,
                "chi2_vs_prior": np.nan, "p_vs_prior": np.nan,
                "converged": False,
            })
            prev_result = None                                                     # break the LRT chain since this fit failed
            continue                                                               # skip to next model spec

        chi2 = p_lrt = np.nan                                                      # default LRT outputs to NaN (used for the first model where there's no prior)
        if prev_result is not None:                                                # only compute LRT if there was a successful prior model
            try:
                ll_full = fit.llf                                                  # log-likelihood of current (more-parameters) model
                ll_reduced = prev_result.llf                                       # log-likelihood of previous (fewer-parameters) model
                df_diff = len(fit.params) - len(prev_result.params)                # parameter difference -> degrees of freedom for the LRT
                if df_diff > 0:                                                    # only meaningful if current model has strictly more parameters
                    chi2 = 2 * (ll_full - ll_reduced)                              # 2(LL_full - LL_reduced) is asymptotically chi-square distributed under H0
                    p_lrt = 1 - _chi2.cdf(chi2, df_diff)                           # right-tail p-value: probability of seeing chi2 this large or larger by chance
            except Exception:                                                      # numerical edge cases -> keep NaN
                pass

        results.append({                                                           # success row: every metric we want to report
            "name": name,
            "formula": formula,
            "n_params": len(fit.params),                                           # parameter count (fixed + random) - drives AIC/BIC penalty
            "loglik": float(fit.llf),                                              # log-likelihood
            "aic": float(fit.aic),                                                 # Akaike Information Criterion
            "bic": float(fit.bic),                                                 # Bayesian Information Criterion (heavier penalty for parameters)
            "chi2_vs_prior": float(chi2) if not np.isnan(chi2) else np.nan,        # LRT statistic vs the previous spec
            "p_vs_prior": float(p_lrt) if not np.isnan(p_lrt) else np.nan,         # LRT p-value
            "converged": bool(getattr(fit, "converged", False)),                   # getattr default-False in case the attribute is missing on some statsmodels versions
        })
        prev_result = fit                                                          # advance the chain: this fit becomes the next iteration's "prior"

    return pd.DataFrame(results)                                                   # convert list-of-dicts into the comparison table
