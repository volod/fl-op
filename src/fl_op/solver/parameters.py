"""Tunable solver parameters threaded through the chain.

One frozen, picklable object carries the parameters worth tuning per run
(Optuna trials, profile experiments). Field defaults are the env-backed
engine constants, so ``SolverParameters()`` reproduces the untuned behavior
and a caller overrides only what a trial varies.
"""

import dataclasses
from typing import Any

from fl_op.core.constants import (
    CLUSTER_SOLVE_TIME_LIMIT_S,
    CLUSTER_TARGET_SIZE,
    CLUSTER_LNS_ENABLED,
    CLUSTER_LNS_TIME_LIMIT_S,
    DEFAULT_CHANGE_PENALTY,
    GLOBAL_ASSIGNMENT_COUNT_PRIORITY,
    SCORE_WEIGHT_MARGIN,
    SCORE_WEIGHT_REPOSITION,
)


@dataclasses.dataclass(frozen=True)
class SolverParameters:
    """Per-run solver parameters; defaults reproduce the engine constants."""

    # Target number of orders per geographic cluster.
    cluster_target_size: int = CLUSTER_TARGET_SIZE
    # Greedy warm-start score weights (margin vs repositioning cost).
    score_weight_margin: float = SCORE_WEIGHT_MARGIN
    score_weight_reposition: float = SCORE_WEIGHT_REPOSITION
    # Wall-clock budget per cluster routing solve.
    cluster_solve_time_limit_s: int = CLUSTER_SOLVE_TIME_LIMIT_S
    # Optional second-pass LNS budget per qualifying high-value cluster.
    lns_time_limit_s: int = (
        CLUSTER_LNS_TIME_LIMIT_S if CLUSTER_LNS_ENABLED else 0
    )
    # Score penalty applied per assignment changed after the freeze window.
    rolling_change_penalty: int = DEFAULT_CHANGE_PENALTY
    # Count-vs-margin tradeoff of the global assignment objective
    # (1.0 = count-first, 0.0 = pure score maximization); profiles set it
    # via allocationPolicy.countPriority.
    assignment_count_priority: float = GLOBAL_ASSIGNMENT_COUNT_PRIORITY

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
