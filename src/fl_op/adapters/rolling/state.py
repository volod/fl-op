"""Plain state object passed from rolling compile/solve to normalization."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fl_op.canonical.plan import Assignment
from fl_op.solver.chain import SolverChainResult


@dataclass(frozen=True)
class RollingSolveResult:
    """Compiled-and-solved rolling state passed to normalize()."""

    chain_result: Optional[SolverChainResult]
    frozen_assignments: list[Assignment]
    carried_forward: list[Assignment]
    previous_by_task: dict[str, Assignment]
    now: datetime
