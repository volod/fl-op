"""Canonical Plan normalization for rolling-dispatch results."""

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fl_op.adapters.base import (
    build_solver_attribution,
    dispatch_to_assignment,
    infeasible_to_unassigned,
    link_reservation_refs,
    reservation_to_canonical,
)
from fl_op.adapters.ortools_periodic import _ortools_version
from fl_op.adapters.rolling.state import RollingSolveResult
from fl_op.canonical.common import RiskSummary
from fl_op.canonical.enums import PlanningMode, PlanStatus
from fl_op.canonical.plan import Assignment, Plan
from fl_op.core.constants import (
    ADAPTER_ORTOOLS_ROLLING_ID,
    ADAPTER_VERSION,
)

if TYPE_CHECKING:
    from fl_op.canonical.snapshot import PlanningSnapshot


def normalize_rolling_result(
    raw_result: RollingSolveResult,
    snapshot: "PlanningSnapshot",
    profile: Any = None,
) -> Plan:
    """Convert a rolling solve result into an immutable canonical Plan revision."""
    now = datetime.now(tz=timezone.utc)
    new_assignments, unassigned, kpis = _normalize_chain_delta(raw_result)
    chain = raw_result.chain_result
    new_reservations = [
        reservation_to_canonical(r)
        for r in (chain.material_reservations if chain is not None else [])
    ]
    new_assignments = link_reservation_refs(new_assignments, new_reservations)
    reservations = [*raw_result.carried_reservations, *new_reservations]
    assignments = [
        *raw_result.frozen_assignments,
        *raw_result.carried_forward,
        *new_assignments,
    ]
    score = _build_score(raw_result, new_assignments, unassigned, kpis)
    from fl_op.planning.drone_kpis import (
        DRONE_KPI_SCORE_KEY,
        build_drone_logistics_kpis,
    )

    drone_kpis = build_drone_logistics_kpis(
        snapshot, assignments, unassigned, score, profile
    )
    if drone_kpis:
        score[DRONE_KPI_SCORE_KEY] = drone_kpis

    return Plan(
        plan_id=f"plan-rolling-{snapshot.snapshot_hash[:8]}",
        revision_id=f"rev-{uuid.uuid4().hex[:8]}",
        parent_revision_id=None,
        origin_plan_id=f"plan-rolling-{snapshot.snapshot_hash[:8]}",
        planning_mode=PlanningMode.ROLLING,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        version_dimensions=snapshot.version_dimensions,
        adapter_id=ADAPTER_ORTOOLS_ROLLING_ID,
        adapter_version=ADAPTER_VERSION,
        solver_version=_ortools_version(),
        generated_at=now,
        effective_from=snapshot.effective_at,
        effective_to=snapshot.planning_horizon.to,
        status=PlanStatus.DRAFT,
        assignments=assignments,
        unassigned_tasks=unassigned,
        material_reservations=reservations,
        corrective_actions=raw_result.corrective_actions,
        score=score,
        quality_summary=snapshot.quality_summary,
        risk_summary=RiskSummary(n_contract_deadlines_at_risk=len(unassigned)),
        source_watermarks=snapshot.source_watermarks,
        lineage_ref=snapshot.lineage_ref,
    )


def _normalize_chain_delta(
    raw_result: RollingSolveResult,
) -> tuple[list[Assignment], list, dict[str, Any]]:
    chain = raw_result.chain_result
    new_assignments: list[Assignment] = []
    unassigned = []
    kpis: dict[str, Any] = {}

    if chain is None:
        return new_assignments, unassigned, kpis

    kpis = chain.kpis
    for dispatch_package in chain.dispatch:
        assignment = dispatch_to_assignment(dispatch_package)
        previous = raw_result.previous_by_task.get(assignment.task_id)
        if previous is not None and previous.bundle_id != assignment.bundle_id:
            assignment = assignment.model_copy(
                update={
                    "previous_bundle_id": previous.bundle_id,
                    "previous_start_time": previous.planned_start,
                    "change_penalty": raw_result.change_penalty,
                }
            )
        new_assignments.append(assignment)
    unassigned = [infeasible_to_unassigned(inf) for inf in chain.infeasible]
    return new_assignments, unassigned, kpis


def _build_score(
    raw_result: RollingSolveResult,
    new_assignments: list[Assignment],
    unassigned: list,
    kpis: dict[str, Any],
) -> dict[str, Any]:
    from fl_op.canonical.enums import CorrectiveActionType

    from fl_op.solver.solve_telemetry import summarize_cluster_telemetry

    chain = raw_result.chain_result
    n_changed = sum(1 for a in new_assignments if a.change_penalty > 0)
    n_new_tasks = sum(
        1 for a in new_assignments if a.task_id not in raw_result.previous_by_task
    )
    by_action = {
        action_type: sum(
            1 for ca in raw_result.corrective_actions if ca.action == action_type
        )
        for action_type in CorrectiveActionType
    }
    return {
        "optimization_objective": kpis.get("optimization_objective", "cost"),
        "n_frozen": len(raw_result.frozen_assignments),
        "n_carried_forward": len(raw_result.carried_forward),
        "n_replanned": len(new_assignments),
        "n_new_tasks": n_new_tasks,
        "n_changed_after_freeze": n_changed,
        "n_repaired_after_asset_loss": by_action[
            CorrectiveActionType.REASSIGNED_AFTER_ASSET_LOSS
        ],
        "n_service_withdrawn": by_action[CorrectiveActionType.SERVICE_WITHDRAWN],
        "n_service_escalated": by_action[CorrectiveActionType.SERVICE_ESCALATED],
        "plan_instability_penalty": n_changed * raw_result.change_penalty,
        "total_estimated_margin_eur": kpis.get("total_estimated_margin_eur", 0.0),
        "greedy_baseline_margin_eur": kpis.get("greedy_baseline_margin_eur", 0.0),
        "solver_improvement_eur": kpis.get("solver_improvement_eur", 0.0),
        "total_completion_time_s": kpis.get("total_completion_time_s", 0.0),
        "avg_completion_time_s": kpis.get("avg_completion_time_s", 0.0),
        "p95_completion_time_s": kpis.get("p95_completion_time_s", 0.0),
        "max_completion_time_s": kpis.get("max_completion_time_s", 0.0),
        "n_tasks_with_deadlines": kpis.get("n_tasks_with_deadlines", 0),
        "n_on_time": kpis.get("n_on_time", 0),
        "on_time_rate_pct": kpis.get("on_time_rate_pct", 0.0),
        "n_late": kpis.get("n_late", 0),
        "total_fuel_l": kpis.get("total_fuel_l", 0.0),
        "total_fuel_cost_eur": kpis.get("total_fuel_cost_eur", 0.0),
        "total_energy_cost_eur": kpis.get("total_energy_cost_eur", 0.0),
        "total_energy_quantity_by_type": kpis.get(
            "total_energy_quantity_by_type", {}
        ),
        "total_energy_quantity_by_unit": kpis.get(
            "total_energy_quantity_by_unit", {}
        ),
        "total_fertilizer_kg": kpis.get("total_fertilizer_kg", 0.0),
        "total_material_cost_eur": kpis.get("total_material_cost_eur", 0.0),
        "total_distance_km": kpis.get("total_distance_km", 0.0),
        "total_labor_cost_eur": kpis.get("total_labor_cost_eur", 0.0),
        "total_machine_wear_cost_eur": kpis.get("total_machine_wear_cost_eur", 0.0),
        "total_toll_cost_eur": kpis.get("total_toll_cost_eur", 0.0),
        "n_unassigned": len(unassigned),
        "n_clusters": chain.n_clusters if chain is not None else 0,
        "n_greedy_warm_start_assignments": (
            len(chain.greedy_assignment) if chain is not None else 0
        ),
        "solve_telemetry": summarize_cluster_telemetry(
            chain.cluster_telemetry if chain is not None else []
        ),
        **_solver_attribution_score(chain),
    }


def _solver_attribution_score(chain: Any) -> dict[str, Any]:
    if chain is None:
        return {}
    assignment_attr, unassigned_attr = build_solver_attribution(
        chain.dispatch, chain.infeasible, chain.cluster_telemetry
    )
    score: dict[str, Any] = {}
    if assignment_attr:
        score["assignment_attribution"] = assignment_attr
    if unassigned_attr:
        score["unassigned_attribution"] = unassigned_attr
    return score
