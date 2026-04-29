# Linear mixed models in this analysis

Notebook 05 tests whether each kinematic feature changes across experimental
phases. A naive per-subject-mean ANOVA would throw away the hundreds of
reaches per session and the multiple sessions per subject per phase.
Linear mixed models (LMMs) keep the raw reach-level data and partition
variance properly across three nested levels.

---

## The hierarchy

```
reach (residual variance)
  within session (session random intercept)
    within subject (subject random intercept)
      at phase (fixed effect we're testing)
```

- **Reach variance** is huge (hundreds of reaches per session). Helps
  estimate the noise floor.
- **Session variance** captures the fact that reaches from one session
  are correlated: same motivation, same rig calibration, same pellet
  batch. Ignoring it inflates effective N.
- **Subject variance** is the true experimental unit. With few subjects
  it's noisy but still estimable.

---

## The formula

In `helpers/models.py::fit_phase_lmm`:

```python
mixedlm(
    formula=f"Q('{feature}') ~ C(phase_group)",
    data=subset,
    groups='subject_id',
    vc_formula={'session': '0 + C(session_date)'},
)
```

- `C(phase_group)` treats phase as categorical with the Baseline (or the
  first listed phase) as the reference.
- `groups='subject_id'` adds a random intercept for each subject.
- `vc_formula` adds a nested variance component for each session. The
  `0 +` means "no intercept offset, just a variance component". This is
  statsmodels' slightly awkward way of expressing what lme4 writes as
  `(1 | subject_id / session_date)`.

---

## What is reported

For each feature we record:

- **`phase_p`**: omnibus Wald chi-square p-value for the phase term
  ("does phase matter at all, pooling over levels?").
- **`phase_p_adj`**: Benjamini-Hochberg FDR correction across the
  features that converged.
- **`converged`**: whether the optimizer reported convergence. Features
  with singular random-effect covariance or numerical failures are
  flagged and kept out of the FDR pool.
- **`n_reaches`, `n_subjects`**: sample size the model actually saw.

Two narrower analyses run with the same helper:

- **Deficit delta**: subset to Baseline + Post_Injury_2-4. With only two
  phase levels, the Wald p-value IS the contrast.
- **Recovery delta**: subset to Post_Injury_2-4 + Post_Rehab_Test. Same.

---

## Small-sample caveat

statsmodels uses a chi-square Wald approximation for the omnibus test
rather than Satterthwaite or Kenward-Roger degrees-of-freedom correction.
At small N subjects this is mildly anti-conservative (p-values slightly too
small). For a rigorous small-sample test, pymer4 (R's lme4 wrapper via
Python) provides Kenward-Roger df. That's a future enhancement; worth a
methods-section footnote when writing up.

See [`../assumptions.md`](../assumptions.md) for the broader discussion of
what the results can and cannot claim at current sample size.
