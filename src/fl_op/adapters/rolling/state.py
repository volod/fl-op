"""Plain state object passed from rolling compile/solve to normalization."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from fl_op.canonical.plan import Assignment, CorrectiveAction, MaterialReservation
from fl_op.core.constants import DEFAULT_CHANGE_PENALTY
from fl_op.solver.chain import SolverChainResult


@dataclass(frozen=True)
class RollingSolveResult:
    """Compiled-and-solved rolling state passed to normalize()."""

    chain_result: Optional[SolverChainResult]
    frozen_assignments: list[Assignment]
    carried_forward: list[Assignment]
    previous_by_task: dict[str, Assignment]
    now: datetime
    change_penalty: int = DEFAULT_CHANGE_PENALTY
    corrective_actions: list[CorrectiveAction] = field(default_factory=list)
    # Previous-revision reservations of frozen/carried tasks, re-published so
    # every revision's reservation list is self-contained.
    carried_reservations: list[MaterialReservation] = field(default_factory=list)
