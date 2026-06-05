"""Canonical object builders for accumulated mapping rows."""

from typing import Any

from fl_op.canonical.asset import Asset, GeoLocation
from fl_op.canonical.commitment import InventoryPosition
from fl_op.canonical.common import TimeInterval
from fl_op.canonical.forecast import Forecast
from fl_op.canonical.location import Location
from fl_op.canonical.task import Task
from fl_op.mapping.bindings import BindingTable
from fl_op.mapping.result import MappingResult


def build_asset(table: BindingTable, acc: dict[str, Any]) -> Asset:
    """Build an Asset from one accumulated source row."""
    loc = acc.get("location")
    geoloc = GeoLocation(lat=loc["lat"], lon=loc["lon"]) if loc else None
    roles = [table.asset_role] if table.asset_role else []
    return Asset(
        asset_id=str(acc["assetId"]),
        asset_type=str(acc.get("assetType", "")),
        roles=roles,
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
        source_ref=f"{table.contract_id}:{location_id}",
    )


def build_task(table: BindingTable, acc: dict[str, Any]) -> Task:
    """Build a Task from one accumulated source row."""
    return Task(
        task_id=str(acc["taskId"]),
        order_id=str(acc.get("orderId", "")),
        operation_type=str(acc["operationType"]),
        location_ref=str(acc["locationRef"]),
        area_ha=float(acc["areaHa"]) if "areaHa" in acc else None,
        deadline=acc.get("deadline"),
        priority_class=int(acc.get("priorityClass", 5)),
        revenue_value_eur=float(acc.get("revenueValue", 0.0)),
        penalty_per_day_eur=float(acc.get("penaltyPerDay", 0.0)),
        status=str(acc.get("status", "pending")),
        source_ref=f"{table.contract_id}:{acc['taskId']}",
    )


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
