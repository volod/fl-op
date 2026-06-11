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

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)
