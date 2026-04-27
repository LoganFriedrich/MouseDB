# Figure Review Lessons Learned

> Accumulated from reviewing lab_figures.py (15 figures), with user feedback.
> This document drives improvements to `mousedb.figures` standards and the recipe system.

---

## Review 1: lab_figures.py (15 figures)

### Verdict by Figure

| Fig | Title | Verdict | Reason |
|-----|-------|---------|--------|
| 01 | Project Overview | **Remove** | Architecture infographic. Obsidian exports cover this better. |
| 02 | Data Organization | **Remove** | Architecture diagram. Redundant with Obsidian/CLAUDE.md. |
| 03 | MouseBrain Pipeline | **Remove** | Architecture diagram. Documentation, not data. |
| 04 | Brain Region Counts | **Keep + Fix** | Real data. Wrong cell counts. Needs provenance. |
| 05 | eLife Comparison | **Skipped** | No eLife data available at generation time. |
| 06 | Hemisphere Laterality | **Keep + Fix** | Real data. Good chart type. Layout/outlier issues. |
| 07 | Slice Quantification | **Skipped** | No 2D batch data available. |
| 08 | MouseReach Pipeline | **Remove** | Architecture diagram. Obsidian territory. |
| 09 | Reach Outcomes | **Rebuild** | Real data but useless without phase/subject structure. |
| 10 | Kinematic Comparison | **Failed** | Missing seaborn. Needs review after fix. |
| 11 | Behavior by Phase | **Failed** | DB locked. Needs review after fix. |
| 12 | MouseCam System | **Remove** | Architecture diagram. |
| 13 | Database Schema | **Remove** | Schema diagram with broken row counts ("?"). |
| 14 | Project Scale | **Remove** | Dashboard duplicate of fig 01 with contradictory numbers. |
| 15 | Processing Progress | **Keep + Fix** | Useful workflow effort timeline. Overlapping labels. |

**Score: 3 keep, 2 rebuild, 2 need review, 8 remove.**

---

## Generalizable Rules for mousedb.figures

### 1. DATA FIGURES ONLY
The figure system should only generate figures that change when data changes. Architecture diagrams, schema diagrams, and pipeline infographics belong in documentation tools (Obsidian, CLAUDE.md, PowerPoint templates), not in the programmatic figure system. If a figure would look identical regardless of what data exists, it's not a data figure.

### 2. NO PIE/DONUT CHARTS
Pie charts are almost always worse than bars for comparison. Replace with stacked bars or grouped bars. The only exception might be a binary split (e.g., 70/30) where the visual "slice" is immediately informative.

### 3. LAYOUT VALIDATION IS MANDATORY
Every figure function must verify its output doesn't have:
- Overlapping text labels
- Text that extends beyond figure bounds
- Elements that are unreadable at the target DPI/size

Strategy: After rendering, check bounding boxes of text elements for overlap. Or use constrained layout managers (matplotlib's `constrained_layout=True`).

### 4. COLORS MUST ENCODE MEANING
Color should map to a meaningful variable (cohort, phase, outcome type), never be arbitrary decoration. Specifically:
- Per-subject figures: unique color per subject, consistent across all figures
- Per-cohort figures: cohort color from shared palette
- Per-phase figures: phase colors from shared palette (pre-injury, post-injury, rehab)
- Decorative color bars with no semantic meaning should be removed

### 5. ABBREVIATIONS MUST BE DEFINED
Any abbreviation used in a figure must either:
- Be defined in a legend/footnote within the figure
- Be spelled out in the methodology panel
- Link to a lookup table in the sidecar JSON

Examples: "Sa" in "Displaced Sa", brain region codes (GRN, MOp5, SSp-ul5).

### 6. INDIVIDUAL DATA POINTS MUST BE DISTINGUISHABLE
When showing individual subjects/brains as dots:
- Each must have a distinct, identifiable color
- A legend must map color to subject ID
- Anonymous gray dots are not acceptable — they hide the data structure

### 7. BRAIN/SUBJECT IDS MUST INCLUDE CONTEXT
Never show bare IDs like "Brain 349" or "Subject P1". Always include:
- Cohort membership (e.g., "349 CNT_01_02")
- Experimental condition if relevant
- This applies to legends, axis labels, and annotations

### 8. CROSS-FIGURE CONSISTENCY
The same dataset queried in different figures must produce the same count. Contradictions (e.g., "~96,000 cells" in one figure vs "25,659" in another) indicate broken data loading. The registry system should catch this by hashing the source data.

### 9. OUTLIER FLAGGING
When one data point dominates the scale (e.g., a TOTAL bar dwarfing all regions, or a single brain with 10x the cells of others):
- Either separate it (inset, broken axis, separate panel)
- Or flag it with an annotation explaining whether it's real or a data quality issue

### 10. PHASE/GROUPING STRUCTURE IS NOT OPTIONAL
Behavior data must always be structured by experimental phase. Aggregating across all phases hides the entire point of the experiment. Every behavior figure should show:
- Pre-injury baseline
- Post-injury acute
- Rehabilitation/recovery
- And group by subject within each phase

### 11. PROVENANCE ON EVERY FIGURE
Every data figure must include or reference:
- What source files produced the data (with hashes)
- What processing parameters were used (detection settings, calibration run ID)
- When the data was generated
- What tool versions were involved

This is handled by the registry system, but the figure itself should have a methodology panel or at minimum a sidecar JSON.

### 12. SCALE-AWARE DESIGN
Figures must work at the expected data scale. A grouped bar chart with 4 brains is fine; with 20 it breaks. Design should anticipate growth:
- Use scrollable/paginated layouts for large N
- Or switch chart types (heatmap instead of grouped bars)
- Or aggregate with proper statistical summaries

### 13. X-AXIS: EXPERIMENTAL TIMEPOINTS, NOT CALENDAR DATES
Never use raw calendar dates (2025-07-08) as the x-axis for longitudinal behavior data. Use experimental timepoints relative to a meaningful anchor:
- "Session 1, 2, 3..." or "Day 1, 2, 3..." relative to training start, injury, etc.
- Phase labels (Training, Baseline, Post-Injury, Rehab) as x-axis regions
- Calendar dates are meaningless to anyone who wasn't in the lab that day

### 14. TRAY TYPE / TASK DIFFICULTY MUST BE VISIBLE
Percent scores are misleading when task difficulty varies. 100% on easy trays is fundamentally different from 10% on pillar trays. Every behavior performance figure must show:
- What tray type was used for each session (annotation, color, or secondary axis)
- How many pellets were presented per session (not just percent retrieved)
- Raw counts alongside or instead of percentages when tray types vary

### 15. DATA SOURCE MUST BE OBVIOUS
Every figure must make clear whether data is:
- Manually collected (pellet scores entered by hand)
- Automatically derived (MouseReach pipeline output)
- Mixed (some manual, some automated)
This affects how the viewer interprets accuracy and precision.

### 16. FIGURE LEGEND != COLOR KEY
A color key (blue = group mean, light blue = 95% CI) is NOT a figure legend. A proper figure legend is a text block explaining:
- What is being plotted and why
- What each visual element represents
- How to interpret the figure
- Any caveats or context needed
Every data figure must have explanatory text, not just a color key.

### 17. NAME FIGURES ACCURATELY
"Learning curve" implies the training/acquisition phase. If most of the data covers baseline, injury, and rehab, don't call it a learning curve. Name figures for what they actually show:
- "Pellet Retrieval Performance Over Time" not "Learning Curve"
- "Recovery Trajectory" not "Learning Curve" if the focus is post-injury
- Accurate names prevent misinterpretation

### 18. INDIVIDUAL TRACES MUST REVEAL SUB-EFFECTS
When displaying individual subjects over time:
- Use distinct colors/styles per subject (not anonymous lines)
- This reveals hidden sub-populations: fast vs slow learners, handedness bias, non-responders
- If all individuals look the same, the figure is hiding information
- Look for: baseline performance differences (inherent bias?), divergent recovery rates, ceiling/floor effects

### 19. QUARTILES > MEAN+SEM FOR HIGH-VARIANCE DATA
When data has huge spread (0-100% range), mean + SEM bars are misleading — the SEM is tiny relative to the actual distribution. Use:
- Box/violin plots showing quartiles and full distribution
- Or strip plots with median + IQR
- Mean is only informative when variance is small relative to effect size

### 20. CROSS-PHASE COMPARISONS MUST TRACK INDIVIDUALS
A phase comparison figure should connect the same subject across phases (connected dot plot / spaghetti plot). This answers: "Did THIS mouse get worse after injury and recover during rehab?" Unconnected dots across phases hide within-subject trajectories and make it impossible to see if recovery is real or driven by a few outliers.

### 21. STATISTICAL REFERENCE POINT MATTERS
When comparing across phases (Training vs Post-Injury vs Rehab), the experimental design determines the reference. Post-injury is the manipulation — it should be the reference point for comparisons:
- Post-Injury vs Pre-Injury (did injury cause a deficit?)
- Post-Injury vs Rehab (did rehab restore function?)
- Don't just compare everything to Training — that conflates learning effects with injury effects.

### 22. FILTER TO COMPARABLE CONDITIONS
Only data collected under equivalent conditions can be compared across phases. For pellet retrieval:
- Only PILLAR tray sessions are valid testing data for cross-phase comparison
- Training trays, ramp trays, and other non-pillar sessions must be excluded or shown separately
- "Rehab" that includes non-pillar sessions inflates apparent recovery
- Every phase-comparison figure must state what tray type filter was applied

### 23. DAYS-BASED X-AXIS MUST HANDLE GAPS
If using DPI (days post-injury) or similar day-based axis, days with no testing produce zeros that distort the curve. Either:
- Use session number (sequential sessions only), not calendar days
- Or explicitly mark missing days as gaps (broken axis), not as zero-value data points
- Zero performance and no-data-collected are fundamentally different and must never be confused

### 24. TRAY TYPE DETERMINES WHAT CAN BE MEASURED
Not all tray types produce equivalent data:
- **Pillar trays**: valid testing data — retrieval success reflects true motor ability
- **Flat/Easy trays**: only measure engagement/attention — mice can only "fail" these by giving up, not by lacking motor skill
- **DLC tracking only works on pillar trays** — flat/easy videos produce engagement scores only, never kinematic data
- Never mix tray types in a single performance curve. Flat/easy data belongs in a separate "engagement" panel, not averaged with pillar data.

### 25. EVERY FIGURE MUST TELL A COMPLETE STORY
A data figure is not just a plot — it must be a self-contained narrative:
1. **Question**: What question does this figure answer?
2. **Method**: What was done? (inclusion criteria, filtering, tray types, phases)
3. **Finding**: What does the data show? (main effect)
4. **Analysis**: How was significance determined? (test name, correction method)
5. **Effect size**: Cohen's d (or equivalent) for every statistically significant finding — p-values alone are not enough
6. **Confounds**: What alternative explanations exist? What might be contributing?
7. **Follow-up**: What questions does this raise?

This narrative belongs in the methodology panel / figure legend. A figure without this context is uninterpretable.

### 26. EFFECT SIZES ARE MANDATORY
Every statistically significant result must report achieved Cohen's d (or appropriate effect size measure). P-values tell you whether an effect exists; effect sizes tell you whether it matters. A p<0.0001 with d=0.1 is a trivially small effect that happened to reach significance due to large N. Always report both.

### 28. BASELINE NORMALIZATION NEEDS A FLOOR
When normalizing to pre-injury baseline (showing % of baseline), animals with very low baselines produce absurd ratios. A mouse at 3% pre-injury retrieving 9% post-rehab shows "300% of baseline" — misleading.
- Set a minimum baseline threshold for inclusion in normalized analyses
- Animals below the floor should be excluded or shown separately
- Always show raw data alongside normalized data so the denominator is visible
- Flag any animal exceeding 100% of baseline recovery as needing manual review — it may indicate a data or detection error, not genuine super-recovery

### 29. ONE IDEA PER FIGURE
Don't cram 4+ panels into one image when each panel has enough information to stand alone. Multi-panel figures should only combine views that MUST be seen together to make a point. If panels can be interpreted independently, they should be separate figures. Each figure should answer ONE clear question.

### 30. KINEMATIC DATA MUST BE FILTERED FOR PLAUSIBLE RANGES
Raw kinematic features contain detection artifacts (e.g., 1750-frame reach duration, 400+ px/frame velocity). Before plotting:
- Define physiologically plausible ranges per feature (e.g., max extent > 0mm, duration > 1 frame)
- A reach with 0mm extent is not a reach. A reach that is 1 frame long is not a reach.
- Negative max extent is impossible — indicates a fundamental analysis bug, not an outlier
- Exclude or clip values outside plausible ranges
- State the filtering criteria in the methodology panel
- Report how many reaches were excluded and why
- Consider PCA or GMM-based pre-filtering to identify and remove noise clusters ("minis", detection artifacts) before analysis

### 31. KINEMATIC ANALYSIS: FILTER BY OUTCOME AND RELEVANCE
Not all reaches are equally informative for all features:
- **Successful retrievals** are the primary interest for kinematic profile analysis (how does the shape of a successful reach change after injury?)
- **Displacements** are also interesting (the mouse contacted the pellet but couldn't grasp it — motor control without fine manipulation)
- **Untouched/misses** — may be useful for trajectory analysis but not for features like max extent
- Don't dump all reach types into one analysis. Separate by outcome, then analyze each meaningfully.
- The core scientific question: **how do kinematic profiles of successful retrieval shift across phases?**

### 32. WITHIN-SUBJECT EFFECTS ARE MORE INTERESTING
For longitudinal injury/recovery data:
- Within-subject changes (how did THIS mouse's kinematics change from pre to post?) are more informative than between-group averages
- LMM (linear mixed models) with subject as random effect is likely better than simple ANOVA
- Repeated measures must be accounted for — these are the same mice measured multiple times
- Show per-subject kinematic trajectories, not just group summaries

### 33. SPLIT TIMEPOINTS WHEN THEY DIFFER
If Post-Injury Day 1 shows different patterns from Post-Injury Days 2-4, they should be split:
- Don't average across timepoints that might be biologically distinct
- Acute post-injury (day 1) vs subacute (days 2-4) vs chronic (weeks later) may reflect different mechanisms
- Let the data tell you where the splits should be — if adjacent timepoints cluster together, combine them; if they diverge, split them

### 34. EVERY ANALYSIS MUST JUSTIFY ITS STATISTICAL APPROACH
Don't just run a test — explain why that test:
- Why chi-sq/Fisher vs paired t-test vs Wilcoxon vs LMM?
- What assumptions does the test make? Are they met?
- Is this a between-subjects or within-subjects comparison?
- What is the comparison structure? (which groups vs which?)
- Are repeated measures accounted for?
- A figure with p-values but no justification for the test is worse than no stats at all — it implies rigor that may not exist

### 35. TICK ALIGNMENT MUST BE UNAMBIGUOUS
Data points must clearly sit ON tick marks, not between them. If dates/sessions are the x-axis, each data point should align exactly with its tick. Ambiguous alignment makes it impossible to read exact values.

### 36. DIRECTION OF "GOOD" MUST BE INDICATED
When showing change scores (% change from baseline, normalization indices, etc.), indicate which direction represents recovery/improvement. Not all features improve by increasing — shorter reach duration or lower variability may indicate recovery. Use arrows, color coding, or explicit labels to mark the "recovery direction" for each metric.

### 37. RECOVERY/GROUP THRESHOLD MUST BE STATED ON FIGURE
If animals are split into groups by a threshold (e.g., "recovered" = rehab eaten% >= 80% of pre-injury), the threshold value, its justification, and the resulting group sizes must be visible on the figure itself — not just in the script. Arbitrary thresholds without justification undermine the entire analysis.

### 38. NEVER DISPLAY EMPTY DATA COLUMNS
If a condition has no data (e.g., "Rehab Easy" with zero observations), remove it from the figure entirely. Blank space in a heatmap or chart implies zero change, not missing data. Empty columns waste space, mislead viewers, and make the figure harder to read. Only show conditions that have data.

### 39. KEY COMPARISONS MUST BE VISUALLY PROMINENT
The main experimental comparison (e.g., post-injury vs rehab pillar) must be the most visually distinct pairing in the figure. If the two conditions being compared look grouped or blended into adjacent columns, the figure fails to communicate its purpose. Use spacing, borders, color breaks, or explicit annotation to separate the key comparison from surrounding context.

### 40. RECOVERY DEFINITION MUST ACCOUNT FOR DEFICIT DEPTH
Defining "recovered" as (rehab_metric / pre_metric >= threshold) rewards animals that never lost much function. An animal at 90% that drops to 75% (ratio 0.83) is "recovered" while one that drops to 5% and fights back to 70% (ratio 0.78) is not. Recovery definitions must account for:
- The magnitude of deficit (post-injury drop from baseline)
- The amount of restoration (rehab improvement from post-injury nadir)
- Consider: recovery_ratio = (rehab - post_injury) / (pre_injury - post_injury) -- proportion of lost function restored
- The threshold, its justification, and its formula must be visible on the figure

### 41. REHAB TEST vs REHAB TRAINING ARE DIFFERENT DATA
Pillar sessions during rehabilitation training (practice/therapy) are fundamentally different from the post-rehab pillar TEST (final assessment). They must NEVER be lumped together as "Rehab Pillar." The rehabilitation protocol includes guided/assisted reaching on pillar trays -- those are therapy, not testing. Only the final post-rehab test session(s) should be used for recovery assessment. Scripts must explicitly distinguish:
- `Rehab_Training_Pillar` -- practice sessions during rehab protocol (exclude from performance comparison)
- `Post_Rehab_Test_Pillar` -- the actual test after rehab is complete (use for comparison)

### 42. EVERY TERM ON A FIGURE MUST BE OPERATIONALLY DEFINED
"Recovered", "not recovered", "max extent", "smoothness", "hand rotation" -- every term used on a figure must have an operational definition visible on the figure or in the legend:
- How is it calculated? (formula or algorithm reference)
- What units? What range of values is possible?
- What does high vs low mean biologically?
- This is especially critical for derived metrics that aren't self-explanatory

### 43. FIGURES MUST SUPPORT HYPOTHESIS FORMATION AND EVALUATION
A figure must give the viewer enough information to form AND evaluate hypotheses from it. If someone sees an unexpected pattern (e.g., inverted head width suggesting compensatory head rotation) and their next thought is "I need to go read the script to know if this is real" -- the figure has failed. The legend and methodology must provide enough context that the viewer can reason about whether a pattern is:
- A real biological signal (compensatory strategy, subpopulation effect)
- A measurement artifact (DLC tracking error, keypoint mislabeling)
- A data contamination issue (wrong reaches included, rehab training mixed with test)
Without leaving the figure. This is the ultimate test of figure quality: can a knowledgeable viewer generate and evaluate hypotheses from this figure alone?

### 44. SESSION LABELS MUST BE DESCRIPTIVE, NOT NUMERIC
"Rehab Session 3" or "Session 5" is meaningless without context. Labels must describe the experimental context: "Day 5: Pillar Test After 3 Days Easy Training", "Post-Rehab Test (Pillar)", etc. The viewer needs to know what happened at each timepoint -- what tray type, what protocol stage, what the purpose of that session was. Numeric session labels hide the experimental design.

### 45. WEEKEND/GAP EFFECTS MUST BE ANNOTATED ON TEMPORAL FIGURES
There is a known weekend effect on mouse engagement -- performance drops after 2-day breaks. Any temporal figure (session-by-session, day-by-day) must annotate weekends and other gaps. Without this, a dip at session 3 could be misinterpreted as a treatment effect when it's just Monday. Mark weekends, holidays, and any gap > 1 day between sessions.

### 46. ENGAGEMENT/ATTENTION MUST ACCOMPANY PERFORMANCE FIGURES
Without an attention/engagement metric alongside performance, you cannot distinguish "the mouse couldn't do it" (motor deficit) from "the mouse didn't try" (motivational/engagement issue). If rehabilitation improves engagement but not motor function, that is a completely different scientific conclusion. Every performance figure should either:
- Include an attention score panel
- Or explicitly state that engagement was controlled/verified
- This is critical for interpreting rehab effects: is rehab restoring motor ability or just re-engaging the animal?

### 47. FIGURE TITLES MUST NOT PRESUPPOSE MECHANISMS
"Kinematic Learning During Rehabilitation" presupposes that changes during rehab are learning. They could be recovery, compensation, re-engagement, practice effects, or spontaneous recovery. True learning (skill acquisition) only applies to the pre-injury training phase. Post-injury changes are an open question -- the figure should describe what is SHOWN, not what is CONCLUDED:
- BAD: "Kinematic Learning During Rehabilitation"
- GOOD: "Kinematic Feature Changes Across Rehab Sessions"
- BAD: "Recovery of Reaching Function"
- GOOD: "Reaching Performance After Rehabilitation"
This is a form of fundamental attribution error in figure naming. The title should never answer the scientific question before the data does.

### 48. NO IMPLEMENTATION DETAILS IN FIGURE TEXT
Internal codes, column names, and numeric encodings must never appear on a figure. "(0=Miss, 1=Displaced, 2=Retrieved)" exposes the database schema to the viewer. "Segment-level" is pipeline jargon. Figures are for communicating science, not debugging code. Replace implementation terms with domain language:
- BAD: "Outcome (0=Miss, 1=Displaced, 2=Retrieved)"
- GOOD: "Reach Outcome (Miss / Displaced / Retrieved)"
- BAD: "Segment-level outcomes"
- GOOD: "Per-reach outcomes" or "Individual reach outcomes"

### 50. CORRECTLY ATTRIBUTE THE ANALYSIS TOOL
DLC (DeepLabCut) detects pose keypoints. MouseReach uses DLC tracking data to detect reaches and classify outcomes (retrieved, displaced, miss). Saying "DLC-detected retrieval rate" is wrong -- DLC has no concept of retrieval. The correct attribution is "MouseReach pipeline (using DLC tracking)." Every figure must correctly attribute which tool performed which analysis. Misattribution undermines trust in the entire analysis chain.

### 49. UNEQUAL OBSERVATION COUNTS ACROSS PHASES MUST BE EXPLAINED
If Pre-Injury has 2493 reaches but Post-Injury has 473 (a 5:1 ratio), the figure must explain why. Fewer reaches post-injury could mean: fewer sessions, shorter sessions, reduced engagement, or the mouse stopped reaching. Each explanation has different implications for interpretation. State the number of sessions, reaches per session, and reason for count differences in the methodology panel.

---

## Review 2: visualizations.py

### learning_curve.png (CNT_01)
**Verdict: Rebuild**
- Way too small, unreadable at generated size
- Calendar dates instead of experimental timepoints (Rule 13)
- Percent without tray type context (Rule 14)
- No indication of pellet count per session
- No data source indication (manual vs MouseReach) (Rule 15)
- Anonymous light blue individual lines (Rule 18)
- Color key but no figure legend (Rule 16)
- "Learning curve" misnomer — most data is post-training (Rule 17)
- No phase annotations on x-axis
- Injury marker good but needs on-plot label

### phase_comparison.png (CNT_01)
**Verdict: Rebuild**
- Same percent-without-tray-type problem (Rule 14, 22) — phases use different trays, can't compare raw %
- "Rehab" likely includes non-pillar sessions, inflating recovery (Rule 22)
- Anonymous gray dots — can't track individuals across phases (Rule 18, 20)
- Bar + mean + SEM misleading with 0-100% spread — use quartiles/violin (Rule 19)
- No connected individual traces across phases (Rule 20)
- Phase naming confusing: "Pre-Injury" shows LOW performance — is this really pre-baseline?
- No statistical tests or brackets
- No figure legend (Rule 16)
- Error bars type (SEM? SD?) not stated
- Should use Post-Injury as reference for stats, not Training (Rule 21)

### pellet_heatmap.png (CNT_01)
**Verdict: Rebuild**
- Question: does pellet position affect retrieval success? (fatigue/spatial bias analysis)
- Numbers in cells are ambiguous — counts? percentages? Not labeled clearly
- Colorbar range 0-100% but data is all 25-37 — no visual differentiation, entire heatmap is same orange
- "Tray 1-4" doesn't name the tray types (Pillar? Ramp? Training?)
- Pellet positions 1-20 have no spatial layout context
- Aggregated across all subjects/sessions/phases — no structure
- Answer likely varies by timepoint and tray type
- Needs much more data to be meaningful — defer until N is large enough

### recovery_trajectory.png (CNT_01)
**Verdict: Rebuild — closest to useful, needs fixes**
- GOOD: Uses Days Post-Injury (DPI) as x-axis — correct anchor point (Rule 13)
- GOOD: Injury event clearly marked
- GOOD: Story shape visible (high pre-injury -> drop -> partial recovery)
- Way too small, unreadable at generated size
- Still no tray type filtering — pre-injury spike vs recovery may be tray artifact (Rule 14, 22)
- Anonymous pink individual lines — can't distinguish subjects (Rule 18)
- 95% CI band covers entire y-axis — huge variance makes mean meaningless (Rule 19)
- No figure legend (Rule 16)
- All red/pink — no phase color differentiation on the continuous timeline
- Best candidate for recipe conversion: fix tray filter, add individual colors, add legend

### weight_tracking.png (CNT_01)
**Verdict: Failed to generate**
- dtype error: Column 'date' has dtype object, cannot use nsmallest
- Needs date parsing fix before review

---

## Review 3: plot_connectome_behavior.py (Grant figures)

### behavior_CNT_01.png
**Verdict: Rebuild — best data pipeline so far, visualization needs overhaul**
- GOOD: Real stats (chi-sq/Fisher + Holm correction, omnibus p-values)
- GOOD: Shows both Retrieved and Contacted outcomes
- GOOD: Subtitle has N, learner criteria, injury model
- GOOD: Phase labels include session counts and pellet counts (Rule 14)
- GOOD: "Rehab Pillar" explicitly filtered (Rule 22)
- Element overlaps make text unreadable (Rule 3)
- Anonymous gray dots — no individual tracking across phases (Rule 18, 20)
- Need connected lines between individuals across phases
- Bar + SEM misleading with this variance (Rule 19)
- All gray — no color coding
- Top and bottom rows appear redundant (stars vs exact p) — unclear why both exist
- No figure legend / methodology text (Rule 16, 25)
- No Cohen's d for significant findings (Rule 26)
- No confounds or follow-up questions stated (Rule 25)
- Stats need justification: why chi-sq/Fisher? Why not paired test since same subjects?

### recovery_CNT_01.png
**Verdict: Improve — best individual-tracking figure so far**
- GOOD: Connected individual traces across phases with color-coded outcomes
- GOOD: Recovery categorization (green=recovered >60%, orange=improved, red=no improvement)
- GOOD: Stats in legend area (p-values, mean diff)
- GOOD: Shows both Retrieved and Contacted
- Too small — legend boxes unreadable
- N=8 vs N=9 in behavior figure — unexplained dropout
- Recovery thresholds (60%, 40%) not justified
- Subject IDs not labeled on lines
- No Cohen's d (Rule 26)
- No methodology panel (Rule 25)

### trajectory_CNT_01.png
**Verdict: Rebuild — too many panels, same issues as recovery**
- 4 panels crammed into one image (Rule 29)
- Waterfall charts good concept but animal IDs unreadable
- Stats boxes unreadable at this size
- Same individual tracking issues as recovery figure

### mega_cohort_eaten.png
**Verdict: Rebuild**
- 4 panels crammed in, too small (Rule 29)
- Baseline normalization artifact: one animal at 250%+ recovery is almost certainly a low-baseline ratio problem (Rule 28)
- Pooled N=37 across 4 cohorts without accounting for cohort as random effect
- Need to show raw alongside normalized (Rule 28)
- Waterfall chart outlier crushes scale (Rule 9)
- Box plots (bottom right) are good choice but compressed

### kinematics_by_phase.png
**Verdict: Rebuild — fundamental analysis issues**
- Negative max extent values are impossible — indicates analysis bug (Rule 30)
- 0mm max extent and 1-frame duration reaches should be excluded — not real reaches (Rule 30)
- All reach types dumped together — should filter to successful retrievals primarily (Rule 31)
- Displacements separately interesting (Rule 31)
- Consider PCA/GMM pre-filtering to remove "minis" and detection artifacts (Rule 30)
- Outliers compress actual data to bottom of panels (Rule 9, 30)
- Box plots are right chart type (Rule 19) but scale is broken by artifacts
- Has KW p-values but no pairwise comparisons, no Cohen's d (Rule 26)
- No per-subject analysis — within-subject effects more interesting (Rule 32)
- Post-Injury should be split: day 1 vs days 2-4 may differ (Rule 33)
- No justification for statistical approach (Rule 34)
- No figure legend or methodology (Rule 16, 25)
- Core question should be: how do kinematic profiles of successful retrieval shift across phases?

### kinematics_by_cohort.png — not yet reviewed

---

## Review 4: kinematic_recovery.py (5 figures)

### fig1_normalization_heatmap.png
**Verdict: Rebuild — fundamentally uninterpretable**
- GOOD: Heatmap is right chart type for features x timepoints
- GOOD: Side-by-side recovered/not-recovered comparison is right structure
- GOOD: Blue/red diverging color scale is intuitive
- Rehab Easy column is completely blank — no data exists, yet it takes up space (Rule 38)
- Rehab Pillar vs Post-Injury Test are not visually distinguished — look grouped (Rule 39)
- Y-axis feature labels are tiny and overlap each other (Rule 3)
- Percentage values inside cells overlap with cell boundaries (Rule 3)
- Colorbar label "% Change from Pre-Injury" is crammed and partially obscured (Rule 3)
- Feature category legend overlaps with data area (Rule 3)
- N=5 recovered vs N=24 not recovered — huge imbalance not called out
- Recovery threshold (>=80% of pre-injury) not shown on figure (Rule 37)
- No indication of which direction is "good" per feature (Rule 36)
- Some values >100% change likely from near-zero baselines (Rule 28)
- No stats — which changes are significant? No p-values, no Cohen's d (Rule 26)
- No figure legend or methodology panel (Rule 16, 25)
- Mixing tray types on x-axis (Rehab Easy/Flat/Pillar) — only Pillar is valid for kinematics (Rule 22, 24)
- "Not Recovered" N=24 — are these genuinely non-recovered or insufficient data?

### fig2_pre_vs_rehab_paired.png
**Verdict: Rebuild — fundamentally unclear what it's trying to say**
- Too many panels crammed in — each could be its own figure (Rule 29)
- "Recovered" vs "Not Recovered" labels undefined — what threshold? what metric? stated nowhere on figure (Rule 37)
- Spaghetti lines make individual tracking impossible — use paired bars or connected dot plots instead (Rule 18, 20)
- Axis labels unreadable at generated size (Rule 3, 12)
- No figure legend — what question is this answering? What does "recovery" mean here? (Rule 16, 25)
- No effect sizes (Rule 26)
- Inclusion criteria unstated — should be pillar trays only, correct timepoints, successful retrievals only (Rule 22, 24, 31)
- No confounds section — tray familiarity, hand preference, session order effects? (Rule 25)
- No stat justification — why these tests for this design? (Rule 34)
- "Does it say something about the natural world or about the analytical tools?" — this figure doesn't answer either clearly
- Better alternatives: paired bar charts with individual points, connected dot plots with clear phase separation
- Main question should be explicit: "Does pellet retrieval recover after CST injury, and do kinematic profiles normalize?"

### fig3_kinematic_trajectories.png
**Verdict: Rebuild — fundamental analysis contamination**
- 9 panels crammed into one figure (Rule 29) — should be 3 figures grouped by feature type (spatial, temporal, postural)
- Shaded confidence bands span entire y-axis — means are meaningless with this variance (Rule 19). Show individual subject trajectories instead.
- "Recovered" (N=5) vs "Not Recovered" (N=24) still undefined on figure (Rule 37, 40)
- Recovery definition is flawed: rewards "didn't lose much" not "actually recovered" (Rule 40)
- No stats anywhere — no p-values, no effect sizes for any of the 9 features (Rule 26)
- No direction-of-recovery indicators per metric (Rule 36) — shorter duration = better, but nothing indicates this
- No individual data points — can't see if mean represents any actual animal (Rule 6, 18)
- Some panels contradict recovery narrative (hand rotation, nose-to-slit decline in "recovered" group) — unexplained
- Band overlap makes groups indistinguishable in most panels — if bands overlap completely, the feature doesn't differentiate groups, so why show it?
- CRITICAL: "Rehab Pillar" includes rehabilitation TRAINING sessions mixed with post-rehab TEST (Rule 41) — data contamination
- CRITICAL: NO reach outcome filtering — retrieved, displaced, missed all mixed together (Rule 31)
- CRITICAL: NO plausible range filtering — artifact reaches included in means (Rule 30)
- No operational definitions for any kinematic term (Rule 42)
- Sidecar JSON has empty data_sources — zero provenance (Rule 11)
- Only 3 timepoints instead of standard 4 — Post-Injury not split into Day 1 vs Days 2-4 (Rule 33)

### fig4_recovery_index.png
**Verdict: Best concept so far, but unusable without context**
- GOOD: Recovery Index formula (rehab - post) / (pre - post) is the RIGHT metric — measures proportion of lost function restored
- GOOD: Subtitle defines scale (1.0 = full return, 0.0 = no change from post-injury)
- GOOD: Reference lines at 0.0 and 1.0 are helpful
- GOOD: Horizontal bars make feature names readable
- GOOD: Inverted-expectation features (head width negative for recovered) provoke genuinely important hypotheses (compensatory head rotation strategy?)
- BUT: A viewer cannot evaluate whether unexpected patterns are real biology, measurement artifacts, or data contamination — the figure doesn't provide enough context (Rule 43)
- Error bars span entire axis — means are meaningless with this variance, show individual animal dots (Rule 19)
- No stats — which features actually differ between groups? (Rule 26)
- N not stated on figure
- Non-kinematic features mixed in (Frames Low Confidence, Pellet Position Idealness, Attention Score) without category separation
- Features not grouped by type (distance, velocity, timing, quality, posture all jumbled)
- Values > 1.0 ("over-recovery") not flagged or explained (Rule 28)
- Same underlying data contamination: rehab training mixed with test (Rule 41), no outcome filtering (Rule 31), no plausible range filtering (Rule 30)
- No figure legend (Rule 25) — Recovery Index formula should be explicitly stated
- No operational definitions for kinematic terms (Rule 42)

### fig5_rehab_kinematic_learning.png
**Verdict: Rebuild — uninterpretable without session context**
- GOOD: Session-by-session view during rehab is the right idea — shows within-rehab trajectory
- Y-axis values appear z-scored or normalized but doesn't say so — "Max Extent (mm)" ranges from -1.2 to 0.4 which can't be raw (Rule 42)
- "Rehab Session 1-5" is meaningless — what happened at each session? What tray type? What protocol stage? (Rule 44)
- No weekend/gap annotations — known engagement effect after breaks (Rule 45)
- No attention/engagement metric alongside performance — can't tell if rehab improves motor function or just re-engages animals (Rule 46)
- With N=5 recovered across 5 sessions, some points may be single animals — N per session not shown
- Duration panel suggests recovered mice never lost speed — possible that "recovery" = "never had a deficit" not "regained function" (Rule 40)
- Recovered group appears WORSE on several metrics — contradicts premise, unexplained
- Same recurring issues: undefined recovery split, no stats, no figure legend, no outcome/plausible range filtering, confidence bands dominate everything
- Title says "Learning" — fundamental attribution error; changes during rehab are not necessarily learning (Rule 47)

## Review 5: kinematic_recovery_stratified.py

### fig1_outcome_distribution.png
**Verdict: Closest to useful — right chart type, right data, needs context**
- GOOD: Stacked bars are right for outcome proportions
- GOOD: N counts shown per phase/group (first figure in this series to do this)
- GOOD: Traffic-light colors (red/orange/green) for Miss/Displaced/Retrieved are intuitive
- GOOD: Data source stated: "Segment-level outcomes from MouseReach/DLC" (Rule 15)
- GOOD: Clear story visible: retrieved% drops post-injury, partially returns at rehab
- Title uses internal numeric codes "(0=Miss, 1=Displaced, 2=Retrieved)" — implementation leaking into figure (Rule 48)
- "Segment-level" is undefined pipeline jargon (Rule 42, 48)
- Right panel error bars enormous — N=24 at near 0% with whiskers spanning 0-25%
- Recovered N=5 has 2493 pre-injury reaches vs 473 post-injury (5:1 ratio) — why? Not explained (Rule 49)
- Same undefined recovery split (Rule 37, 40)
- No figure legend (Rule 25)
- No stats on stacked bars — are distributions significantly different between phases/groups? (Rule 26)

---

### fig2_dlc_vs_manual.png
**Verdict: Important validation figure — needs cleanup, not rebuild**
- GOOD: Validation figure comparing automated vs manual scoring — fundamentally important
- GOOD: Bland-Altman is the correct method for agreement analysis
- GOOD: N=705 sessions is good sample size
- GOOD: Mean bias reported (Miss: -11.1%, Displaced: -14.9%, Retrieved: -1.9%)
- GOOD: Spearman r values shown
- 6 panels crammed in, stats boxes nearly unreadable (Rule 3, 29)
- Axis labels use internal codes "Manual Miss (Score=0) %" (Rule 48)
- DLC bias story is buried: DLC systematically underestimates Miss by 11% and Displaced by 15% — this is a headline finding that affects every downstream figure, should be prominently called out
- No figure legend explaining what the validation means for downstream analyses (Rule 25)
- Heavy overplotting in scatter panels — hex-bin or 2D histogram would show density better
- Title says "MouseReach/DLC" — should clearly state MouseReach (Rule 50)

### fig3_matched_displaced.png & fig3_matched_miss.png
**Verdict: Right concept, same execution problems**
- GOOD: Outcome matching (displaced-only, miss-only) is the correct approach — controls for outcome as a confound (Rule 31)
- GOOD: Comparing same outcome type across phases is a legitimate and interesting question
- 11 panels each — far too many (Rule 29)
- "score=1" / "score=0" in titles — implementation leaking (Rule 48)
- Error bars dominate, individual dots needed (Rule 19)
- Legends tiny and unreadable (Rule 3)
- No stats, no effect sizes (Rule 26)
- The interesting comparison (displaced kinematics vs miss kinematics) requires both on same figure or same scale — currently impossible to compare between the two figures
- Same recurring issues: undefined recovery split, no figure legend, no plausible range filtering

## Pending Reviews

- [ ] visualizations.py (6 figures: learning curves, phase comparison, pellet heatmap, weight curves, recovery trajectory)
- [ ] Grant/presentation scripts (make_presentation_figures.py, plot_connectome_behavior.py, kinematic_recovery.py, kinematic_recovery_stratified.py, predict_recovery.py)
- [ ] lab_figures.py figures 10, 11 (failed, need retry)

---

## Action Items

1. **Codify rules above into `mousedb.figures.standards`** as enforceable checks
2. **Add layout validation utilities** to detect overlapping text
3. **Create region name lookup** (abbreviation -> full name) as a mousedb utility
4. **Establish canonical subject color map** that's consistent across all figures
5. **Delete or archive architecture diagram figures** (01, 02, 03, 08, 12, 13, 14) from lab_figures.py
6. **Rebuild fig 09** with phase/subject structure as a proper recipe
7. **Convert fig 04, 06, 15** into proper FigureRecipe classes
