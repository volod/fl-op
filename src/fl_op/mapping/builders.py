"""Canonical object builders for accumulated mapping rows.

``ENTITY_EMITTERS`` is the adaptive dispatch table the mapping engine consults:
one emitter per canonical entity, each appending built objects to the shared
MappingResult. New canonical entities plug in via ``register_entity_emitter``
without touching the engine.
"""

import logging
import ast
from typing import Any, Callable

from fl_op.canonical.asset import Asset, GeoLocation
from fl_op.canonical.commitment import Commitment, InventoryPosition
from fl_op.canonical.common import TimeInterval
from fl_op.canonical.cost import CostRate
from fl_op.canonical.enums import AssetMobility, CommitmentHardness
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.observation import Observation
from fl_op.canonical.task import Task
from fl_op.canonical.travel import TravelLink
from fl_op.mapping.bindings import BindingTable
from fl_op.mapping.result import MappingResult

logger = logging.getLogger(__name__)


def build_asset(table: BindingTable, acc: dict[str, Any]) -> Asset:
    """Build an Asset from one accumulated source row."""
    loc = acc.get("location")
    geoloc = GeoLocation(lat=loc["lat"], lon=loc["lon"]) if loc else None
    roles = [table.asset_role] if table.asset_role else []
    return Asset(
        asset_id=str(acc["assetId"]),
        asset_type=str(acc.get("assetType", "")),
        roles=roles,
        mobility=str(acc.get("mobility", AssetMobility.MOBILE.value)),
        name=str(acc.get("name", "")),
        home_depot_ref=acc.get("homeDepotRef"),
        location=geoloc,
        capabilities=acc["_capabilities"],
        source_ref=f"{table.contract_id}:{acc['assetId']}",
    )


def build_location(
    table: BindingTable,
    acc: dict[str, Any],
    result: MappingResult,
) -> Location:
    """Build a Location and append any inventory positions."""
    loc_type = "depot" if table.asset_role == "depot" else "field"
    location_id = str(acc["locationId"])
    for material, unit, qty in acc["_inventory"]:
        result.inventory.append(
            InventoryPosition(
                inventory_location_ref=location_id,
                material_type=material,
                available_quantity=float(qty),
                canonical_unit=unit or "",
            )
        )
    return Location(
        location_id=location_id,
        location_type=loc_type,
        lat=float(acc["lat"]),
        lon=float(acc["lon"]),
        name=str(acc.get("name", "")),
        area_ha=float(acc["areaHa"]) if "areaHa" in acc else None,
        soil_type=str(acc.get("soilType", "")),
        polygon=parse_polygon(acc.get("polygon")),
        restricted_operations=[str(op) for op in acc.get("restrictedOperations") or []],
        restriction_windows=parse_time_intervals(acc.get("restrictionWindows")),
        source_ref=f"{table.contract_id}:{location_id}",
    )


def build_task(table: BindingTable, acc: dict[str, Any]) -> Task:
    """Build a Task from one accumulated source row."""
    duration = acc.get("serviceDurationMinutes")
    return Task(
        task_id=str(acc["taskId"]),
        order_id=str(acc.get("orderId", "")),
        operation_type=str(acc["operationType"]),
        location_ref=str(acc["locationRef"]),
        area_ha=float(acc["areaHa"]) if "areaHa" in acc else None,
        work_quantity=float(acc["workQuantity"]) if "workQuantity" in acc else None,
        work_quantity_unit=str(acc.get("workQuantityUnit", "")),
        service_duration_minutes=int(float(duration)) if duration else None,
        load_demand_kg=float(acc["loadDemand"]) if "loadDemand" in acc else None,
        load_material=str(acc.get("loadMaterial", "")),
        pickup_location_ref=str(acc.get("pickupLocationRef") or "") or None,
        time_windows=parse_time_intervals(acc.get("timeWindows")),
        depends_on_task_ref=str(acc.get("dependsOnTaskRef") or "") or None,
        deadline=acc.get("deadline"),
        priority_class=int(acc.get("priorityClass", 5)),
        revenue_value_eur=float(acc.get("revenueValue", 0.0)),
        penalty_per_day_eur=float(acc.get("penaltyPerDay", 0.0)),
        status=str(acc.get("status", "pending")),
        source_ref=f"{table.contract_id}:{acc['taskId']}",
    )


def parse_time_intervals(raw: Any) -> list[TimeInterval]:
    """Parse ISO-8601 "from/to" interval strings into TimeInterval objects.

    Accepts a list of interval strings (the coerced interval-set value) or
    None; malformed items are skipped so one bad window cannot drop the task.
    """
    if not raw:
        return []
    intervals: list[TimeInterval] = []
    for item in raw:
        parts = str(item).split("/", 1)
        if len(parts) != 2 or not parts[0]:
            continue
        try:
            intervals.append(
                TimeInterval(**{"from": parts[0], "to": parts[1] or None})
            )
        except Exception:
            logger.warning("Skipping malformed time window %r", item)
    return intervals


def parse_polygon(raw: Any) -> list[list[float]]:
    """Parse canonical [lat, lon] polygon vertices from list or stringified list."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            logger.warning("Skipping malformed polygon %r", raw)
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    points: list[list[float]] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            points.append([float(pair[0]), float(pair[1])])
        except (TypeError, ValueError):
            logger.warning("Skipping malformed polygon vertex %r", pair)
    if len(points) >= 2 and points[0] == points[-1]:
        points.pop()
    return points if len(points) >= 3 else []


def build_forecast(table: BindingTable, acc: dict[str, Any]) -> Forecast:
    """Build a Forecast from one accumulated source row."""
    forecast_for = acc.get("forecastFor", {})
    interval = None
    if forecast_for.get("from"):
        interval = TimeInterval(
            **{"from": forecast_for["from"], "to": forecast_for.get("to")}
        )
    return Forecast(
        forecast_id=str(acc["forecastId"]),
        location=acc.get("location"),
        forecast_for=interval,
        value=acc.get("value", {}),
        source_ref=f"{table.contract_id}:{acc['forecastId']}",
    )


def build_observation(table: BindingTable, acc: dict[str, Any]) -> Observation:
    """Build an Observation from one accumulated source row.

    The raw source metric code is normalized through the mapping's
    ``metricCodes`` table so the engine always sees canonical metric codes;
    unmapped codes pass through unchanged (retained, not interpreted).
    """
    value = acc.get("value")
    raw_metric = str(acc.get("metric", ""))
    return Observation(
        observation_id=str(acc["observationId"]),
        entity_ref=str(acc.get("entityRef", "")),
        metric=table.metric_codes.get(raw_metric, raw_metric),
        value=float(value) if value is not None else None,
        state_value=str(acc.get("stateValue", "")),
        unit=acc.get("unit"),
        observed_at=acc.get("observedAt"),
        ingested_at=acc.get("ingestedAt"),
        quality_flag=str(acc.get("qualityFlag", "")),
        source_ref=f"{table.contract_id}:{acc['observationId']}",
    )


def build_travel_link(table: BindingTable, acc: dict[str, Any]) -> TravelLink:
    """Build a TravelLink from one accumulated source row."""
    return TravelLink(
        link_id=str(acc["linkId"]),
        from_location_ref=str(acc["fromLocationRef"]),
        to_location_ref=str(acc["toLocationRef"]),
        travel_time_s=float(acc["travelTimeS"]),
        distance_km=float(acc["distanceKm"]) if "distanceKm" in acc else None,
        source_ref=f"{table.contract_id}:{acc['linkId']}",
    )


def build_cost_rate(table: BindingTable, acc: dict[str, Any]) -> CostRate:
    """Build a CostRate from one accumulated source row."""
    return CostRate(
        cost_rate_id=str(acc["costRateId"]),
        rate_type=str(acc["rateType"]),
        unit_price_eur=float(acc["unitPrice"]),
        per_unit=str(acc.get("perUnit", "")),
        valid_from=acc.get("validFrom"),
        valid_to=acc.get("validTo"),
        source_ref=f"{table.contract_id}:{acc['costRateId']}",
    )


def build_commitment(table: BindingTable, acc: dict[str, Any]) -> Commitment:
    """Build a Commitment from one accumulated source row."""
    hardness_raw = str(acc.get("hardness", CommitmentHardness.MEDIUM.value))
    value: dict[str, Any] = {}
    if acc.get("deadline") is not None:
        value["deadline"] = acc["deadline"]
    if acc.get("latenessPenalty") is not None:
        value["latenessPenalty"] = acc["latenessPenalty"]
    return Commitment(
        commitment_id=str(acc["commitmentId"]),
        contract_id=str(acc.get("contractRef", "")),
        task_id=acc.get("taskRef"),
        commitment_type=str(acc.get("commitmentType", "")),
        hardness=CommitmentHardness(hardness_raw),
        value=value,
        valid_from=acc.get("validFrom"),
        valid_to=acc.get("validTo"),
    )


# -- adaptive entity dispatch -------------------------------------------------

# An emitter consumes one accumulated row and appends canonical objects to the
# shared MappingResult.
EntityEmitter = Callable[[BindingTable, dict[str, Any], MappingResult], None]


def _emit_asset(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.assets.append(build_asset(table, acc))


def _emit_location(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.locations.append(build_location(table, acc, result))


def _emit_task(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.tasks.append(build_task(table, acc))


def _emit_forecast(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.forecasts.append(build_forecast(table, acc))


def _emit_observation(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.observations.append(build_observation(table, acc))


def _emit_commitment(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.commitments.append(build_commitment(table, acc))


def _emit_travel_link(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.travel_links.append(build_travel_link(table, acc))


def _emit_cost_rate(table: BindingTable, acc: dict[str, Any], result: MappingResult) -> None:
    result.cost_rates.append(build_cost_rate(table, acc))


ENTITY_EMITTERS: dict[str, EntityEmitter] = {
    "asset": _emit_asset,
    "location": _emit_location,
    "task": _emit_task,
    "forecast": _emit_forecast,
    "observation": _emit_observation,
    "commitment": _emit_commitment,
    "travel-link": _emit_travel_link,
    "cost-rate": _emit_cost_rate,
}


def register_entity_emitter(entity: str, emitter: EntityEmitter) -> None:
    """Register (or override) the emitter handling one canonical entity."""
    ENTITY_EMITTERS[entity] = emitter
