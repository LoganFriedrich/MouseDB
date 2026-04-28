# PCA in this analysis

Principal Component Analysis finds the directions in a dataset along which
observations differ the most. Each direction is a linear combination of
the original variables, and the combinations are chosen to be orthogonal
so each captures a different piece of variance.

In this tool we use PCA in two places:

- **Notebook 01**: PCA on connectivity data (subjects x eLife-grouped
  regions). The output tells us which brain regions covary across subjects
  -- i.e. where sparing varies most between mice.
- **Notebook 02**: PCA on kinematics per phase (subjects x aggregated
  kinematic features). Fit separately for each phase; sign-aligned to
  Baseline so loadings can be compared across phases.

---

## What to read off a PCA result

- **Scree plot**: one bar per PC, height = variance explained. A steep
  drop after the first few bars means most structure is captured by those
  first PCs.
- **Loadings**: how each original variable contributes to a given PC. A
  variable with a large absolute loading is "driving" that component;
  small loadings mean that variable doesn't participate in that axis of
  variation.
- **Scores**: where each subject lands along each PC. Not plotted
  directly in this tool because N=4 makes the scatterplot uninformative.

---

## Caveats at small N

- **N is the ceiling on components**. A PCA can extract at most N-1
  components. With 4 matched subjects, you get at most 3 PCs, regardless
  of how many features you have.
- **Loadings are noisy**. Small changes to the data can flip which
  variables dominate a given PC. Treat "top 10 regions on PC1" as
  suggestive, not definitive, until N grows.
- **Sign ambiguity**. Per-phase PCA returns components with arbitrary
  sign. `align_signs_to_reference` in `helpers/dimreduce.py` flips each
  non-baseline phase's PCs if they're negatively correlated with baseline,
  so "positive PC1 loading" means the same thing across phases.

---

## Reading hint: variance is not importance

A region with high variance across subjects can end up with a large PC1
loading. That means "injuries differ most in this region's sparing" -- it
does **not** automatically mean the region matters for reaching. PLS
(notebook 04) is the tool that combines variance with behavioral outcome;
PCA alone is a variance map.

See [`../assumptions.md`](../assumptions.md) for the caveats around
interpreting variance as injury-driven.
