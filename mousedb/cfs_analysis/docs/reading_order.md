# Reading order

For someone who received this folder and wants to understand the analysis
without necessarily running it. Each file is short; the whole tour takes
about 30 minutes.

---

## Part 1: What this is (5 min)

1. [`../README.md`](../README.md) -- overview, who it's for, what it produces.
2. [`../QUICKSTART.md`](../QUICKSTART.md) -- how to run it if you want to.
3. [`assumptions.md`](assumptions.md) -- caveats and statistical limitations.

---

## Part 2: The analytical primers (15 min)

Read these in order. Each is self-contained but they build on each other.

4. [`methods/pca_explained.md`](methods/pca_explained.md) -- what PCA is,
   what loadings mean, how to read a scree plot. Prerequisite for 01 and 02.
5. [`methods/region_priors.md`](methods/region_priors.md) -- why brain
   regions are shown in a specific order in every plot.
6. [`methods/pls_explained.md`](methods/pls_explained.md) -- partial least
   squares as applied to connectivity-vs-kinematics. Covers notebook 04.
7. [`methods/lmm_explained.md`](methods/lmm_explained.md) -- linear mixed
   models, nested random effects, FDR correction. Covers notebook 05.

---

## Part 3: The code (10 min)

Skim top-to-bottom; every module has a docstring at the top explaining
what it is.

8. [`../cfs_analysis/config.py`](../cfs_analysis/config.py) -- every
   constant that governs the analysis lives here.
9. [`../cfs_analysis/data_loader.py`](../cfs_analysis/data_loader.py) --
   how the six base dataframes come out of the database.
10. [`../cfs_analysis/helpers/kinematics.py`](../cfs_analysis/helpers/kinematics.py)
    -- feature selection, aggregation, proportion computations.
11. [`../cfs_analysis/helpers/dimreduce.py`](../cfs_analysis/helpers/dimreduce.py)
    -- PCA per phase, PLS across blocks.
12. [`../cfs_analysis/helpers/models.py`](../cfs_analysis/helpers/models.py)
    -- LMM specification.
13. [`../cfs_analysis/helpers/clusters.py`](../cfs_analysis/helpers/clusters.py)
    -- clustering methods, cluster profiling / naming, permutation validity,
    alluvial record builder.
14. [`../cfs_analysis/helpers/synthetic.py`](../cfs_analysis/helpers/synthetic.py)
    -- generator for the cloned-and-perturbed synthetic cohort used to
    exercise the full pipeline at realistic N.
15. [`../cfs_analysis/helpers/hierarchical.py`](../cfs_analysis/helpers/hierarchical.py)
    -- grouped-vs-ungrouped connectivity PCA, per-eLife-group drill-down
    using the ``constituent_regions`` JSON mapping.

---

## Part 4: The notebooks (if running)

See [`../QUICKSTART.md`](../QUICKSTART.md) step 5.
