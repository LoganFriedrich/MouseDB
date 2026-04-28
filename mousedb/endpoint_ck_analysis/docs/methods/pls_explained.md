# PLS in this analysis

Partial Least Squares (PLS) relates two multivariate data blocks by
finding pairs of latent variables that maximize covariance across them.
Where PCA asks "what structure is in X alone?", PLS asks "what structure
in X corresponds to structure in Y?".

In this tool, `X` is a connectivity block (subjects x regions) and `Y` is
a kinematics block (subjects x features). Three Y-blocks are considered,
all sharing the same X:

| Variant | Y-block | Question |
|---------|---------|----------|
| `injury_snapshot` | kinematic profile at Post_Injury_2-4 | what does each connectivity profile predict about post-injury function? |
| `deficit_delta`   | kinematic change from Baseline to Post_Injury_2-4 | which connectivity differences track how far function drops after injury? |
| `recovery_delta`  | kinematic change from Post_Injury_2-4 to Post_Rehab_Test | which connectivity differences track rehab response? |

---

## The fit

We use `sklearn.cross_decomposition.PLSCanonical` with `n_components=2`
(capped at N-1 by the implementation). Each block is z-scored before
fitting so the latent variables aren't dominated by the largest-scale
variables.

---

## What each figure shows

For every latent variable (LV), `plot_pls` produces three panels:

1. **X-loadings bar chart**: which connectivity regions contribute most to
   this LV. Top N shown by absolute loading magnitude, then sorted by
   signed value for readability.
2. **Y-loadings bar chart**: which kinematic features contribute most.
3. **Cross-score scatter**: each subject as a point; x = X-score on this
   LV, y = Y-score on this LV. Close to the diagonal means this LV
   captures a direction that is coherent across both blocks. A dashed
   best-fit line and Pearson r / p label annotate the panel.

---

## Caveats at small N

- **PLS can find signal even when there is none**. At N=4 and dozens of
  features per side, the optimization has too much freedom. The
  cross-score scatter will look structured even for random data. Treat
  any single result as exploratory until replicated.
- **Cross-validation is uninformative**. The usual Q^2 / leave-one-out
  metric requires enough subjects that leaving one out is informative;
  at N=4 each fold is one subject and the variance estimates are wild.
- **Sparse PLS / stability selection**. At higher N the right follow-up
  is sPLS (L1-penalized PLS) with cross-validated sparsity and
  resampling-based feature selection. Out of scope for this release.

See [`../assumptions.md`](../assumptions.md) for the broader discussion of
what the variance-based methods do and don't tell us.
