"""Profile-constraint enforcement inside the solver chain.

Enforces the declared profile constraints that the chain previously only
validated for adapter support:

- ``respect-weather-window``: a weather-sensitive task is infeasible when no
  forecast window at its nearest forecast location satisfies the limits for
  the dimensions its operation type is sensitive to.
- ``operator-qualified``: tasks whose operation is not certified by the
  cluster's allocated operator are infeasible.
- ``required-material-available``: cumulative consumable demand per depot
  (per-operation rate x task area) may not exceed the depot's inventory;
  excess tasks are infeasible, higher-penalty tasks claim material first.

Every exclusion is an explicit InfeasibleOrder record with a canonical reason
code; nothing is dropped silently.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from fl_op.canonical.enums import ReasonCode, ReservationStatus
from fl_op.core.constants import (
    MATERIAL_INVENTORY_CANONICAL_UNIT,
    OPERATOR_SHARING_SEQUENTIAL,
    ROUTING_HORIZON_S,
)
from fl_op.solver.task_relations import parse_time_windows
from fl_op.solver.travel_time import operation_set
from fl_op.solver.types import ClusterSpec, InfeasibleOrder

if TYPE_CHECKING:
    from fl_op.contracts.profile import (
        MaterialDemandSpec,
        OptimizationProfile,
        WeatherPolicySpec,
    )

logger = logging.getLogger(__name__)

# Weather dimensions a profile's sensitivity lists may reference.
WEATHER_DIM_WIND = "wind"
WEATHER_DIM_RAIN = "rain"
WEATHER_DIM_SOIL_MOISTURE = "soil-moisture"

_CONSTRAINT_WEATHER = "respect-weather-window"
_CONSTRAINT_OPERATOR = "operator-qualified"
_CONSTRAINT_MATERIAL = "required-material-available"

# task_id -> closed [start, end] epoch-second intervals during which the task
# may not execute (non-compliant forecast windows). Consumed by the routing
# model as occupancy blocks, exactly like location restriction windows.
BlockedWindows = dict[str, list[tuple[int, int]]]


@dataclass(frozen=True)
class EnforcementPolicy:
    """Which profile constraints the chain enforces, with their parameters."""

    weather: Optional["WeatherPolicySpec"] = None
    operator_qualification: bool = False
    material_demand: dict[str, "MaterialDemandSpec"] = field(default_factory=dict)

    @classmethod
    def from_profile(cls, profile: "OptimizationProfile") -> "EnforcementPolicy":
        enforced = set(profile.enforced_constraints())
        return cls(
            weather=profile.weatherPolicy if _CONSTRAINT_WEATHER in enforced else None,
            operator_qualification=_CONSTRAINT_OPERATOR in enforced,
            material_demand=(
                dict(profile.materialDemand) if _CONSTRAINT_MATERIAL in enforced else {}
            ),
        )


def _infeasible(task_id: str, cluster_id: str, reason: ReasonCode, detail: str) -> InfeasibleOrder:
    return {
        "task_id": task_id,
        "cluster_id": cluster_id,
        "reason_code": reason.value,
        "detail": detail,
    }


def ops_set(raw: Any) -> set[str]:
    """Parse an operations list that may arrive as a stringified Python list."""
    return operation_set(raw)


# -- respect-weather-window ----------------------------------------------------


def _window_ok(forecast: Any, dims: list[str], weather: "WeatherPolicySpec") -> bool:
    if WEATHER_DIM_WIND in dims:
        if forecast.wind_speed is not None and forecast.wind_speed > weather.maxWindMs:
            return False
    if WEATHER_DIM_RAIN in dims:
        if (
            forecast.precipitation_rate is not None
            and forecast.precipitation_rate > weather.maxRainMmPerH
        ):
            return False
    if WEATHER_DIM_SOIL_MOISTURE in dims:
        if (
            forecast.soil_moisture is not None
            and forecast.soil_moisture > weather.maxSoilMoisturePct
        ):
            return False
    return True


def _forecast_epoch_interval(forecast: Any) -> Optional[tuple[int, int]]:
    """Closed epoch-second interval one forecast window covers; None when the
    window carries no parseable start. A missing end means the condition holds
    until further notice, blocked out to the routing horizon."""
    try:
        start = int(datetime.fromisoformat(str(forecast.valid_from)).timestamp())
    except (ValueError, TypeError):
        return None
    try:
        end = int(datetime.fromisoformat(str(forecast.valid_to)).timestamp())
    except (ValueError, TypeError):
        end = start + ROUTING_HORIZON_S
    if end < start:
        return None
    return start, end


def _coverage_horizon(order: Any, now_epoch: int) -> tuple[int, int]:
    """Closed [now, deadline] epoch-second window a task must be weather-covered
    across. Falls back to one routing horizon past ``now`` when the order
    carries no parseable deadline."""
    raw = getattr(order, "deadline", None)
    try:
        end = int(datetime.fromisoformat(str(raw)).timestamp())
    except (ValueError, TypeError):
        end = now_epoch + ROUTING_HORIZON_S
    return now_epoch, max(now_epoch, end)


def apply_weather_filter(
    orders: list[Any],
    sites: list[Any],
    forecasts: list[Any],
    weather: Optional["WeatherPolicySpec"],
    now: Optional[datetime] = None,
) -> tuple[list[Any], list[InfeasibleOrder], BlockedWindows]:
    """Split off weather-sensitive tasks without any compliant forecast window.

    Forecast windows are grouped by location; each task checks the forecast
    location nearest to its work site. Without forecast data (or for
    operations with no declared sensitivity) tasks pass through.

    Additionally returns each kept sensitive task's *non-compliant* forecast
    windows as blocked epoch intervals, so the routing model can schedule
    execution into the compliant windows instead of merely knowing one exists.

    When the policy sets ``requireForecastCoverage`` the filter is conservative:
    any time between ``now`` and the task deadline that is *not* covered by a
    compliant forecast window is blocked (not only the explicitly non-compliant
    windows). A sensitive task with no compliant coverage over that horizon is
    declared infeasible, including the case of missing forecast data entirely.
    """
    if weather is None or not weather.sensitivity or not forecasts:
        return orders, [], {}

    conservative = bool(getattr(weather, "requireForecastCoverage", False))
    now_epoch = int((now or datetime.now(tz=timezone.utc)).timestamp())

    by_location: dict[tuple[float, float], list[Any]] = {}
    for forecast in forecasts:
        by_location.setdefault((forecast.lat, forecast.lon), []).append(forecast)
    site_coords = {s.location_id: (s.lat, s.lon) for s in sites}

    def nearest_windows(location_ref: str) -> list[Any]:
        coords = site_coords.get(location_ref)
        if coords is None:
            return []
        key = min(
            by_location,
            key=lambda c: (c[0] - coords[0]) ** 2 + (c[1] - coords[1]) ** 2,
        )
        return by_location[key]

    kept: list[Any] = []
    infeasible: list[InfeasibleOrder] = []
    blocked: BlockedWindows = {}
    for order in orders:
        dims = weather.sensitivity.get(order.operation_type)
        if not dims:
            kept.append(order)
            continue
        windows = nearest_windows(order.location_ref)
        if conservative:
            _filter_conservative(
                order, dims, windows, weather, now_epoch, kept, infeasible, blocked
            )
            continue
        if not windows or any(_window_ok(w, dims, weather) for w in windows):
            kept.append(order)
            intervals = [
                interval
                for w in windows
                if not _window_ok(w, dims, weather)
                and (interval := _forecast_epoch_interval(w)) is not None
            ]
            if intervals:
                blocked[order.task_id] = intervals
            continue
        infeasible.append(
            _infeasible(
                order.task_id,
                "",
                ReasonCode.NO_VALID_WEATHER_WINDOW,
                f"no forecast window satisfies {dims} for {order.operation_type}",
            )
        )
    if infeasible:
        logger.info("Weather windows excluded %d tasks", len(infeasible))
    return kept, infeasible, blocked


def _filter_conservative(
    order: Any,
    dims: Any,
    windows: list[Any],
    weather: "WeatherPolicySpec",
    now_epoch: int,
    kept: list[Any],
    infeasible: list[InfeasibleOrder],
    blocked: BlockedWindows,
) -> None:
    """Conservative coverage: block every interval inside [now, deadline] not
    proven safe by a compliant forecast window; drop tasks with no coverage."""
    from fl_op.solver.restrictions import merge_intervals, subtract_intervals

    horizon = _coverage_horizon(order, now_epoch)
    compliant: list[tuple[int, int]] = []
    for window in windows:
        if not _window_ok(window, dims, weather):
            continue
        interval = _forecast_epoch_interval(window)
        if interval is None:
            continue
        start = max(interval[0], horizon[0])
        end = min(interval[1], horizon[1])
        if end >= start:
            compliant.append((start, end))
    compliant = merge_intervals(compliant)
    if not compliant:
        infeasible.append(
            _infeasible(
                order.task_id,
                "",
                ReasonCode.NO_VALID_WEATHER_WINDOW,
                "no compliant forecast covers "
                f"[{horizon[0]}, {horizon[1]}] for {order.operation_type}",
            )
        )
        return
    kept.append(order)
    gaps = subtract_intervals([horizon], compliant)
    if gaps:
        blocked[order.task_id] = gaps


# -- operator-qualified ----------------------------------------------------------


def apply_operator_qualification(
    clusters: list[ClusterSpec],
    order_index: dict[str, Any],
    operators_by_id: dict[str, Any],
    free_capacity: Optional[dict[str, float]] = None,
    now: Optional[datetime] = None,
) -> list[InfeasibleOrder]:
    """Drop cluster tasks no qualified operator can take.

    A task whose operation the cluster's allocated operator is certified for
    stays as-is. A task outside that set is paired with a backup operator
    (certified for the operation, never a cluster's own prime operator,
    preferring the freest calendar) recorded in the cluster's
    ``task_operators`` map; the dispatch then carries the per-task operator.
    Only tasks for which no qualified operator exists anywhere are dropped. A
    backup is claimed by one cluster but may cover several of its tasks, the
    same fidelity as the cluster operator itself.

    Backup sharing is time-aware: a backup operator may serve more than one
    cluster as long as the clusters' demand windows do not overlap. The demand
    window for an operation is the union of the workable time windows of the
    cluster tasks needing that operation, clamped to the routing horizon; a
    task without an explicit window contributes the whole horizon (conservative,
    so it cannot be silently double-booked). An operator is reusable across
    clusters whose demand windows are disjoint, and stays single-use whenever
    windows are unknown -- matching the prior behaviour in that degenerate case.

    With ``OPERATOR_SHARING_SEQUENTIAL`` a scarce backup operator with no free
    (disjoint) candidate may instead be shared across an *overlapping* window;
    the contending clusters are stamped (``shared_backup_operators``) so the pool
    serializes them and the routing blocks each one with the intervals the
    earlier clusters actually committed, keeping the operator single-tasking
    without losing the share.
    """
    free_capacity = free_capacity or {}
    infeasible: list[InfeasibleOrder] = []
    now_dt = now or datetime.now(timezone.utc)
    now_epoch = int(now_dt.timestamp())
    horizon_end = now_epoch + ROUTING_HORIZON_S
    # Cluster prime operators are never offered as backups; tracked once up front.
    main_claimed: set[str] = {
        str(c.get("operator_ref", "")) for c in clusters if c.get("operator_ref")
    }
    # Per backup operator, the merged windows it is already committed to.
    backup_busy: dict[str, list[tuple[int, int]]] = {}
    allow_overlap = OPERATOR_SHARING_SEQUENTIAL
    # Backup operator -> clusters claiming it, and the subset claimed across an
    # overlapping window. Clusters that share an overlap-claimed operator must
    # solve sequentially (see cluster_pool), so they are stamped after the pass.
    operator_claimants: dict[str, list[str]] = {}
    operator_overlap: set[str] = set()
    clusters_by_id = {c["cluster_id"]: c for c in clusters}
    for cluster in clusters:
        if not cluster.get("allocated_prime_related") and not cluster.get("operator_ref"):
            continue
        operator = operators_by_id.get(cluster.get("operator_ref", ""))
        certified = ops_set(operator.certified_operations) if operator is not None else set()
        # First pass: partition tasks and gather per-operation backup demand.
        plan: list[tuple[str, Optional[str], bool]] = []
        needs_backup: dict[str, list[str]] = {}
        for task_id in cluster["task_ids"]:
            order = order_index.get(task_id)
            operation = str(getattr(order, "operation_type", "") or "").upper() or None
            certified_here = order is not None and operation in certified
            plan.append((task_id, operation, certified_here))
            if not certified_here and operation:
                needs_backup.setdefault(operation, []).append(task_id)
        # Claim one backup per operation, honouring time-disjoint sharing.
        cluster_backups: dict[str, str] = {}
        cluster_id = cluster["cluster_id"]
        for operation, task_ids in needs_backup.items():
            demand = _operator_demand_window(
                (order_index.get(t) for t in task_ids), now_epoch, horizon_end
            )
            backup_id, is_overlap = _claim_backup_operator(
                operation,
                operators_by_id,
                main_claimed,
                backup_busy,
                demand,
                free_capacity,
                allow_overlap,
            )
            if backup_id is not None:
                cluster_backups[operation] = backup_id
                claimants = operator_claimants.setdefault(backup_id, [])
                if cluster_id not in claimants:
                    claimants.append(cluster_id)
                if is_overlap:
                    operator_overlap.add(backup_id)
        # Second pass: assign and record infeasible tasks.
        task_operators: dict[str, str] = {}
        kept_ids: list[str] = []
        for task_id, operation, certified_here in plan:
            if certified_here:
                kept_ids.append(task_id)
                continue
            backup_id = cluster_backups.get(operation) if operation else None
            if backup_id is not None:
                task_operators[task_id] = backup_id
                kept_ids.append(task_id)
                continue
            detail = (
                f"operator {cluster.get('operator_ref', '<none>')} not certified "
                f"for {operation or '<unknown>'} and no qualified backup operator "
                "is free"
            )
            infeasible.append(
                _infeasible(
                    task_id, cluster["cluster_id"], ReasonCode.NO_AVAILABLE_OPERATOR, detail
                )
            )
        cluster["task_ids"] = kept_ids
        if task_operators:
            cluster["task_operators"] = task_operators
    # Stamp every cluster contending for an overlap-shared backup operator with
    # that operator id, so the pool serializes the contending clusters and feeds
    # each the operator intervals the earlier ones actually committed.
    for operator_id in operator_overlap:
        for claimant_id in operator_claimants.get(operator_id, []):
            claimant = clusters_by_id.get(claimant_id)
            if claimant is None:
                continue
            shared = claimant.setdefault("shared_backup_operators", [])
            if operator_id not in shared:
                shared.append(operator_id)
    if infeasible:
        logger.info("Operator qualification excluded %d tasks", len(infeasible))
    return infeasible


def _operator_demand_window(
    orders: Any, now_epoch: int, horizon_end: int
) -> list[tuple[int, int]]:
    """Merged epoch windows an operation needs a backup operator for.

    The union of each task's workable windows clamped to ``[now, horizon]``. A
    task without a usable window forces the whole horizon, so an operation whose
    timing is unknown reserves a backup conservatively (no silent overlap).
    """
    intervals: list[tuple[int, int]] = []
    for order in orders:
        windows = (
            parse_time_windows(getattr(order, "time_windows", None))
            if order is not None
            else []
        )
        if not windows:
            return [(now_epoch, horizon_end)]
        for start, end in windows:
            start_epoch = max(now_epoch, int(start.timestamp()))
            end_epoch = (
                min(horizon_end, int(end.timestamp())) if end is not None else horizon_end
            )
            if end_epoch > start_epoch:
                intervals.append((start_epoch, end_epoch))
    if not intervals:
        return [(now_epoch, horizon_end)]
    return _merge_busy(intervals)


def _merge_busy(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent half-open epoch intervals."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _intervals_overlap(
    left: list[tuple[int, int]], right: list[tuple[int, int]]
) -> bool:
    """True when any half-open interval in ``left`` overlaps one in ``right``."""
    for start_l, end_l in left:
        for start_r, end_r in right:
            if start_l < end_r and start_r < end_l:
                return True
    return False


def _claim_backup_operator(
    operation: str,
    operators_by_id: dict[str, Any],
    main_claimed: set[str],
    backup_busy: dict[str, list[tuple[int, int]]],
    demand: list[tuple[int, int]],
    free_capacity: dict[str, float],
    allow_overlap: bool = False,
) -> tuple[Optional[str], bool]:
    """Claim the freest certified operator for the demand window.

    Excludes cluster prime operators. Prefers an operator free over the demand
    window (disjoint from its existing commitments); the freest such wins. When
    none is free and ``allow_overlap`` is set, falls back to the freest operator
    already committed to an overlapping window -- an *overlap share*, valid only
    because the contending clusters are then solved sequentially so the routing
    blocks the actual committed intervals. The operator's busy calendar absorbs
    the demand window either way. Returns ``(operator_id, is_overlap_share)``.
    """
    free_best: Optional[str] = None
    free_best_score = -1.0
    overlap_best: Optional[str] = None
    overlap_best_score = -1.0
    for operator_id, operator in operators_by_id.items():
        if operator_id in main_claimed:
            continue
        if operation not in ops_set(operator.certified_operations):
            continue
        free = free_capacity.get(operator_id, 1.0)
        if _intervals_overlap(backup_busy.get(operator_id, []), demand):
            if free > overlap_best_score:
                overlap_best, overlap_best_score = operator_id, free
        elif free > free_best_score:
            free_best, free_best_score = operator_id, free

    chosen, is_overlap = (free_best, False)
    if chosen is None and allow_overlap:
        chosen, is_overlap = (overlap_best, True)
    if chosen is not None:
        backup_busy[chosen] = _merge_busy(backup_busy.get(chosen, []) + demand)
    return chosen, is_overlap


# -- required-material-available ---------------------------------------------------


# One material reservation produced by cluster-admission charging, in the
# plan contract's record vocabulary (canonical MaterialReservation fields).
MaterialReservationRecord = dict[str, Any]


def apply_material_limits(
    clusters: list[ClusterSpec],
    order_index: dict[str, Any],
    depots: list[Any],
    material_demand: dict[str, "MaterialDemandSpec"],
) -> tuple[list[InfeasibleOrder], list[MaterialReservationRecord]]:
    """Charge per-task consumable demand against depot inventory.

    Tasks are served in descending penalty order per depot; demand beyond the
    depot's remaining inventory makes the task infeasible. Every admitted
    charge is recorded as a provisional material reservation, so feasibility
    and the plan's MaterialReservation outputs are one mechanism: what gated
    admission is exactly what the published plan reserves
    (finalize_material_reservations settles them against the final dispatch).
    """
    if not material_demand:
        return [], []
    remaining = {d.location_id: float(d.inventory_material) for d in depots}
    infeasible: list[InfeasibleOrder] = []
    reservations: list[MaterialReservationRecord] = []

    for cluster in clusters:
        depot_ref = cluster["depot_ref"]
        demanding = []
        passthrough = []
        for task_id in cluster["task_ids"]:
            order = order_index.get(task_id)
            spec = material_demand.get(order.operation_type) if order is not None else None
            if order is None or spec is None:
                passthrough.append(task_id)
            else:
                demanding.append((order, spec))
        demanding.sort(key=lambda pair: float(pair[0].penalty_per_day), reverse=True)

        kept_ids = list(passthrough)
        for order, spec in demanding:
            demand = spec.perAreaHa * _area_ha_for_material(order)
            if demand <= 0:
                kept_ids.append(order.task_id)
                continue
            available = remaining.get(depot_ref, 0.0)
            if demand <= available:
                remaining[depot_ref] = available - demand
                kept_ids.append(order.task_id)
                reservations.append(
                    {
                        "reservation_id": f"res-{order.task_id}",
                        "task_id": order.task_id,
                        "material_type": spec.material,
                        "inventory_location_ref": depot_ref,
                        "quantity": round(demand, 2),
                        "canonical_unit": MATERIAL_INVENTORY_CANONICAL_UNIT,
                        "status": ReservationStatus.PROVISIONAL.value,
                    }
                )
                continue
            infeasible.append(
                _infeasible(
                    order.task_id,
                    cluster["cluster_id"],
                    ReasonCode.INSUFFICIENT_MATERIAL,
                    f"needs {demand:.0f} {spec.material} at {depot_ref}; "
                    f"{available:.0f} remaining",
                )
            )
        cluster["task_ids"] = [t for t in cluster["task_ids"] if t in set(kept_ids)]
    if infeasible:
        logger.info("Material availability excluded %d tasks", len(infeasible))
    return infeasible, reservations


def _area_ha_for_material(order: Any) -> float:
    """Area basis for per-hectare material demand.

    `area` is the legacy field. When area-shaped work is present only on the
    generic quantity surface, accept `work_quantity` if its unit is
    hectare-like.
    """
    area = _nonnegative_float(getattr(order, "area", 0.0))
    if area > 0:
        return area
    unit = str(getattr(order, "work_quantity_unit", "") or "").strip().lower()
    if unit in {"", "ha", "hectare", "hectares"}:
        return _nonnegative_float(getattr(order, "work_quantity", 0.0))
    return 0.0


def _nonnegative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def finalize_material_reservations(
    reservations: list[MaterialReservationRecord],
    dispatch_packages: list[dict[str, Any]],
) -> list[MaterialReservationRecord]:
    """Settle admission-time reservations against the final dispatch.

    A reserved task that was dispatched gets a confirmed reservation spanning
    its scheduled window; one the solve left unserved is marked released (the
    charge is undone but stays on the plan as an audit record of the
    admission decision).
    """
    dispatched = {dp.get("task_id", ""): dp for dp in dispatch_packages}
    for reservation in reservations:
        package = dispatched.get(reservation["task_id"])
        if package is not None:
            reservation["status"] = ReservationStatus.CONFIRMED.value
            reservation["reserved_from"] = package.get("scheduled_start")
            reservation["reserved_to"] = package.get("scheduled_end")
        else:
            reservation["status"] = ReservationStatus.RELEASED.value
    return reservations
