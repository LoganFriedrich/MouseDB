# Assumptions and caveats

Read this before drawing scientific conclusions from the output figures.
Every analysis in this tool rests on assumptions that are defensible at
the current sample size but not proven. A writeup should mention each of
these.

---

## Sample size at time of release

N = 4 subjects with BOTH kinematics and connectomics data:
CNT_01_02, CNT_02_08, CNT_03_07, CNT_03_08.

Every statistical conclusion is pre-registered as exploratory at this
sample size. Effect sizes reported, confidence intervals printed, but
"this region matters for reaching" style claims require replication at
larger N.

---

## Variance as a proxy for injury-driven sparing

PCA on the connectivity matrix identifies axes of maximum variance across
subjects. We treat those as "where sparing varies most across injuries,"
which is the phrasing used throughout. This is a reasonable but **not
fully established** assumption. Other sources of connectivity variance
include:

- Baseline biological variability (uninjured mice differ in projection
  neuron counts).
- Tracing efficiency variability between brains (labeling yield, staining
  quality, registration errors).
- Counting noise / segmentation errors.
- Deliberate group differences if cohorts got different injury parameters.

We currently have no uninjured controls in the matched-subject set, so we
cannot subtract a baseline-variance floor. Dose-response across cohorts is
not yet tested. Writeup implication: caveat every "injury caused this
variance" claim as "assuming injury sparing is the dominant source of
variance, which we support by [future: controls / dose response]".

---

## PCA results at N=4

- **Loadings are noisy**. Removing or adding a single subject can
  substantially re-order which regions dominate a PC. Treat "top 10
  regions on PC1" as suggestive, not definitive.
- **PC sign is arbitrary**. We align non-baseline phases to Baseline via
  `align_signs_to_reference` so "positive PC1 loading" means the same
  thing across phases. That does not make the signs meaningful in
  absolute terms.
- **Variance !== importance**. A region with high variance across
  subjects can land on PC1 regardless of whether it's functionally
  important for reaching. PLS (notebook 04) combines variance with
  kinematic outcome; PCA alone is a variance map.

---

## PLS at N=4

- **Canonical correlation is always high at N=4**. PLSCanonical on small
  N finds latent variables that make the cross-score scatter look
  structured, regardless of whether there is real signal. The r and p
  labels on those panels are descriptive, not inferential.
- **Cross-validation is unreliable**. Standard Q^2 / leave-one-out at N=4
  means each fold is a single subject and the estimates are wild.
- **Sparse PLS not run**. At higher N the right tool is sparse PLS (L1
  penalty, cross-validated sparsity) with stability selection. Deferred
  until N is sufficient.

See [`methods/pls_explained.md`](methods/pls_explained.md).

---

## Double-dipping concerns

Notebooks 01 and 02 identify "important regions" and "important features"
by their own loadings, then notebook 04 uses those as the X and Y columns
for PLS. This is a form of selection-then-test that biases the PLS
result toward finding structure -- by construction, the chosen regions
vary most across subjects in a way that couples to chosen features.

Mitigation at current scale: frame the notebook 04 results as descriptive
(what structure the data shows given the selection), not inferential
(there is a true relationship between connectivity and kinematics). At
higher N the defensible path is data-splitting: identify important
regions on a discovery set, then test on a held-out set.

See [`methods/pls_explained.md`](methods/pls_explained.md) and project
notes on "two-stage pipeline hazards" in the team discussion history.

---

## Linear mixed model assumptions

- **Wald chi-square df**. statsmodels uses a chi-square approximation for
  the omnibus phase test rather than Satterthwaite or Kenward-Roger
  df-correction. At N=4 subjects this is mildly anti-conservative
  (p-values slightly too small).
- **Singular random-effect covariances**. Some features produce
  singular subject-level variance estimates. Those features are flagged
  as `converged=False` and kept out of the FDR pool, but the fixed
  effects are still interpretable for features that do converge.
- **No random slopes**. Only random intercepts for subject and nested
  session. Adding a random slope (e.g. phase effect varies by subject)
  requires more subjects than we have. A future version at higher N
  should test this.

See [`methods/lmm_explained.md`](methods/lmm_explained.md).

---

## Region prior is a hypothesis, not a measurement

The `SKILLED_REACHING` ordering in `mousedb.region_priors` (and the
frozen fallback in `config.py`) reflects what the field currently
*predicts* matters for skilled reaching. It is not a measurement. Plots
are ordered by this prior so that the eye compares within-group, but
treating "region N in the ordering" as "Nth-most-important region" would
be circular.

At higher N, data-driven feature selection (sparse PLS, stability
selection, elastic net) is the appropriate way to refine the ordering.
The current version is for readability and pre-registration of
hypothesis structure only.

See [`methods/region_priors.md`](methods/region_priors.md).

---

## Pellet-scoring validation scope

Notebook 06 compares manual vs algorithmic pellet outcomes on pillar
trays only. The algorithm is designed for pillar trays; E and F trays
have different geometry and the algorithm was never intended to handle
them. Writeup should state that agreement rates apply to the pillar
subset and that E/F tray outcomes require manual scoring.

---

## Synthetic-cohort mode (notebook 07)

Notebook 07 supports a ``USE_SYNTHETIC = True`` mode that clones the real
N=4 subjects into a larger synthetic cohort (default N=30) with
perturbation, so the clustering / permutation / interaction-LMM machinery
can be exercised at realistic N while real data is small. Caveats:

- **Synthetic mice are not real mice.** Every synthetic subject is a
  noisy clone of one of the 4 real subjects. Prototype lineage is
  encoded in the subject ID (``SYN_###_from_<real_id>``) so the origin
  is always recoverable.
- **Ground-truth clustering is known.** By construction, clones of the
  same prototype should cluster together. This is the test of whether
  the pipeline works: if ward / kmeans / gmm / consensus can recover
  the prototype assignment at high agreement, the tools are correctly
  wired. (During development all four methods recovered at 100%
  agreement with seed=42 and default noise scales.)
- **No biological novelty.** Synthetic runs cannot reveal new
  connectivity-kinematic relationships; they can only surface ones
  already present in the 4 real subjects. They validate plumbing, not
  science.
- **Results from synthetic runs should never be reported as real** --
  every figure generated in this mode should be labeled synthetic in
  any writeup, or regenerated on real data before publication.
- **The noise scales are tunable**. ``SYNTHETIC_CONN_NOISE`` and
  ``SYNTHETIC_KINE_NOISE`` in notebook 07's parameters cell control
  how much clone-vs-prototype spread there is. Smaller noise => tighter
  recovery; larger noise => harder test of the pipeline.

See [`../cfs_analysis/helpers/synthetic.py`](../cfs_analysis/helpers/synthetic.py)
for the generator and
[`methods/lmm_explained.md`](methods/lmm_explained.md) for why this
matters at larger N.

---

## What this tool does NOT attempt

- **Uninjured controls**. Not included in the matched set. All inference
  is within injured mice.
- **Dose-response to contusion force**. Not modeled.
- **Sex differences**. Not modeled.
- **Longitudinal trajectories via growth-curve models**. Only
  cross-phase snapshots and deltas. Full growth-curve modeling (random
  slopes for phase or time) is a future enhancement.
- **Causal claims**. Every result is associational. "Connectivity
  pattern X corresponds to kinematic pattern Y" should never become
  "connectivity pattern X causes kinematic pattern Y" in writeup without
  intervention data.
