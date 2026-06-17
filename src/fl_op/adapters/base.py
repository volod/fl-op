"""Shared adapter helpers: result normalization and profile validation."""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from fl_op.adapters.spi import AdapterManifest, ValidationReport
from fl_op.canonical.bundle import compute_bundle_id
from fl_op.canonical.enums import ReasonCode, ReservationStatus
from fl_op.canonical.plan import Assignment, MaterialReservation, UnassignedTask
from fl_op.core.constants import MAPPING_VERSION

if TYPE_CHECKING:
    from fl_op.contracts.profile import OptimizationProfile

logger = logging.getLogger(__name__)


def _parse_ts(value: str) -> datetime:
    if not value:
        return datetime.now(tz=timezone.utc)
    return datetime.fromisoformat(value)


def dispatch_to_assignment(dp: dict[str, Any]) -> Assignment:
    """Convert a solver dispatch package into a canonical Assignment."""
    vehicle_id = dp.get("prime_asset_id", "")
    implement_id = dp.get("related_asset_id", "")
    operator_id = dp.get("operator_asset_id", "")
    bundle_id = compute_bundle_id([vehicle_id, implement_id], [], MAPPING_VERSION)
    return Assignment(
        assignment_id=dp.get("dispatch_id", f"assign-{dp.get('task_id', '')}"),
        task_id=dp.get("task_id", ""),
        bundle_id=bundle_id,
        asset_ids=[a for a in (vehicle_id, implement_id) if a],
        operator_ids=[operator_id] if operator_id else [],
        planned_start=_parse_ts(dp.get("scheduled_start", "")),
        planned_finish=_parse_ts(dp.get("scheduled_end", "")),
        expected_margin_eur=float(dp.get("estimated_margin_eur", 0.0)),
        explanation_ref=f"explain://assignment/{dp.get('task_id', '')}",
    )


def _parse_optional_ts(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def reservation_to_canonical(raw: dict[str, Any]) -> MaterialReservation:
    """Convert a chain material-reservation record into the canonical model."""
    return MaterialReservation(
        reservation_id=raw.get("reservation_id", ""),
        task_id=raw.get("task_id", ""),
        material_type=raw.get("material_type", ""),
        inventory_location_ref=raw.get("inventory_location_ref", ""),
        quantity=float(raw.get("quantity", 0.0)),
        canonical_unit=raw.get("canonical_unit", ""),
        reserved_from=_parse_optional_ts(raw.get("reserved_from")),
        reserved_to=_parse_optional_ts(raw.get("reserved_to")),
        status=ReservationStatus(
            raw.get("status", ReservationStatus.PROVISIONAL.value)
        ),
    )


def link_reservation_refs(
    assignments: list[Assignment],
    reservations: list[MaterialReservation],
) -> list[Assignment]:
    """Stamp each assignment with its task's material-reservation ids.

    Released reservations belong to unserved tasks and therefore never match
    an assignment; only the confirmed charges end up referenced.
    """
    refs_by_task: dict[str, list[str]] = {}
    for reservation in reservations:
        refs_by_task.setdefault(reservation.task_id, []).append(
            reservation.reservation_id
        )
    return [
        a.model_copy(update={"material_reservation_refs": refs_by_task[a.task_id]})
        if a.task_id in refs_by_task
        else a
        for a in assignments
    ]


def infeasible_to_unassigned(inf: dict[str, Any]) -> UnassignedTask:
    """Convert a solver infeasibility record into a canonical UnassignedTask."""
    reason_code = ReasonCode(inf.get("reason_code", ReasonCode.UNKNOWN.value))
    return UnassignedTask(
        task_id=inf.get("task_id", ""),
        reason_code=reason_code,
        details={
            "detail": inf.get("detail", ""),
            "cluster_id": inf.get("cluster_id", ""),
        },
        explanation_ref=f"explain://unassigned/{inf.get('task_id', '')}",
    )


def build_solver_attribution(
    dispatch: list[dict[str, Any]],
    infeasible: list[dict[str, Any]],
    cluster_telemetry: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build compact per-task attribution from solver outputs.

    OR-Tools routing does not expose LP duals for the CP routing model, so the
    actionable attribution comes from the solver's conflict surface: cluster
    status, routing objective, LNS delta, time-limit state, tasks dropped in the
    same cluster, and the primal resource-conflict signal naming the routing
    dimension running tightest behind those drops (time horizon, load capacity,
    or fleet; see solver/cluster/conflict.py). Revision-diff reporting consumes
    this map to explain why a changed assignment moved without scraping logs.
    """
    telemetry_by_cluster = {
        str(record.get("cluster_id", "")): record for record in cluster_telemetry
    }
    unserved_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for record in infeasible:
        unserved_by_cluster.setdefault(str(record.get("cluster_id", "")), []).append(
            {
                "task_id": record.get("task_id", ""),
                "reason_code": record.get("reason_code", ""),
                "detail": record.get("detail", ""),
            }
        )

    assignments: dict[str, dict[str, Any]] = {}
    for record in dispatch:
        task_id = str(record.get("task_id", ""))
        cluster_id = str(record.get("cluster_id", ""))
        telemetry = telemetry_by_cluster.get(cluster_id, {})
        resource_conflict = telemetry.get("resource_conflict") or {}
        conflicts = [
            item
            for item in unserved_by_cluster.get(cluster_id, [])
            if item.get("task_id") != task_id
        ][:5]
        assignments[task_id] = {
            "source": "ortools-routing",
            "cluster_id": cluster_id,
            "solver_status": telemetry.get("status", ""),
            "routing_status": telemetry.get("routing_status", ""),
            "objective_value": telemetry.get("objective_value"),
            "first_solution_objective": telemetry.get("first_solution_objective"),
            "lns_objective_delta": telemetry.get("lns_objective_delta", 0),
            "hit_time_limit": bool(telemetry.get("hit_time_limit", False)),
            "n_cluster_tasks": telemetry.get("n_tasks", 0),
            "n_unserved_in_cluster": telemetry.get("n_unserved", 0),
            "estimated_margin_eur": record.get("estimated_margin_eur", 0.0),
            "binding_resource": resource_conflict.get("binding_resource"),
            "resource_conflict": resource_conflict,
            "conflicts": conflicts,
        }

    unassigned: dict[str, dict[str, Any]] = {}
    for record in infeasible:
        task_id = str(record.get("task_id", ""))
        cluster_id = str(record.get("cluster_id", ""))
        telemetry = telemetry_by_cluster.get(cluster_id, {})
        resource_conflict = telemetry.get("resource_conflict") or {}
        unassigned[task_id] = {
            "source": "ortools-routing",
            "cluster_id": cluster_id,
            "solver_status": telemetry.get("status", ""),
            "routing_status": telemetry.get("routing_status", ""),
            "objective_value": telemetry.get("objective_value"),
            "hit_time_limit": bool(telemetry.get("hit_time_limit", False)),
            "reason_code": record.get("reason_code", ""),
            "detail": record.get("detail", ""),
            "binding_resource": resource_conflict.get("binding_resource"),
            "resource_conflict": resource_conflict,
        }
    return assignments, unassigned


def validate_profile_against_features(
    profile: "OptimizationProfile",
    supported_constraints: set[str],
) -> ValidationReport:
    """A profile may only run if every enforced constraint is supported."""
    unsupported = [
        c.id for c in profile.constraints
        if c.enforced and c.id not in supported_constraints
    ]
    return ValidationReport(
        ok=not unsupported,
        unsupported_constraints=unsupported,
        messages=(
            [] if not unsupported
            else [f"adapter does not support enforced constraints: {unsupported}"]
        ),
    )
