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

If any dataframe is empty or `matched_subjects` is 0, something upstream
is wrong: phase columns weren't backfilled, the wrong DB is configured,
or the imaging-parameter filter excluded everything. Re-check
`connectome.db` is the right file and that recent backfills were applied.
""",
    ),

    "01_connectivity_pca.ipynb": (
        "What the connectivity PCA outputs tell you (click to expand)",
        """\
**Scree plot**: how variance partitions across PCs.

- PC1 alone capturing >50% of variance = one dominant axis of injury
  variation; mice differ mostly in one coherent connectivity pattern.
- PC1 + PC2 together capturing >80% = most structure is 2D; the
  3D / higher PCs are noise.
- Variance spread evenly across PCs = no dominant pattern; subjects
  differ in many independent ways.

**Loading bars (per-PC)**: which connectivity regions drive each component.

- Top regions on PC1 are where injuries vary MOST across the matched
  cohort. They are NOT necessarily the regions that matter for reaching --
  they are just the ones where sparing differs most between mice. PLS
  (notebook 04) is what tells you which of those couple to kinematics.
- A region with a long bar in one direction (positive or negative)
  contributes strongly to that PC. The sign is arbitrary on its own;
  what matters is the magnitude and the relative ordering.

**Subject x region heatmap with red dashed line**: visual sanity check.
The red line is the predicted-importance cutoff from
`SKILLED_REACHING.high_priority_cutoff`. If subjects' bright cells cluster
to the LEFT of the line, the prior ordering aligns with what's varying
most in the data; if they're spread evenly or favor the RIGHT, the
literature-derived prior may be missing something the data sees.

At small N every PC loading is noisy. Treat the top-10 region list as
suggestive, not definitive. Re-run as N grows.
""",
    ),

    "02_kinematic_pca.ipynb": (
        "What the per-phase kinematic PCA outputs tell you (click to expand)",
        """\
**Per-phase scree plots** (one per phase): how variance partitions across
PCs within each phase.

- Similar shapes across the four phases = kinematic structure is
  preserved across injury and recovery; the same axes of variation
  exist before and after.
- Very different scree shapes = the kinematic state space changes with
  phase (e.g., more PCs needed post-injury to capture the same total
  variance, suggesting movement variability increases).

**Per-phase loading bars**: which kinematic features drive each PC at
each phase.

**Sign-aligned cross-phase heatmap** (the headline figure): rows are
features, columns are phases, color = loading on each PC.

- A row where colors are similar across all four phases = that feature
  carries the same role pre-injury, post-injury, and post-rehab.
- A row where colors flip or change magnitude across phases = the
  feature's role in the dominant axis of variation depends on phase.
  These are the features that "mean different things" at different
  experimental timepoints.

**Important features list**: the union across PCs of features with the
highest mean absolute loading. These feed into the PLS Y-block in
notebook 04 -- they're the kinematic features the variance-mapping
flagged as carrying signal.

At small N each per-phase PCA has the matched-subject count as its row count -- PC1 is reasonably stable but
higher PCs are noisy.
""",
    ),

    "03_kinematic_clustering.ipynb": (
        "What the feature dendrogram + 2D/3D scatter tell you (click to expand)",
        """\
**Dendrogram** (height = 1 - |correlation|): how kinematic features
relate to each other.

- Pairs of features at distance ~0 = essentially the same measurement
  (e.g., `max_extent_mm` and `max_extent_pixels` should sit at distance ~0
  -- this is the `prefer_calibrated_units` deduplication validating
  itself).
- Tight cluster of 3-4 features = those features carry overlapping
  information; you don't lose much by dropping all but one.
- Features sitting alone at the right edge = they carry independent
  information not captured by anything else in the dataset.
- Big chunks of the dendrogram with similar height = the kinematic
  feature space has clear functional groupings (e.g., velocity-related
  features clustering together separate from path-shape features).

**2D PC scatter colored by cluster + labeled centroids**: feature
relationships projected into PC space. Tight clusters in 2D = features
that can be summarized by a single new axis. Spread clusters = the
clustering is forcing structure that PCA does not see.

**3D plotly view**: same plot interactive; hover any dot to see the full
feature name. Use this when 2D labels collide.

The dendrogram is the most informative panel; the 2D/3D PC views are
sanity checks on the cluster structure.
""",
    ),

    "04_pls_variants.ipynb": (
        "What the three PLS results tell you (click to expand)",
        """\
**Each variant produces 3 panels per latent variable**: connectivity
loadings (X), kinematic loadings (Y), subject cross-score scatter.

**Cross-score scatter** is the headline.

- All subjects falling on or near the diagonal with high r (>0.8) =
  connectivity X-block and kinematic Y-block share a real coupled
  axis. PLS found a connectivity pattern that tracks a kinematic
  pattern across the cohort.
- Spread cloud, low r = no coherent axis was found.
- At small N, **every PLS will look highly correlated by construction**.
  PLSCanonical is given enough freedom (4 subjects x dozens of features
  per side) that it can always find a fit. Treat the cross-score r as
  descriptive ("here's what the data shows"), NOT inferential ("there's
  a real relationship"). At higher N this becomes a real test.

**Variant interpretations**:

- *Injury snapshot*: which connectivity regions covary with which
  kinematic features at the post-injury timepoint. Heads up the "this
  injury produces this kinematic deficit" story.
- *Deficit delta*: which connectivity regions track HOW FAR a subject
  fell from baseline. The X-loadings here are the regions whose sparing
  buffers vs amplifies the immediate deficit.
- *Recovery delta*: which connectivity regions track HOW MUCH a subject
  recovered with ABT. The X-loadings here are the regions whose
  sparing predicts rehab response.

**Loadings interpretation** (same for all three variants): high
absolute loading on LV1 means the region/feature contributes most to
the dominant coupled axis. Sign tells you the direction (positive
loading on X side + positive on Y side = these covary; opposite signs
= they covary inversely).
""",
    ),

    "05_lmm_phase_effects.ipynb": (
        "What the per-feature LMM bar chart tells you (click to expand)",
        """\
**Three stacked bar charts**: omnibus phase effect, deficit delta
(Baseline vs Post_Injury_2-4), recovery delta (Post_Injury_2-4 vs
Post_Rehab_Test). Each plots `-log10(FDR-adjusted p)` per feature.

- Bars to the right of the red dashed line (`-log10(0.05)`) = features
  significantly affected by phase after multiple-testing correction.
- A long blue (significant) tail = many kinematic features change
  across phases; reaching kinematics is broadly affected.
- Few or no bars past the red line = no detectable phase effects after
  FDR; either the cohort is too small to find them or the effects are
  small relative to within-subject variability.

**At small N expect few features to clear FDR.** The LMM uses
reach-level data (hundreds of reaches per subject) which gives within-
subject precision, but the inferential N is still the 4 subjects.
The chi-square Wald approximation at small N means raw
p-values are slightly too small; FDR correction partially compensates.

**Per-analysis interpretation**:

- *Omnibus*: "is anything different across the four phases at all?".
  This is the broadest test; surviving features here are robustly
  phase-dependent.
- *Deficit*: "which features dropped between baseline and Post_Injury_2-4?".
  Surviving features = injury-affected aspects of reaching.
- *Recovery*: "which features came back between Post_Injury_2-4 and
  Post_Rehab_Test?". Surviving features = rehab-responsive aspects.

A feature surviving all three FDR cutoffs is a strong candidate for the
paper's "this is what reaching looks like at each phase" narrative.
""",
    ),

    "06_pellet_validation.ipynb": (
        "What the pellet-scoring agreement metrics mean (click to expand)",
        """\
**Cohen's kappa (three-way)**: chance-corrected agreement between
manual and algorithmic scoring across the missed / displaced /
retrieved categories.

- <0.4 = poor agreement; do not trust algorithmic scoring; manual
  scoring required for downstream analyses.
- 0.4 - 0.6 = moderate; algorithmic scoring usable for exploratory
  work but flag results that hinge on outcome categorization.
- 0.6 - 0.8 = substantial; algorithmic scoring fine for most analyses
  but spot-check edge cases.
- >0.8 = almost perfect; algorithmic scoring trustworthy for
  publication.

**Three-way exact-agreement rate**: simpler metric, no chance
correction. Generally aligns with kappa direction but harder to
interpret across class imbalances.

**Binary (contacted vs missed) agreement rate**: usually higher than
three-way because the displaced-vs-retrieved boundary is the harder
call. If binary is high but three-way is low, the algorithm is good at
detecting contact but bad at distinguishing displacement from retrieval.

**Confusion matrix**: rows manual, columns algorithm.

- Strong diagonal = high agreement.
- Off-diagonal mass concentrated in displaced<->retrieved cells = the
  algorithm confuses retrievals with displacements (or vice versa);
  diagonal-flanking errors.
- Off-diagonal mass in missed<->contacted cells = the algorithm misses
  contacts entirely; more concerning.

**Per-phase agreement bar chart**: does algorithm reliability change
across the experiment?

- Steady ~0.9 across all phases = algorithm is robust; use it freely.
- Dropping agreement post-injury = injury changes reach kinematics in
  ways the algorithm wasn't trained for; consider manual scoring for
  post-injury analyses.

The N annotations on each bar tell you the per-phase sample size --
a phase with very few reaches has noisy agreement.
""",
    ),

    "07_connectivity_trajectory_linkage.ipynb": (
        "What the trajectory + cluster outputs tell you (click to expand)",
        """\
**Cluster profile heatmap (z-score per cluster per region)**: which
connectivity regions define each cluster.

- Clusters with strongly red OR strongly blue cells in distinct
  regions = clusters genuinely differ in their connectivity profile;
  the auto-generated names ("up-Corticospinal_both", etc.) reflect
  real defining features.
- Clusters with mostly pale colors = subjects in those clusters are
  near the population mean in most regions; clusters are "central"
  and don't have strong defining features.

**Permutation validation panel** (histogram + vertical lines):

- Observed within-cluster variance (red) far below the null
  distribution mean (gray histogram) = clustering captures real
  structure; subjects within a cluster genuinely look more alike than
  random groups of the same size.
- Observed near or above the null distribution = clusters are noise;
  the algorithm divided subjects but no within-cluster coherence
  beyond chance.
- LOO ticks (blue) clustered tightly around observed = result is
  robust to dropping any single subject. LOO ticks spread widely =
  one or two subjects are driving the cluster structure; results are
  fragile.

**Alluvial / Sankey** (kinematic-cluster flow across phases): do
subjects stay in the same kinematic cluster across phases?

- Mostly horizontal flows (each subject stays in its column-aligned
  cluster across phases) = kinematic phenotype is stable across the
  experiment; subjects reach in their own characteristic style
  through baseline, injury, and recovery.
- Crossing flows = subjects change which other subjects they cluster
  with at different phases; kinematic structure reshuffles with
  injury/recovery.
- At small N the alluvial degenerates to one subject per cluster per
  phase; the visualization is a smoke test, not informative until
  N grows.

**Trajectory plots** (continuous + by-cluster):

- Subjects with similar connectivity PC1 score (similar color in the
  continuous view) following similar trajectories across phases =
  connectivity predicts kinematic trajectory shape.
- Similar-color subjects diverging across phases = no predictive
  relationship at small N.

**Interaction LMM table**: tests whether trajectory shape differs by
cluster. At small N with each cluster having ~1 subject the interaction is
vacuous; at higher N this becomes the primary inferential test for
"do connectivity clusters follow different recovery trajectories?".
""",
    ),

    "08_hypothesis_informed_tests.ipynb": (
        "What the four hypothesis-informed tests tell you (click to expand)",
        """\
**Grouped vs ungrouped PCA scree comparison**: does the eLife grouping
hide variance?

- Grouped and ungrouped curves track each other closely = eLife
  grouping captures the dominant variance structure; aggregating to
  group level loses little.
- Ungrouped PC1+PC2 explain notably MORE than the grouped equivalent
  = atomic-region variance is being averaged out by the grouping;
  drill-down (next panel) is justified.
- Ungrouped PC1+PC2 explain notably LESS = the grouping is creating
  structure that doesn't exist at the atomic level (rare but
  possible).

**Per-group drill-down stacked bar**: each bar is one eLife group, its
height shows variance explained by the top 3 within-group PCs.

- Bars dominated by blue (PC1) = group is internally coherent; one
  axis captures all subregional variation; the eLife grouping is
  defensible for that group.
- Bars where PC1, PC2, PC3 are all substantial = group has subregional
  heterogeneity that the eLife grouping is hiding. These groups are
  candidates for "we should split this into subregions in future
  work."
- Dashed line at 0.9 = visual reference for "PC1+PC2+PC3 capture
  almost everything"; bars below the line indicate even more variance
  is in higher-order PCs.

**Nested LMM comparison table**:

- AIC and BIC: smaller = better. If `+top5_priors` has a smaller AIC
  than `baseline (phase only)`, adding the top-5 prior-ranked regions
  improves the model fit beyond what phase alone explains.
- `p_vs_prior`: the LRT p-value for the full model vs the previous
  model. Small (<0.05) = the added connectivity regions explain
  variance the prior model couldn't. Large = the simpler model was
  sufficient.
- At small N with reach-level data the LMM has plenty of within-subject
  data points but only 4 inferential units; LRT is anti-conservative.
  Synthetic mode (USE_SYNTHETIC=True) is the right test bench.

**Prior-weighted PCA comparison bar chart**:

- Top regions on PC1 (unweighted) and (prior-weighted) overlap heavily
  = the dominant variance axis is already aligned with the prior;
  the prior didn't change what PC1 sees.
- Lists differ substantially = the unweighted PCA was driven by
  regions the prior de-emphasizes. Worth investigating: are those
  regions truly unimportant for reaching (literature might be missing
  something) or noisy / artifactual?

The four tests collectively answer: should we trust the eLife grouping
and the literature-derived priors, or does the data argue for a more
data-driven feature selection?
""",
    ),

    "99_figure_gallery.ipynb": (
        "How to read this gallery (click to expand)",
        """\
This notebook just embeds every PNG saved by notebooks 01-08 into one
scrollable page. There is no analysis here; it's an at-a-glance
summary for showing someone the outputs without re-running anything.

**For interpretation of each figure, refer to the originating notebook's
own "How to read this notebook's output" section.** The expected output
patterns and what divergence means are documented there.

**If a figure is missing** (you see a "Not found" line instead of an
image), re-run the notebook that produces it. The expected file map
is enumerated in the cell above; cross-reference filename to notebook.
""",
    ),
}


def append_interpretation(nb_path: Path) -> None:
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])
    # Skip if marker already present
    for cell in cells:
        joined = "".join(cell.get("source", []))
        if MARKER in joined:
            print(f"  skip {nb_path.name} (already has interpretation block)")
            return
    summary, body = INTERPRETATIONS.get(nb_path.name, (None, None))
    if summary is None:
        print(f"  skip {nb_path.name} (no interpretation defined)")
        return
    cell_text = wrap(summary, body)
    cells.append(make_md_cell(cell_text))
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