"""Analytical helpers extracted from the original CFS notebook.

Each submodule corresponds to one pipeline stage. See ``docs/reading_order.md``
for the order in which the notebooks call these.

Modules:
    kinematics   - feature selection, aggregation, proportion computations
    connectivity - long-to-wide pivot of cell-count data
    filters      - subject-intersection filtering between data blocks
    dimreduce    - PCA per phase and PLS across blocks
    models       - linear mixed models for phase effects
    plotting     - figure helpers (scree plots, loading bars, PLS triptychs)
"""
