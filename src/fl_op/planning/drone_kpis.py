"""Drone-logistics KPI derivation from canonical snapshots and plans."""

from __future__ import annotations

import ast
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fl_op.canonical.enums import ReasonCode
from fl_op.solver.restrictions import parse_polygon, point_in_polygon, polygons_intersect

if TYPE_CHECKING:
    from fl_op.canonical.plan import Assignment, UnassignedTask
    from fl_op.canonical.snapshot import PlanningSnapshot


DRONE_KPI_SCORE_KEY = "drone_logistics_kpis"

_UGV_OPERATION = "UGV_DELIVERY"
_UAV_OPERATION = "UAV_DELIVERY"


def build_drone_logistics_kpis(
    snapshot: "PlanningSnapshot",
    assignments: list["Assignment"],
    unassigned: list["UnassignedTask"],
    score: dict[str, Any],
    profile: Any = None,
) -> dict[str, Any]:
    """Build domain KPIs for mixed UGV/UAV logistics plans.

    The helper returns an empty dict for non-drone snapshots, so callers can
    safely invoke it from shared adapters.
    """
    task_by_id = snapshot.task_index()
    if not _looks_like_drone_snapshot(snapshot):
        return {}

    n_assigned = len(assignments)
    n_unassigned = len(unassigned)
    n_total = n_assigned + n_unassigned
    mode_split = {"UGV": 0, "UAV": 0}
    on_time = 0
    late = 0

    for assignment in assignments:
        task = task_by_id.get(assignment.task_id)
        mode = _task_mode(task)
        if mode:
            mode_split[mode] += 1
        if task is not None and task.deadline is not None:
            if _is_on_time(assignment.planned_finish, task.deadline):
                on_time += 1
            else:
                late += 1

    deadline_count = on_time + late
    asset_by_id = {asset.asset_id: asset for asset in snapshot.assets}
    ugv_assets = {
        asset.asset_id
        for asset in snapshot.assets
        if _asset_mode(asset) == "UGV" and _is_prime_mover(asset)
    }
    uav_assets = {
        asset.asset_id
        for asset in snapshot.assets
        if _asset_mode(asset) == "UAV" and _is_prime_mover(asset)
    }
    operators = {
        asset.asset_id for asset in snapshot.assets if "operator" in asset.roles
    }
    used_assets = {
        asset_id
        for assignment in assignments
        for asset_id in assignment.asset_ids
        if asset_id in asset_by_id
    }
    used_operators = {
        operator_id
        for assignment in assignments
        for operator_id in assignment.operator_ids
        if operator_id in operators
    }
    unassigned_reasons = _unassigned_reason_counts(unassigned)
    weather_blocked, weather_infeasible = _weather_blocked_uav_tasks(
        snapshot, profile
    )
    no_fly_excluded = _no_fly_excluded_uav_tasks(snapshot)

    return {
        "total_deliveries": n_total,
        "assigned_deliveries": n_assigned,
        "unassigned_deliveries": n_unassigned,
        "fill_rate_pct": _pct(n_assigned, n_total),
        "on_time_rate_pct": _pct(on_time, deadline_count),
        "on_time_deliveries": on_time,
        "late_deliveries": late,
        "delivery_margin_eur": round(
            float(score.get("total_estimated_margin_eur", 0.0) or 0.0), 2
        ),
        "mode_split": mode_split,
        "mode_split_pct": {
            mode: _pct(count, n_assigned) for mode, count in mode_split.items()
        },
        "ugv_utilization_pct": _pct(len(used_assets & ugv_assets), len(ugv_assets)),
        "uav_utilization_pct": _pct(len(used_assets & uav_assets), len(uav_assets)),
        "used_ugvs": len(used_assets & ugv_assets),
        "total_ugvs": len(ugv_assets),
        "used_uavs": len(used_assets & uav_assets),
        "total_uavs": len(uav_assets),
        "support_team_utilization_pct": _pct(len(used_operators), len(operators)),
        "used_support_operators": len(used_operators),
        "total_support_operators": len(operators),
        "unassigned_reasons": unassigned_reasons,
        "energy_or_fuel_equivalent_usage": _energy_usage(score),
        "rolling_churn_pct": _pct(
            int(score.get("n_changed_after_freeze", 0) or 0),
            max(1, n_assigned),
        ),
        "rolling_changed_assignments": int(
            score.get("n_changed_after_freeze", 0) or 0
        ),
        "rolling_carried_forward_assignments": int(
            score.get("n_carried_forward", 0) or 0
        ),
        "weather_blocked_uav_tasks": len(weather_blocked),
        "weather_infeasible_uav_tasks": len(weather_infeasible),
        "no_fly_exclusion_count": len(no_fly_excluded),
    }


def _looks_like_drone_snapshot(snapshot: "PlanningSnapshot") -> bool:
    return any(
        task.operation_type in {_UGV_OPERATION, _UAV_OPERATION}
        for task in snapshot.tasks
    ) or any(_asset_mode(asset) in {"UGV", "UAV"} for asset in snapshot.assets)


def _task_mode(task: Any) -> str:
    operation = str(getattr(task, "operation_type", "") or "")
    if operation == _UGV_OPERATION:
        return "UGV"
    if operation == _UAV_OPERATION:
        return "UAV"
    return ""


def _asset_mode(asset: Any) -> str:
    value = f"{getattr(asset, 'asset_type', '')} {getattr(asset, 'asset_id', '')}".upper()
    if "UGV" in value:
        return "UGV"
    if "UAV" in value:
        return "UAV"
    return ""


def _is_prime_mover(asset: Any) -> bool:
    return "mobile-prime-mover" in (getattr(asset, "roles", None) or [])


def _is_on_time(planned_finish: datetime, deadline: datetime) -> bool:
    try:
        return planned_finish <= deadline
    except TypeError:
        return planned_finish.replace(tzinfo=None) <= deadline.replace(tzinfo=None)


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100.0, 2)


def _unassigned_reason_counts(unassigned: list["UnassignedTask"]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in unassigned:
        reason = item.reason_code
        code = reason.value if hasattr(reason, "value") else str(reason)
        counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _energy_usage(score: dict[str, Any]) -> dict[str, Any]:
    by_type = dict(score.get("total_energy_quantity_by_type") or {})
    by_unit = dict(score.get("total_energy_quantity_by_unit") or {})
    fuel_l = round(float(score.get("total_fuel_l", 0.0) or 0.0), 2)
    if not by_type and fuel_l > 0:
        by_type["fuel"] = fuel_l
        by_unit["L"] = fuel_l
    cost_raw = score.get("total_energy_cost_eur")
    if cost_raw is None:
        cost_raw = score.get("total_fuel_cost_eur", 0.0)
    return {
        "by_type": by_type,
        "by_unit": by_unit,
        "fuel_equivalent_l": fuel_l,
        "electricity_kwh": round(float(by_unit.get("kWh", 0.0) or 0.0), 2),
        "cost_eur": round(float(cost_raw or 0.0), 2),
    }


def _weather_blocked_uav_tasks(
    snapshot: "PlanningSnapshot", profile: Any
) -> tuple[set[str], set[str]]:
    weather = getattr(profile, "weatherPolicy", None)
    sensitivity = getattr(weather, "sensitivity", None) or {}
    dims = sensitivity.get(_UAV_OPERATION) or []
    if weather is None or not dims or not snapshot.forecasts:
        return set(), set()

    by_location: dict[tuple[float, float], list[Any]] = {}
    for forecast in snapshot.forecasts:
        location = forecast.location or {}
        if "lat" not in location or "lon" not in location:
            continue
        by_location.setdefault(
            (float(location["lat"]), float(location["lon"])), []
        ).append(forecast)
    if not by_location:
        return set(), set()

    site_coords = {
        location.location_id: (float(location.lat), float(location.lon))
        for location in snapshot.locations
    }
    blocked: set[str] = set()
    infeasible: set[str] = set()
    for task in snapshot.tasks:
        if task.operation_type != _UAV_OPERATION:
            continue
        coords = site_coords.get(task.location_ref)
        if coords is None:
            continue
        key = min(
            by_location,
            key=lambda item: (item[0] - coords[0]) ** 2 + (item[1] - coords[1]) ** 2,
        )
        windows = by_location[key]
        bad = [forecast for forecast in windows if not _forecast_ok(forecast, dims, weather)]
        if bad:
            blocked.add(task.task_id)
        if windows and len(bad) == len(windows):
            infeasible.add(task.task_id)
    return blocked, infeasible


def _forecast_ok(forecast: Any, dims: list[str], weather: Any) -> bool:
    wind = forecast.value.get("windSpeed")
    rain = forecast.value.get("precipitationRate")
    soil = forecast.value.get("soilMoisture")
    if "wind" in dims and wind is not None and wind > weather.maxWindMs:
        return False
    if "rain" in dims and rain is not None and rain > weather.maxRainMmPerH:
        return False
    if (
        "soil-moisture" in dims
        and soil is not None
        and soil > weather.maxSoilMoisturePct
    ):
        return False
    return True


def _no_fly_excluded_uav_tasks(snapshot: "PlanningSnapshot") -> set[str]:
    site_by_id = {location.location_id: location for location in snapshot.locations}
    restricted_areas = [
        location
        for location in snapshot.locations
        if _UAV_OPERATION in _operation_set(location.restricted_operations)
    ]
    excluded: set[str] = set()
    for task in snapshot.tasks:
        if task.operation_type != _UAV_OPERATION:
            continue
        site = site_by_id.get(task.location_ref)
        if site is None:
            continue
        if _UAV_OPERATION in _operation_set(site.restricted_operations):
            excluded.add(task.task_id)
            continue
        site_polygon = parse_polygon(site.polygon)
        site_point = (float(site.lon), float(site.lat))
        for area in restricted_areas:
            if area.location_id == site.location_id:
                continue
            area_polygon = parse_polygon(area.polygon)
            if not area_polygon:
                continue
            if site_polygon:
                blocked = polygons_intersect(site_polygon, area_polygon)
            else:
                blocked = point_in_polygon(site_point, area_polygon)
            if blocked:
                excluded.add(task.task_id)
                break
    return excluded


def _operation_set(raw: Any) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {raw}
    if isinstance(raw, (list, tuple, set)):
        return {str(item) for item in raw}
    return {str(raw)}
