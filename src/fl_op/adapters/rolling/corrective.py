"""Corrective-rescheduling detection for rolling revisions.

Pure helpers the rolling compiler uses to make plans survive being wrong:

- release frozen or carried assignments whose assets disappeared mid-plan so
  the affected tasks are re-solved instead of staying bound to a dead bundle;
- detect derived service tasks withdrawn because newer readings contradict the
  prognosis (false positive), recording why they were derived and revoked;
- detect service tasks escalated because the asset degraded faster than the
  prognosis (false negative), forcing an immediate re-solve of their
  assignments.

Every detection yields a canonical CorrectiveAction recorded on the revision.
"""

from typing import TYPE_CHECKING, Any, Optional

from fl_op.canonical.enums import CorrectiveActionType
from fl_op.canonical.plan import Assignment, CorrectiveAction, Plan
from fl_op.core.constants import METRIC_BATTERY_LEVEL, METRIC_HEALTH_STATUS
from fl_op.snapshot.monitoring import ESCALATED_REASON_PREFIX, latest_observations

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot

# Identifier prefixes stamped by the monitoring policy on derived tasks.
SERVICE_TASK_PREFIX = "service-"
_MONITORING_SOURCE_PREFIX = "monitoring:"


def is_service_task_id(task_id: str) -> bool:
    return task_id.startswith(SERVICE_TASK_PREFIX)


def _service_asset_id(task_id: str) -> str:
    return task_id[len(SERVICE_TASK_PREFIX):]


def _current_readings(snapshot: "PlanningSnapshot", asset_id: str) -> dict[str, Any]:
    """Latest battery/health evidence for one asset, for action records."""
    latest = latest_observations(snapshot.observations)
    evidence: dict[str, Any] = {}
    battery = latest.get((asset_id, METRIC_BATTERY_LEVEL))
    if battery is not None and battery.value is not None:
        evidence["battery_level_pct"] = battery.value
    health = latest.get((asset_id, METRIC_HEALTH_STATUS))
    if health is not None and health.state_value:
        evidence["health_status"] = health.state_value
    return evidence


def release_lost_asset_assignments(
    frozen_ids: set[str],
    previous_by_task: dict[str, Assignment],
    available_asset_ids: set[str],
    current_task_ids: set[str],
) -> tuple[set[str], list[CorrectiveAction]]:
    """Release frozen assignments whose assets disappeared mid-execution.

    Returns the frozen ids that remain valid plus one corrective action per
    released task; released tasks fall into the re-solve set.
    """
    kept = set(frozen_ids)
    actions: list[CorrectiveAction] = []
    for task_id in sorted(frozen_ids):
        prev = previous_by_task.get(task_id)
        if prev is None or task_id not in current_task_ids:
            continue
        lost = sorted(set(prev.asset_ids) - available_asset_ids)
        if not lost:
            continue
        kept.discard(task_id)
        actions.append(
            CorrectiveAction(
                action=CorrectiveActionType.REASSIGNED_AFTER_ASSET_LOSS,
                task_id=task_id,
                detail=f"frozen assignment lost assets {lost}; released for re-solve",
                evidence={"lost_assets": lost, "bundle_id": prev.bundle_id},
            )
        )
    return kept, actions


def carried_asset_loss_actions(
    previous_assignments: list[Assignment],
    frozen_ids: set[str],
    current_task_ids: set[str],
    available_asset_ids: set[str],
) -> list[CorrectiveAction]:
    """Record asset loss for non-frozen prior assignments (re-solved anyway)."""
    actions: list[CorrectiveAction] = []
    for prev in previous_assignments:
        if prev.task_id in frozen_ids or prev.task_id not in current_task_ids:
            continue
        lost = sorted(set(prev.asset_ids) - available_asset_ids)
        if not lost:
            continue
        actions.append(
            CorrectiveAction(
                action=CorrectiveActionType.REASSIGNED_AFTER_ASSET_LOSS,
                task_id=prev.task_id,
                detail=f"assignment lost assets {lost}; task re-solved",
                evidence={"lost_assets": lost, "bundle_id": prev.bundle_id},
            )
        )
    return actions


def withdrawn_service_actions(
    previous_plan: Optional[Plan],
    current_task_ids: set[str],
    snapshot: "PlanningSnapshot",
    previous_service_reasons: dict[str, str],
) -> list[CorrectiveAction]:
    """Service tasks no longer derived: the prognosis was a false positive.

    Records why each task was derived (the previous revision's monitoring
    reasons) and the current readings that contradict it.
    """
    if previous_plan is None:
        return []
    previous_service_ids = {
        a.task_id for a in previous_plan.assignments if is_service_task_id(a.task_id)
    } | {
        u.task_id for u in previous_plan.unassigned_tasks if is_service_task_id(u.task_id)
    }
    actions: list[CorrectiveAction] = []
    for task_id in sorted(previous_service_ids - current_task_ids):
        asset_id = _service_asset_id(task_id)
        actions.append(
            CorrectiveAction(
                action=CorrectiveActionType.SERVICE_WITHDRAWN,
                task_id=task_id,
                detail=previous_service_reasons.get(
                    task_id, "derived in a previous revision"
                ),
                evidence=_current_readings(snapshot, asset_id),
            )
        )
    return actions


def escalated_service_tasks(
    snapshot: "PlanningSnapshot",
    previous_service_reasons: dict[str, str],
    previous_by_task: dict[str, Assignment],
) -> tuple[set[str], list[CorrectiveAction]]:
    """Escalated service tasks plus actions for newly escalated ones.

    Returns the task ids whose existing assignments must be re-solved (their
    urgency changed) and one corrective action per task that was previously
    derived without escalation: the prognosis was a false negative.
    """
    force_resolve: set[str] = set()
    actions: list[CorrectiveAction] = []
    for task in snapshot.tasks:
        if not task.source_ref.startswith(_MONITORING_SOURCE_PREFIX):
            continue
        if ESCALATED_REASON_PREFIX not in task.source_ref:
            continue
        if task.task_id in previous_by_task:
            force_resolve.add(task.task_id)
        previous_reason = previous_service_reasons.get(task.task_id)
        if previous_reason is not None and ESCALATED_REASON_PREFIX not in previous_reason:
            asset_id = _service_asset_id(task.task_id)
            actions.append(
                CorrectiveAction(
                    action=CorrectiveActionType.SERVICE_ESCALATED,
                    task_id=task.task_id,
                    detail=f"was: {previous_reason}; now: {task.source_ref}",
                    evidence=_current_readings(snapshot, asset_id),
                )
            )
    return force_resolve, actions
