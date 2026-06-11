"""Incremental compile/re-solve logic for rolling dispatch."""

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

from fl_op.canonical.plan import Assignment, Plan
from fl_op.core.constants import FREEZE_WINDOW_MINUTES
from fl_op.solver.chain import run_solver_chain

from fl_op.adapters.rolling.corrective import (
    carried_asset_loss_actions,
    escalated_service_tasks,
    release_lost_asset_assignments,
    withdrawn_service_actions,
)
from fl_op.adapters.rolling.state import RollingSolveResult

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot

logger = logging.getLogger(__name__)

_STARTED_STATUS = "started"


def frozen_task_ids(
    snapshot: "PlanningSnapshot",
    previous_by_task: dict[str, Assignment],
    now: datetime,
) -> set[str]:
    """Return task ids protected by started status or the freeze window."""
    cutoff = now + timedelta(minutes=FREEZE_WINDOW_MINUTES)
    frozen: set[str] = set()
    for task in snapshot.tasks:
        if task.status == _STARTED_STATUS:
            frozen.add(task.task_id)
    for task_id, prev in previous_by_task.items():
        if prev.planned_start <= cutoff:
            frozen.add(task_id)
    return frozen


def compile_rolling_state(
    snapshot: "PlanningSnapshot",
    config: dict[str, Any],
) -> RollingSolveResult:
    """Freeze/carry forward unaffected work and re-solve the remaining tasks.

    To minimize avoidable disruption, only tasks actually affected
    by events since the last plan are re-optimized:
      - started or freeze-window tasks are frozen (preserved verbatim), unless
        their assignment lost an asset mid-execution: then the task is
        released and re-solved with a corrective-action record;
      - non-frozen prior assignments whose task and assets still exist are
        carried forward unchanged, unless their service task was escalated;
      - new tasks and assignments whose asset disappeared are re-solved.

    Corrective actions (asset-loss repairs, withdrawn service prognoses,
    escalations) are detected here and recorded on the revision.
    On a baseline build with no previous plan, every task is re-solved.
    """
    now = config.get("now") or snapshot.effective_at
    previous_plan: Optional[Plan] = config.get("previous_plan")
    previous_service_reasons: dict[str, str] = config.get("previous_service_reasons") or {}
    previous_assignments = list(previous_plan.assignments) if previous_plan else []
    previous_by_task: dict[str, Assignment] = {
        a.task_id: a for a in previous_assignments
    }

    current_task_ids = {t.task_id for t in snapshot.tasks}
    available_asset_ids = {a.asset_id for a in snapshot.assets}

    frozen_ids = frozen_task_ids(snapshot, previous_by_task, now)
    frozen_ids, corrective_actions = release_lost_asset_assignments(
        frozen_ids, previous_by_task, available_asset_ids, current_task_ids
    )
    corrective_actions.extend(
        carried_asset_loss_actions(
            previous_assignments, frozen_ids, current_task_ids, available_asset_ids
        )
    )
    force_resolve_ids, escalation_actions = escalated_service_tasks(
        snapshot, previous_service_reasons, previous_by_task
    )
    corrective_actions.extend(escalation_actions)
    corrective_actions.extend(
        withdrawn_service_actions(
            previous_plan, current_task_ids, snapshot, previous_service_reasons
        )
    )

    frozen_assignments = [
        previous_by_task[tid].model_copy(update={"is_frozen": True})
        for tid in frozen_ids
        if tid in previous_by_task
    ]
    carried_forward = _carried_forward_assignments(
        previous_assignments,
        frozen_ids,
        current_task_ids,
        available_asset_ids,
        force_resolve_ids,
    )

    preserved = frozen_ids | {a.task_id for a in carried_forward}
    tasks_to_resolve = {t.task_id for t in snapshot.tasks if t.task_id not in preserved}
    from fl_op.solver.inputs import build_solver_inputs

    chain_result = _resolve_tasks(
        build_solver_inputs(snapshot),
        tasks_to_resolve,
        [*frozen_assignments, *carried_forward],
        enforcement=config.get("enforcement"),
    )

    logger.info(
        "Rolling replan: %d frozen, %d carried forward, %d re-solved, %d corrective actions",
        len(frozen_assignments),
        len(carried_forward),
        len(tasks_to_resolve),
        len(corrective_actions),
    )
    return RollingSolveResult(
        chain_result=chain_result,
        frozen_assignments=frozen_assignments,
        carried_forward=carried_forward,
        previous_by_task=previous_by_task,
        now=now,
        corrective_actions=corrective_actions,
    )


def _carried_forward_assignments(
    previous_assignments: list[Assignment],
    frozen_ids: set[str],
    current_task_ids: set[str],
    available_asset_ids: set[str],
    force_resolve_ids: set[str],
) -> list[Assignment]:
    carried_forward: list[Assignment] = []
    for assignment in previous_assignments:
        task_id = assignment.task_id
        if task_id in frozen_ids or task_id not in current_task_ids:
            continue
        if task_id in force_resolve_ids:
            continue
        if not set(assignment.asset_ids).issubset(available_asset_ids):
            continue
        carried_forward.append(assignment.model_copy(update={"is_frozen": False}))
    return carried_forward


def _resolve_tasks(
    solver_rows: dict[str, Any],
    tasks_to_resolve: set[str],
    held_assignments: list[Assignment],
    enforcement: Any = None,
):
    if not tasks_to_resolve:
        return None

    from fl_op.solver.inputs import (
        SECTION_OPERATORS,
        SECTION_PRIME_MOVERS,
        SECTION_RELATED,
        SECTION_TASKS,
    )

    # Held assets are classified by canonical solver-row section membership
    # (never by domain-specific id prefixes), so any domain pack's rolling
    # re-solve treats its prime movers and related equipment correctly.
    prime_mover_ids = {r.asset_id for r in solver_rows.get(SECTION_PRIME_MOVERS, [])}
    related_ids = {r.asset_id for r in solver_rows.get(SECTION_RELATED, [])}

    # Held prime movers stay available to the incremental re-solve: their
    # frozen/carried assignment windows travel along as busy time windows
    # (vehicle break intervals in the routing model), so a held vehicle is
    # reused only in a real non-overlapping gap, never double-booked. Held
    # related equipment and operators are bound to their assignment for its
    # whole duration and remain excluded.
    held_vehicle_windows = _held_vehicle_windows(held_assignments, prime_mover_ids)
    held_implements = {
        aid
        for assignment in held_assignments
        for aid in assignment.asset_ids
        if aid in related_ids
    }
    held_operators = {op for assignment in held_assignments for op in assignment.operator_ids}

    payload = dict(solver_rows)
    payload[SECTION_TASKS] = [
        o for o in payload.get(SECTION_TASKS, []) if o.task_id in tasks_to_resolve
    ]
    payload[SECTION_RELATED] = [
        im
        for im in payload.get(SECTION_RELATED, [])
        if im.asset_id not in held_implements
    ]
    payload[SECTION_OPERATORS] = [
        op
        for op in payload.get(SECTION_OPERATORS, [])
        if op.asset_id not in held_operators
    ]
    return run_solver_chain(
        payload, enforcement=enforcement, held_windows=held_vehicle_windows
    )


def _held_vehicle_windows(
    held_assignments: list[Assignment],
    prime_mover_ids: set[str],
) -> dict[str, list[tuple[int, int]]]:
    """Map each held prime mover to its busy [start, end) epoch-second intervals."""
    windows: dict[str, list[tuple[int, int]]] = {}
    for assignment in held_assignments:
        start = int(assignment.planned_start.timestamp())
        end = int(assignment.planned_finish.timestamp())
        for aid in assignment.asset_ids:
            if aid in prime_mover_ids:
                windows.setdefault(aid, []).append((start, end))
    return windows
