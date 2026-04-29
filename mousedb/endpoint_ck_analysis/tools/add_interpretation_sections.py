"""Append a collapsible 'How to read this notebook's output' markdown cell
to each endpoint_ck_analysis notebook.

Per the lab convention, interpretation guides are reference content (not
first-read narrative), so they're wrapped in <details><summary> blocks
that collapse by default in JupyterLab, VS Code, and GitHub markdown.

Idempotent: skips notebooks that already have an interpretation cell
(detected by a marker comment in the markdown).
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path


MARKER = "<!-- INTERPRETATION_BLOCK -->"


def make_md_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "id": uuid.uuid4().hex[:8],
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def wrap(summary: str, body: str) -> str:
    return (
        f"{MARKER}\n"
        f"## How to read this notebook's output\n\n"
        f"<details>\n"
        f"<summary>{summary}</summary>\n\n"
        f"{body}\n"
        f"</details>\n"
    )


INTERPRETATIONS = {
    "00_setup.ipynb": (
        "What the doctor + data load tells you (click to expand)",
        """\
**Doctor table**: every required check should show `[OK]`. Optional checks
(like `jupyterlab`) may show `[INFO]` if you're using VS Code -- that's
fine. Any `[FAIL]` line means the analysis won't run; the detail column
gives the fix (usually a `pip install ...` command).

**Data load shapes** (printed after `load_all`): the notebook prints the
actual row/column counts of every dataframe and the contents of
`matched_subjects`. Verify each is nonzero and that `matched_subjects`
contains the expected number of mice for the current cohort.

- `AKDdf`: every contacted/uncontacted reach in the analyzable phases.
- `FCDGdf_wide`: matched subjects x eLife groups (both/left/right hemispheres).
- `AKDdf_agg_contact`: subject x phase x contact_group aggregated kinematics.
- `matched_subjects`: mouse IDs for which both kinematic and connectomic data exist.
  These are the only mice the analysis can use -- a mouse with kinematics but
  no traced brain (or vice versa) drops out here, since coupling questions
  require both blocks.

If any dataframe is empty or `matched_subjects` is 0, something upstream
is wrong: phase columns weren't backfilled, the wrong DB is configured,
or the imaging-parameter filter excluded everything. Re-check
`connectome.db` is the right file and that recent backfills were applied.
""",
    ),

    "01_connectivity_pca.ipynb": (
        "What the connectivity PCA outputs tell you (click to expand)",
        """\
**What this PCA is decomposing.** Every connectivity measurement here is
*residual* -- the cells that survived a uniform injury procedure applied
to mice with non-uniform innate connectivity. That residual is a joint
product of "what was there to begin with" and "what the injury removed,"
and at this stage of the project neither component is separable. The PCA
finds the axes along which mice differ most in their *residual*
connectivity profile.

**PC1 dominance interpretation.** PC1 captures the largest axis of
cohort-wide variation in residual connectivity. It is NOT lesion severity
(the injury procedure was uniform but produced non-uniform outcomes --
there's no single "more vs less injured" axis to recover). It is NOT
innate variation (these mice are all post-injury). It's the dominant
direction along which what's-still-connected differs across mice. **What
this means for the paper:** PC1 (and PC2, PC3 as needed) is the input to
the kinematics question -- given each mouse's position on this
residual-connectivity axis, what reaching kinematics does it produce?
Notebooks 04 and 07 take that question on.

**Why the control cohort matters (deferred).** Once uninjured mice are
added to the dataset, the difference between control and injured
connectivity profiles becomes the injury-attributable loss spectrum. At
that point this PCA can be re-run on (control - injured) and the axes
will reflect "what the injury removed" rather than "what's left." The
current decomposition is the right thing to do at this cohort size --
it just answers a slightly different question than the controlled
analysis will.

**Top PC1 loadings -- what they mean.** The regions with the largest PC1
loadings are where residual connectivity varies most across mice. If
those happen to be the high-priority regions in `SKILLED_REACHING` (CST,
Red Nucleus, Reticular Nuclei), residual variation is concentrated where
we'd expect reaching to be controlled -- predictive coupling to
kinematics has a fighting chance. If low-priority regions dominate, the
cohort's residual variation is concentrated elsewhere; PLS in 04 may
still find a coupled axis on PC2/PC3 but PC1 alone won't carry the story.

**Low PC1 loadings -- what they mean.** A region with a near-zero loading
on PC1 doesn't vary much across mice on that axis -- every mouse retained
roughly similar residual connectivity to that region. *Example:* if
"Vestibular Nuclei" has a tiny PC1 loading, mice all kept comparable
amounts of vestibular connectivity post-injury, so this region can't be
used to distinguish one mouse from another along PC1. It's not a defect
-- it just means that region's contribution to differential outcomes
lives elsewhere (a higher PC, or genuinely no role in this cohort's
variation).

**Heatmap red-line.** The cutoff visualizes the same question. Bright
cells LEFT of the line -> residual variation lives in the
predicted-important regions. Bright cells RIGHT -> residual variation
lives in regions the prior de-emphasizes; either the literature is
missing something or the variance is in tracts the reaching circuit
doesn't lean on.

At small cohort sizes PC loadings past the first few are noisy. Treat
the top-10 region list as suggestive; re-run as N grows.
""",
    ),

    "02_kinematic_pca.ipynb": (
        "What the per-phase kinematic PCA outputs tell you (click to expand)",
        """\
**What this PCA is decomposing.** At each experimental phase (Baseline,
Post_Injury_1, Post_Injury_2-4, Post_Rehab_Test) the kinematics tell us
what reaching motion the mice are actually producing. Each per-phase PCA
finds the axes along which mice differ most from each other in their
reaching style at that phase. Reading those axes across phases tells us
how the cohort's distribution of reaching styles shifts as injury
removes capacity and ABT restores some of it.

**Per-phase scree plots.** How variance partitions across PCs within
each phase.

- Similar scree shapes across the four phases = the cohort's
  reaching-style state space has roughly the same dimensionality at
  every phase. Mice differ from each other in similar numbers of ways
  before, during, and after intervention.
- Very different scree shapes = the dimensionality of reaching style
  changes with phase. *Example:* if Baseline is dominated by PC1 but
  Post_Injury_1 spreads variance across many PCs, the injury produced
  a wider variety of compensatory reaching patterns, with each mouse
  finding its own way through.

**Per-phase loading bars.** Which kinematic features drive each PC at
each phase.

**Sign-aligned cross-phase heatmap (the headline figure).** Rows are
features, columns are phases, color = loading on each PC. The signs are
aligned to a reference phase so comparisons make sense across columns.

- A row where colors are similar across all four phases = that feature's
  role in the cohort's variance is stable; it carries the same
  diagnostic weight at every timepoint.
- A row where colors flip or change magnitude across phases = the
  feature's role depends on when in the experiment you measure it.
  These are the features whose meaning is not phase-invariant -- a paper
  that reports them needs to specify the phase, since the direction of
  the effect can flip.

**Top vs low loadings -- what they mean.** A feature with a large PC1
loading at a given phase is one along which mice differ a lot at that
phase. *Example:* if `peak_velocity_mm_per_sec` loads strongly on PC1 at
Post_Injury_1 but weakly at Baseline, it means injured mice differ
greatly in how fast they reach (some retain near-baseline speed, others
are dramatically slowed) while baseline mice are all roughly comparable.
A near-zero loading means mice are similar on that feature at that
phase -- it can't distinguish one mouse from another in the dominant
variance axis.

**Important features list.** The union across PCs of features with the
highest mean absolute loading. These feed into the PLS Y-block in
notebook 04 -- they're the kinematic features the variance-mapping
flagged as carrying signal. Features that never make the list at any
phase contribute mostly noise from this analysis's perspective.

At small cohort sizes each per-phase PCA has the matched-subject count
as its sample size; PC1 is reasonably stable but higher PCs are noisy.
""",
    ),

    "03_kinematic_clustering.ipynb": (
        "What the feature dendrogram + 2D/3D scatter tell you (click to expand)",
        """\
**What this analysis is doing.** Kinematics is high-dimensional: there
are dozens of features per reach (positions, velocities, durations,
contact metrics). Many of those features are restating the same
underlying information in different units. This notebook clusters
features by how much they correlate with each other across reaches, so
the paper can report a small set of *independent* kinematic axes
instead of a thicket of redundant ones.

**Dendrogram (height = 1 - |correlation|).** How kinematic features
relate to each other across reaches.

- Pairs of features at distance ~0 = essentially the same measurement.
  *Example:* `max_extent_mm` and `max_extent_pixels` should sit at
  distance ~0 since they're the same thing in different units. This is
  the `prefer_calibrated_units` deduplication validating itself.
- Tight cluster of 3-4 features = those features carry overlapping
  information across reaches; you don't lose much by dropping all but
  one when you pick a representative for the paper.
- Features sitting alone at the right edge = they carry independent
  information not captured by anything else in the dataset. These are
  the high-value kinematic axes -- each one adds something the others
  don't.
- Big chunks of the dendrogram with similar height = the kinematic
  feature space has clear functional groupings. *Example:* velocity-
  related features clustering together separate from path-shape
  features tells the reader that "speed" and "trajectory geometry" are
  two distinct things this cohort varies on.

**Top vs low correlation -- what they mean for the paper.** Features
that tightly cluster suggest the paper can pick one representative per
cluster and still convey the same biological story. Features that sit
far from everything else are the ones to make sure the paper reports
explicitly -- dropping them loses information no other feature is
capturing.

**2D PC scatter colored by cluster + labeled centroids.** Feature
relationships projected into PC space. Tight clusters in 2D = features
that can be summarized by a single new composite axis. Spread clusters
= the clustering is forcing structure that the PCA-based geometry does
not see; treat those clusters skeptically.

**3D plotly view.** Same plot interactive; hover any dot to see the
full feature name. Use this when 2D labels collide.

The dendrogram is the most informative panel; the 2D/3D PC views are
sanity checks on the cluster structure.
""",
    ),

    "04_pls_variants.ipynb": (
        "What the three PLS results tell you (click to expand)",
        """\
**What PLS is doing.** PLS finds the axes of *coupling* between two
blocks: residual connectivity (X) and kinematics (Y). Each "latent
variable" (LV) is a paired direction -- one in connectivity space, one
in kinematic space -- such that subjects who score high on the
connectivity side tend to also score high on the kinematic side. This
is the core IV->DV question: does residual connectivity (X) predict
kinematic outcome (Y)?

**Each variant produces 3 panels per LV.** Connectivity loadings (X),
kinematic loadings (Y), subject cross-score scatter.

**Cross-score scatter is the headline.**

- Subjects on or near the diagonal with high r (>0.8) = X and Y share
  a coupled axis. The connectivity pattern this LV captures tracks the
  kinematic pattern this LV captures across mice. *Example:* a clean
  positive scatter on LV1 of the recovery_delta variant would mean
  "mice with more residual connectivity on the LV1 axis recovered more
  reaching capacity post-ABT" -- the headline scientific claim.
- Spread cloud, low r = no coherent coupling at this LV.
- At small cohort sizes, **every PLS will look highly correlated by
  construction.** PLSCanonical has enough freedom (few subjects x many
  features per side) that it can always find a fit. Treat the
  cross-score r as descriptive ("here's what the data shows"), NOT
  inferential ("there's a real relationship"). At higher N this
  becomes a real test.

**Variant interpretations.**

- *Injury snapshot* (X = residual connectivity, Y = post-injury
  kinematics). Which residual connectivity patterns covary with which
  post-injury reaching profile. Heads up the "given what residual
  connectivity each mouse had, here's the reaching style it produces
  immediately after injury" story.
- *Deficit delta* (X = residual connectivity, Y = baseline -> injury
  shift in kinematics). Which residual connectivity patterns track HOW
  FAR a mouse's reaching style shifted between baseline and post-
  injury. X-loadings here are the regions whose residual connectivity
  best predicts the magnitude/direction of the immediate deficit.
- *Recovery delta* (X = residual connectivity, Y = injury -> post-rehab
  shift in kinematics). Which residual connectivity patterns track HOW
  MUCH a mouse recovered after ABT. X-loadings here are the regions
  whose residual connectivity best predicts the post-rehab change.
  This is the headline "does residual connectivity explain
  differential ABT response" question.

**Top vs low loadings -- what they mean.** A region with a large
absolute loading on LV1 contributes strongly to the dominant coupled
axis. Sign tells direction: positive loading on X + positive on Y =
these covary; opposite signs = they covary inversely. *Example:* if
"Corticospinal_both" has a large positive LV1 loading on the X side
and "max_extent_mm" has a large positive LV1 loading on the Y side,
mice with more residual CST connectivity tend to reach further. If
"Corticospinal_both" loads near-zero on LV1, residual CST sparing
doesn't help define the coupling axis -- this LV is being driven by
other regions.

**Why we run all three variants.** The connectivity X-block is the same
each time; only the Y-block changes. Comparing the three tells you
where coupling is actually present: a region with high X-loading on
the recovery_delta variant but low loading on the injury_snapshot
variant matters specifically for ABT response, not for the immediate
post-injury state.

These figures persist to `example_output/04_pls_*.png` and appear in
the figure gallery (notebook 99) so the explorations are traceable
without re-running.
""",
    ),

    "05_lmm_phase_effects.ipynb": (
        "What the per-feature LMM bar chart tells you (click to expand)",
        """\
**What the LMM is asking.** For each kinematic feature, the LMM tests
whether the feature value differs across experimental phases (Baseline,
Post_Injury_1, Post_Injury_2-4, Post_Rehab_Test) once subject-level and
session-level variability are accounted for. A surviving feature is one
where the cohort, on average, reaches differently at different phases
-- it's an aspect of motor performance that injury and/or ABT shifted.
Features that don't survive are stable across the experiment regardless
of injury state.

**Three stacked bar charts.** Omnibus phase effect, deficit delta
(Baseline vs Post_Injury_2-4), recovery delta (Post_Injury_2-4 vs
Post_Rehab_Test). Each plots `-log10(FDR-adjusted p)` per feature.

- Bars to the right of the red dashed line (`-log10(0.05)`) = features
  significantly affected by phase after multiple-testing correction.
- A long blue (significant) tail = many kinematic features change
  across phases; reaching is broadly affected by injury and/or ABT.
- Few or no bars past the red line = no detectable phase effects after
  FDR; either the cohort is too small to find them or the effects are
  small relative to within-subject variability.

**Per-analysis interpretation.**

- *Omnibus*: "is anything different across the four phases at all?".
  This is the broadest test; surviving features here are robustly
  phase-dependent. *Example:* if `peak_velocity_mm_per_sec` survives
  the omnibus, the cohort's reaching speed is not stable across the
  experiment; injury and/or post-injury time and/or ABT changed it.
- *Deficit*: "which features dropped between Baseline and
  Post_Injury_2-4?". Surviving features = injury-affected aspects of
  reaching. These are the kinematic axes the C5 contusion shifted.
- *Recovery*: "which features came back between Post_Injury_2-4 and
  Post_Rehab_Test?". Surviving features = ABT-responsive aspects of
  reaching. These are the kinematic axes that improved with training.

**A feature surviving deficit AND recovery** is a strong candidate for
the paper's headline narrative -- injury reliably perturbed it AND ABT
reliably restored some of it. A feature surviving deficit but NOT
recovery dropped at injury and stayed dropped (no ABT response). A
feature surviving recovery but NOT deficit improved with ABT but didn't
clearly drop at injury (could be late-phase task-learning rather than
injury-specific recovery).

**Top vs low -log10(p) -- what they mean.** A feature with a tall bar
on a given test reliably changed across that contrast. A feature with
a near-zero bar didn't change measurably -- mice on average reach the
same way on that feature before and after. *Example:* if
`reach_duration_sec` has a tall bar on the deficit test but a near-zero
bar on the recovery test, mice slowed down post-injury and stayed
slow despite ABT. That's actually informative -- it points to a
deficit aspect ABT didn't restore, which is itself a publishable
finding.

**At small cohort sizes expect few features to clear FDR.** The LMM
uses reach-level data (hundreds of reaches per subject) which gives
within-subject precision, but the inferential N is still the matched-
subject count. The chi-square Wald approximation at small N means raw
p-values are slightly too small; FDR correction partially compensates.
""",
    ),

    "06_pellet_validation.ipynb": (
        "What the pellet-scoring agreement metrics mean (click to expand)",
        """\
**What this notebook is checking.** Outcome (missed / displaced /
retrieved) is one of the most consequential variables in the
experiment -- the entire analysis pipeline conditions kinematics on
contact/outcome. If algorithmic and manual scoring don't agree, the
outcome labels feeding everything downstream are noisy, and any
conclusion that depends on the missed/displaced/retrieved distinction
is unstable. This notebook quantifies how much we can trust the
algorithmic labels and where they break down.

**Cohen's kappa (three-way).** Chance-corrected agreement between
manual and algorithmic scoring across all three outcome categories.

- <0.4 = poor agreement; do not trust algorithmic scoring; manual
  scoring required for downstream analyses.
- 0.4 - 0.6 = moderate; algorithmic scoring usable for exploratory
  work but flag results that hinge on outcome categorization.
- 0.6 - 0.8 = substantial; algorithmic scoring fine for most analyses
  but spot-check edge cases.
- >0.8 = almost perfect; algorithmic scoring trustworthy for
  publication.

**Three-way exact-agreement rate.** Simpler metric, no chance
correction. Generally aligns with kappa direction but harder to
interpret across class imbalances.

**Binary (contacted vs missed) agreement rate.** Usually higher than
three-way because the displaced-vs-retrieved boundary is the harder
call. If binary is high but three-way is low, the algorithm is good at
detecting contact but bad at distinguishing displacement from
retrieval. **What this means for the paper:** any analysis that uses
contact_group (e.g., the contact-grouped kinematic aggregations in
notebooks 02-05) is on solid ground; analyses that hinge on the
retrieval rate specifically need manual review.

**Confusion matrix.** Rows manual, columns algorithm.

- Strong diagonal = high agreement.
- Off-diagonal mass concentrated in displaced<->retrieved cells = the
  algorithm confuses retrievals with displacements (or vice versa);
  diagonal-flanking errors.
- Off-diagonal mass in missed<->contacted cells = the algorithm misses
  contacts entirely; more concerning because contact detection is the
  precondition for any kinematic analysis to even be valid.

**Per-phase agreement bar chart.** Does algorithm reliability change
across the experiment?

- Steady high agreement across all phases = the algorithm is robust;
  use it freely throughout the analysis.
- Dropping agreement post-injury = the injured cohort's reach
  kinematics differ from what the algorithm was trained on; consider
  manual scoring for post-injury analyses or flag the phase-specific
  uncertainty in the discussion. *Example:* if Baseline kappa is 0.9
  but Post_Injury_1 kappa drops to 0.5, post-injury outcome labels are
  the weakest part of the dataset and any kinematic finding that
  depends on outcome categorization at that phase deserves a caveat.

The N annotations on each bar tell you the per-phase sample size --
a phase with very few reaches has noisy agreement and the kappa
estimate is itself unreliable.
""",
    ),

    "07_connectivity_trajectory_linkage.ipynb": (
        "What the trajectory + cluster outputs tell you (click to expand)",
        """\
**What this notebook is doing.** This is the closest thing in the
pipeline to the headline question: given each mouse's residual
connectivity, what reaching kinematics did it produce across baseline,
injury, and post-ABT? It clusters mice on residual connectivity, then
overlays kinematic trajectories on those clusters to ask whether
similar residuals produce similar reaching trajectories across phases.

**Cluster profile heatmap (z-score per cluster per region).** Which
connectivity regions define each cluster.

- Clusters with strongly red OR strongly blue cells in distinct
  regions = clusters genuinely differ in their residual-connectivity
  profile; the auto-generated names ("up-Corticospinal_both", etc.)
  reflect real defining features. *Example:* if cluster A is bright
  red on `Corticospinal_both` and cluster B is bright blue on the same
  region, mice in cluster A retained more residual CST connectivity
  than mice in cluster B -- this is the cluster the paper would
  describe as "CST-spared."
- Clusters with mostly pale colors = subjects in those clusters are
  near the population mean in most regions; the cluster is "central"
  and doesn't have strong defining features.
- A region with pale cells across all clusters = that region doesn't
  distinguish clusters from each other; mice retained similar amounts
  of it regardless of cluster assignment.

**Permutation validation panel (histogram + vertical lines).**

- Observed within-cluster variance (red) far below the null
  distribution mean (gray histogram) = clustering captures real
  structure; subjects within a cluster genuinely look more alike in
  residual connectivity than random groups of the same size.
- Observed near or above the null distribution = clusters are noise;
  the algorithm divided subjects but no within-cluster coherence
  beyond chance. **Don't take cluster names seriously when this is
  the case.**
- LOO ticks (blue) clustered tightly around observed = result is
  robust to dropping any single subject. LOO ticks spread widely =
  one or two subjects are driving the cluster structure; results are
  fragile and shouldn't be reported without expanded N.

**Alluvial / Sankey (kinematic-cluster flow across phases).** Do
subjects stay in the same kinematic cluster across phases?

- Mostly horizontal flows (each subject stays in its column-aligned
  cluster across phases) = kinematic phenotype is stable across the
  experiment; subjects reach in their own characteristic style
  through baseline, injury, and post-ABT.
- Crossing flows = subjects change which other subjects they cluster
  with at different phases; the kinematic neighborhood reshuffles with
  injury/recovery. *Example:* a subject that clustered with another at
  baseline might cluster with a different one post-injury if injury
  brought their reaching styles into closer alignment.
- At small cohort sizes the alluvial degenerates to one subject per
  cluster per phase; the visualization is a smoke test, not
  informative until N grows.

**Trajectory plots (continuous + by-cluster).** Each subject is a
point at each phase, connected across phases.

- Subjects with similar connectivity PC1 score (similar color in the
  continuous view) following similar trajectories across phases =
  residual connectivity predicts kinematic trajectory shape. This is
  the headline result the analysis is reaching for.
- Similar-color subjects diverging across phases at small N = no
  reliable predictive relationship yet; could be real lack of
  coupling, could be noise. Re-run as N grows.

**Interaction LMM table.** Tests whether trajectory shape differs by
cluster. At small cohort sizes with each cluster having ~1 subject the
interaction is vacuous (no within-cluster replication); at higher N
this becomes the primary inferential test for "do residual-
connectivity clusters follow different recovery trajectories under
ABT?". The framework is in place for when the cohort grows.
""",
    ),

    "08_hypothesis_informed_tests.ipynb": (
        "What the four hypothesis-informed tests tell you (click to expand)",
        """\
**What these tests are checking.** The pipeline makes two consequential
choices upstream: (1) it aggregates atomic regions into eLife groups,
and (2) it uses a literature-derived priority ordering
(`SKILLED_REACHING`) to decide which regions are most relevant for
skilled reaching. This notebook stress-tests both. Are we throwing
away information by grouping? Is the literature ordering actually
predictive in this cohort, or are we letting it overrule the data?

**Grouped vs ungrouped PCA scree comparison.** Does the eLife grouping
hide variance?

- Grouped and ungrouped curves track each other closely = eLife
  grouping captures the dominant variance structure; aggregating to
  group level loses little. **What this means for the paper:** the
  grouped analysis is defensible; we can keep the upstream pipeline
  as-is.
- Ungrouped PC1+PC2 explain notably MORE than the grouped equivalent
  = atomic-region variance is being averaged out by the grouping;
  drill-down (next panel) is justified to find which groups are
  hiding subregional differences.
- Ungrouped PC1+PC2 explain notably LESS = the grouping is creating
  structure that doesn't exist at the atomic level (rare but
  possible -- typically indicates that the grouping is summing
  weakly-correlated subregions in a way that amplifies their joint
  signal).

**Per-group drill-down stacked bar.** Each bar is one eLife group; its
height shows variance explained by the top 3 within-group PCs.

- Bars dominated by blue (PC1) = group is internally coherent; one
  axis captures all subregional variation; the eLife grouping is
  defensible for that group.
- Bars where PC1, PC2, PC3 are all substantial = group has subregional
  heterogeneity the grouping is hiding. *Example:* if "Reticular
  Nuclei" splits its variance evenly across PC1, PC2, PC3, the
  subregions of the reticular formation are doing different things
  across mice and lumping them is losing signal. These groups are
  candidates for "we should split this into subregions in future work"
  in the discussion.
- Dashed line at 0.9 = reference for "PC1+PC2+PC3 capture almost
  everything"; bars below the line have even more variance in
  higher-order PCs.

**Nested LMM comparison table.** Does adding prior-ranked connectivity
regions to the model improve the fit beyond phase alone?

- AIC and BIC: smaller = better. If `+top5_priors` has a smaller AIC
  than `baseline (phase only)`, adding the top-5 prior-ranked regions
  improves the model fit beyond what phase alone explains.
- `p_vs_prior`: the LRT p-value for the full model vs the previous
  model. Small (<0.05) = the added connectivity regions explain
  variance the simpler model couldn't, so residual connectivity at
  those regions is doing predictive work. Large = the simpler model
  was sufficient; the prior-ranked regions don't help past phase.
- At small cohort sizes with reach-level data the LMM has plenty of
  within-subject reaches but few inferential units; LRT is
  anti-conservative. Synthetic mode (`USE_SYNTHETIC=True`) is the
  right test bench while the cohort is small.

**Prior-weighted PCA comparison bar chart.** Two PCAs on the same
residual-connectivity matrix; the second weights regions by their
position in `SKILLED_REACHING`. Does the prior change what PC1 sees?

- Top regions on PC1 (unweighted) and (prior-weighted) overlap heavily
  = the dominant variance axis is already aligned with the prior; the
  prior didn't change what PC1 sees. **What this means for the
  paper:** the data and the literature agree on which regions matter;
  the analyses in 01-07 rest on solid ground.
- Lists differ substantially = the unweighted PCA was driven by
  regions the prior de-emphasizes. *Example:* if unweighted PC1 is
  dominated by `Hypothalamic_both` but prior-weighted PC1 is
  dominated by `Corticospinal_both`, the cohort's largest residual
  variation is in a region the literature doesn't link to reaching.
  Either the literature is missing something, the variance is
  artifactual, or this cohort happens to vary on tracts the reaching
  circuit doesn't lean on. Each interpretation has a different
  discussion implication.

**The four tests collectively answer:** should we trust the eLife
grouping and the literature-derived priors as inputs to the main
analyses, or does the data argue for a more data-driven feature
selection? At small cohort sizes the answer is rarely definitive --
these tests are about flagging which decisions need defending in the
paper, not about overturning them.
""",
    ),

    "99_figure_gallery.ipynb": (
        "How to read this gallery (click to expand)",
        """\
This notebook embeds every PNG saved by notebooks 01-08 into one
scrollable page. There is no analysis here -- it's an at-a-glance
summary for sharing the pipeline's outputs with someone without
re-running anything (collaborator handoffs, supplement generation,
review committee snapshots).

**For interpretation of each figure, refer to the originating
notebook's own "How to read this notebook's output" section.** The
expected output patterns and what divergence means -- including the
residual-connectivity framing for the connectomics figures -- are
documented in those notebooks.

**If a figure is missing** (you see a "Not found" line instead of an
image), re-run the notebook that produces it. The expected file map is
enumerated in the cell above; cross-reference filename to notebook.
The 04 PLS figures are intentionally included so the exploratory PLS
runs are traceable in the supplement even if they don't make the main
paper.
""",
    ),
}


def append_interpretation(nb_path: Path) -> None:
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])
    summary, body = INTERPRETATIONS.get(nb_path.name, (None, None))
    if summary is None:                                                            # no interpretation defined for this notebook -> leave it alone
        print(f"  skip {nb_path.name} (no interpretation defined)")
        return
    cell_text = wrap(summary, body)                                                # build the markdown body once

    # Replace existing INTERPRETATION_BLOCK cell in-place if present; otherwise append.
    for i, cell in enumerate(cells):                                               # scan existing cells for the marker
        joined = "".join(cell.get("source", []))
        if MARKER in joined:                                                       # found a previous interpretation block
            cells[i] = make_md_cell(cell_text)                                     # overwrite with the latest content
            nb["cells"] = cells
            nb_path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
            print(f"  replaced interpretation in {nb_path.name}")
            return
    cells.append(make_md_cell(cell_text))                                          # no existing block -> append a fresh one
    nb["cells"] = cells
    nb_path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
    print(f"  appended interpretation to {nb_path.name}")


def main():
    if len(sys.argv) != 2:
        print("usage: add_interpretation_sections.py <notebooks_dir>")
        sys.exit(1)
    nb_dir = Path(sys.argv[1])
    for name in sorted(INTERPRETATIONS):
        path = nb_dir / name
        if not path.exists():
            print(f"  missing {path}")
            continue
        append_interpretation(path)


if __name__ == "__main__":
    main()