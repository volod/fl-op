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

import ast
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from fl_op.canonical.enums import ReasonCode
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
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            raw = [raw]
    return set(raw or [])


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


def apply_weather_filter(
    orders: list[Any],
    sites: list[Any],
    forecasts: list[Any],
    weather: Optional["WeatherPolicySpec"],
) -> tuple[list[Any], list[InfeasibleOrder]]:
    """Split off weather-sensitive tasks without any compliant forecast window.

    Forecast windows are grouped by location; each task checks the forecast
    location nearest to its work site. Without forecast data (or for
    operations with no declared sensitivity) tasks pass through.
    """
    if weather is None or not weather.sensitivity or not forecasts:
        return orders, []

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
    for order in orders:
        dims = weather.sensitivity.get(order.operation_type)
        if not dims:
            kept.append(order)
            continue
        windows = nearest_windows(order.location_ref)
        if not windows or any(_window_ok(w, dims, weather) for w in windows):
            kept.append(order)
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
    return kept, infeasible


# -- operator-qualified ----------------------------------------------------------


def apply_operator_qualification(
    clusters: list[ClusterSpec],
    order_index: dict[str, Any],
    operators_by_id: dict[str, Any],
) -> list[InfeasibleOrder]:
    """Drop cluster tasks whose operation the allocated operator is not
    certified for; clusters without any operator lose all their tasks."""
    infeasible: list[InfeasibleOrder] = []
    for cluster in clusters:
        operator = operators_by_id.get(cluster.get("operator_ref", ""))
        certified = ops_set(operator.certified_operations) if operator is not None else set()
        kept_ids: list[str] = []
        for task_id in cluster["task_ids"]:
            order = order_index.get(task_id)
            if order is not None and order.operation_type in certified:
                kept_ids.append(task_id)
                continue
            detail = (
                f"operator {cluster.get('operator_ref', '<none>')} not certified "
                f"for {getattr(order, 'operation_type', '<unknown>')}"
            )
            infeasible.append(
                _infeasible(
                    task_id, cluster["cluster_id"], ReasonCode.NO_AVAILABLE_OPERATOR, detail
                )
            )
        cluster["task_ids"] = kept_ids
    if infeasible:
        logger.info("Operator qualification excluded %d tasks", len(infeasible))
    return infeasible


# -- required-material-available ---------------------------------------------------


def apply_material_limits(
    clusters: list[ClusterSpec],
    order_index: dict[str, Any],
    depots: list[Any],
    material_demand: dict[str, "MaterialDemandSpec"],
) -> list[InfeasibleOrder]:
    """Charge per-task consumable demand against depot inventory.

    Tasks are served in descending penalty order per depot; demand beyond the
    depot's remaining inventory makes the task infeasible.
    """
    if not material_demand:
        return []
    remaining = {d.location_id: float(d.inventory_material) for d in depots}
    infeasible: list[InfeasibleOrder] = []

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
            demand = spec.perAreaHa * float(order.area)
            available = remaining.get(depot_ref, 0.0)
            if demand <= available:
                remaining[depot_ref] = available - demand
                kept_ids.append(order.task_id)
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
    return infeasible
