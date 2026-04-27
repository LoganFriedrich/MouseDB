"""
Figure Recipes - declarative figure definitions for the Connectome project.

A recipe defines what data a figure needs, what analysis to run, how to plot it,
and what metadata to display. The system handles provenance, themes, and
registry registration automatically.

Usage:
    from mousedb.recipes.behavior import PelletScoreRecovery

    recipe = PelletScoreRecovery()
    record = recipe.generate(output_dir="figures/presentation/")
    print(f"Generated: {record}")  # FIG-20260304-0002
"""

from .base import FigureRecipe, DataSource

# Behavior recipes
from .behavior import (
    PelletScoreRecovery, PelletRecoveryCNT01, OutcomeDistributionShift,
    CohortBehaviorSummary, CohortRecoveryTrajectory, RecoveryWaterfall,
    MegaCohortPooled, KinematicsByPhase, KinematicsByCohort,
)

# Kinematics recipes
from .kinematics import KinematicRecoveryIndex, KinematicPhaseComparison

# Tissue recipes
from .tissue import (
    PipelineStageComparison,
    ElifeComparison,
    EnhancerResponseSummary,
    EnhancerRepresentativeImages,
    EnhancerCrossAnimalHeatmap,
    ClassifierInstabilityAnalysis,
    RegionalPatternComparison,
)

# Recovery recipes
from .recovery import (
    RecoveryOverview, RecoveredVsNot, TrajectoryProfiles,
    RehabLearningCurve, KinematicPredictors, PredictorSummary,
    ContactedVsEatenRecovery,
)

# Kinematic recovery recipes (migrated from Connectome_Grant/kinematic_recovery.py)
from .kinematic_recovery import (
    NormalizationHeatmap, PreVsRehabKinematics, KinematicTrajectories,
    KinematicRecoveryIndex as KinematicRecoveryIndexByGroup,
    RehabKinematicChange,
)
# Kinematic stratified recipes (migrated from kinematic_recovery_stratified.py)
from .kinematic_stratified import (
    StratifiedOutcomeDistribution, DlcVsManualValidation,
    OutcomeMatchedKinematics, ReachesToInteraction,
    ReachFatigueAnalysis, FirstReachAnalysis, HitVsMissKinematics,
    PhaseSeparatedKinematics, TemporalKinematicTrajectory,
    SpontaneousRecoveryAnalysis, IndividualKinematicTrajectories,
    ReachCapacityAnalysis,
)
from .lab_overview import (
    BrainRegionCountsLab, HemisphereLaterality,
    ReachOutcomeSummary, KinematicComparisonLab, ProcessingProgress,
)
