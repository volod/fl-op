"""Project a canonical PlanningSnapshot into the solver chain's working rows.

The OR-Tools solver chain consumes plain dict rows keyed by *canonical* field
names (asset_id, rated_power, task_id, ...), never by domain-specific physical
column names. This module is the single source of truth for the engine's working
vocabulary: it reconstructs canonical rows from the snapshot's canonical objects
(build_solver_inputs) and can translate raw physical rows into the same canonical
shape (to_canonical_rows), so any selected domain pack projects to one
domain-neutral row vocabulary.

Only entities that survived quality policy exist as canonical objects, so the
solver sees the validated, normalized projection of the snapshot, never raw data.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding
from fl_op.mapping.bindings import load_binding_table
from fl_op.solver.types import (
    CostRateRow,
    DepotRow,
    ForecastRow,
    OperatorRow,
    PrimeMoverRow,
    RelatedRow,
    SiteRow,
    TaskRow,
    TravelLinkRow,
    _SolverRow,
)

if TYPE_CHECKING:
    from fl_op.canonical.asset import Asset
    from fl_op.canonical.cost import CostRate
    from fl_op.canonical.location import Location
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.canonical.task import Task
    from fl_op.canonical.travel import TravelLink

logger = logging.getLogger(__name__)

# Canonical row section names the solver chain consumes (domain-neutral roles).
SECTION_PRIME_MOVERS = "prime_movers"
SECTION_RELATED = "related_equipment"
SECTION_OPERATORS = "operators"
SECTION_SITES = "sites"
SECTION_DEPOTS = "depots"
SECTION_TASKS = "tasks"
SECTION_FORECASTS = "forecasts"
SECTION_TRAVEL_LINKS = "travel_links"
SECTION_COST_RATES = "cost_rates"

# Canonical asset roles a prime mover / related equipment / operator plays.
ROLE_PRIME_MOVER = "mobile-prime-mover"
ROLE_RELATED = "implement"
ROLE_OPERATOR = "operator"
ROLE_DEPOT = "depot"

# Contract id -> canonical row section for registered domain packs.
_CONTRACT_SECTION: dict[str, str] = {
    "vehicles": SECTION_PRIME_MOVERS,
    "implements": SECTION_RELATED,
    "operators": SECTION_OPERATORS,
    "fields": SECTION_SITES,
    "depots": SECTION_DEPOTS,
    "orders": SECTION_TASKS,
    "routes": SECTION_TRAVEL_LINKS,
    "prices": SECTION_COST_RATES,
    "machines": SECTION_PRIME_MOVERS,
    "attachments": SECTION_RELATED,
    "construction-operators": SECTION_OPERATORS,
    "sites": SECTION_SITES,
    "yards": SECTION_DEPOTS,
    "jobs": SECTION_TASKS,
    "drone-ugvs": SECTION_PRIME_MOVERS,
    "drone-uavs": SECTION_PRIME_MOVERS,
    "drone-payload-modules": SECTION_RELATED,
    "drone-operators": SECTION_OPERATORS,
    "drone-logistics-hubs": SECTION_DEPOTS,
    "drone-delivery-locations": SECTION_SITES,
    "drone-restricted-zones": SECTION_SITES,
    "drone-delivery-orders": SECTION_TASKS,
    "drone-travel-links": SECTION_TRAVEL_LINKS,
    "drone-weather": SECTION_FORECASTS,
    "drone-prices": SECTION_COST_RATES,
}

# Contract id -> frozen solver-row dataclass it projects into.
_CONTRACT_ROW_CLASS: dict[str, type[_SolverRow]] = {
    "vehicles": PrimeMoverRow,
    "implements": RelatedRow,
    "operators": OperatorRow,
    "fields": SiteRow,
    "depots": DepotRow,
    "orders": TaskRow,
    "routes": TravelLinkRow,
    "prices": CostRateRow,
    "machines": PrimeMoverRow,
    "attachments": RelatedRow,
    "construction-operators": OperatorRow,
    "sites": SiteRow,
    "yards": DepotRow,
    "jobs": TaskRow,
    "drone-ugvs": PrimeMoverRow,
    "drone-uavs": PrimeMoverRow,
    "drone-payload-modules": RelatedRow,
    "drone-operators": OperatorRow,
    "drone-logistics-hubs": DepotRow,
    "drone-delivery-locations": SiteRow,
    "drone-restricted-zones": SiteRow,
    "drone-delivery-orders": TaskRow,
    "drone-travel-links": TravelLinkRow,
    "drone-weather": ForecastRow,
    "drone-prices": CostRateRow,
}

# Binding path -> canonical solver-row key. The single source of truth for the
# engine's working-row vocabulary; every read site in the solver uses these names.
_CANONICAL_KEY: dict[str, str] = {
    "asset.assetId": "asset_id",
    "asset.assetType": "asset_type",
    "asset.name": "name",
    "asset.homeDepotRef": "home_depot_ref",
    "asset.location.lat": "lat",
    "asset.location.lon": "lon",
    "asset.capabilities.ratedPower": "rated_power",
    "asset.capabilities.requiredPower": "required_power",
    "asset.capabilities.fuelTankVolume": "fuel_tank_volume",
    "asset.capabilities.fuelConsumptionRate": "fuel_consumption_rate",
    "asset.capabilities.travelSpeed": "travel_speed",
    "asset.capabilities.workingWidth": "working_width",
    "asset.capabilities.minOperatingSpeed": "min_speed",
    "asset.capabilities.maxOperatingSpeed": "max_speed",
    "asset.capabilities.fertilizerCapacity": "material_capacity",
    "asset.capabilities.workRates": "work_rates",
    "asset.capabilities.loadCapacity": "load_capacity",
    "asset.capabilities.loadCapacities": "load_capacities",
    "asset.capabilities.compatibleOperations": "compatible_operations",
    "asset.capabilities.certifiedOperations": "certified_operations",
    "asset.availability.shiftStart": "shift_start",
    "asset.availability.shiftEnd": "shift_end",
    "location.locationId": "location_id",
    "location.name": "name",
    "location.lat": "lat",
    "location.lon": "lon",
    "location.areaHa": "area",
    "location.soilType": "soil_type",
    "location.polygon": "polygon",
    "location.restrictedOperations": "restricted_operations",
    "location.restrictionWindows": "restriction_windows",
    "location.inventory.fuel": "inventory_fuel",
    "location.inventory.fertilizer": "inventory_material",
    "task.taskId": "task_id",
    "task.orderId": "order_ref",
    "task.alternativeGroupRef": "alternative_group_ref",
    "task.locationRef": "location_ref",
    "task.operationType": "operation_type",
    "task.areaHa": "area",
    "task.workQuantity": "work_quantity",
    "task.workQuantityUnit": "work_quantity_unit",
    "task.serviceDurationMinutes": "service_duration_min",
    "task.timeWindows": "time_windows",
    "task.dependsOnTaskRef": "depends_on_task_ref",
    "task.loadDemand": "load_demand",
    "task.loadMaterial": "load_material",
    "task.pickupLocationRef": "pickup_location_ref",
    "task.deadline": "deadline",
    "task.penaltyPerDay": "penalty_per_day",
    "task.priorityClass": "priority_class",
    "task.status": "status",
    "task.revenueValue": "revenue",
    "forecast.forecastId": "forecast_id",
    "forecast.location.lat": "lat",
    "forecast.location.lon": "lon",
    "forecast.forecastFor.from": "valid_from",
    "forecast.forecastFor.to": "valid_to",
    "forecast.value.windSpeed": "wind_speed",
    "forecast.value.precipitationRate": "precipitation_rate",
    "forecast.value.soilMoisture": "soil_moisture",
    "travelLink.linkId": "link_id",
    "travelLink.fromLocationRef": "from_location_ref",
    "travelLink.toLocationRef": "to_location_ref",
    "travelLink.travelTimeS": "travel_time_s",
    "travelLink.distanceKm": "distance_km",
    "travelLink.networkMode": "network_mode",
    "costRate.costRateId": "rate_id",
    "costRate.rateType": "rate_type",
    "costRate.unitPrice": "unit_price",
    "costRate.perUnit": "per_unit",
    "costRate.validFrom": "valid_from",
    "costRate.validTo": "valid_to",
}


def _canonical_key(binding: FieldBinding) -> Optional[str]:
    return _CANONICAL_KEY.get(binding.meta.binding)


def _asset_value(asset: "Asset", binding: FieldBinding) -> Any:
    tokens = binding.meta.binding.split(".")
    if "capabilities" in tokens or "availability" in tokens:
        return asset.capability_value(binding.meta.semantic_term)
    path = tokens[1:]
    if path == ["assetId"]:
        return asset.asset_id
    if path == ["assetType"]:
        return asset.asset_type
    if path == ["name"]:
        return asset.name
    if path == ["homeDepotRef"]:
        return asset.home_depot_ref
    if path == ["location", "lat"]:
        return asset.location.lat if asset.location else None
    if path == ["location", "lon"]:
        return asset.location.lon if asset.location else None
    return None


def _location_value(
    loc: "Location", binding: FieldBinding, inv_lookup: dict[tuple[str, str], float]
) -> Any:
    tokens = binding.meta.binding.split(".")
    path = tokens[1:]
    if path == ["locationId"]:
        return loc.location_id
    if path == ["name"]:
        return loc.name
    if path == ["lat"]:
        return loc.lat
    if path == ["lon"]:
        return loc.lon
    if path == ["areaHa"]:
        return loc.area_ha
    if path == ["soilType"]:
        return loc.soil_type
    if path == ["polygon"]:
        return loc.polygon
    if path == ["restrictedOperations"]:
        return list(loc.restricted_operations)
    if path == ["restrictionWindows"]:
        return _intervals_to_strings(loc.restriction_windows)
    if path[:1] == ["inventory"]:
        return inv_lookup.get((loc.location_id, path[-1]), 0.0)
    return None


def _intervals_to_strings(intervals: list) -> list[str]:
    """Serialize TimeInterval objects back to ISO-8601 "from/to" strings."""
    return [
        f"{w.from_.isoformat()}/{w.to.isoformat() if w.to else ''}"
        for w in intervals
    ]


def _forecast_value(forecast: Any, binding: FieldBinding) -> Any:
    path = binding.meta.binding.split(".")[1:]
    if path == ["forecastId"]:
        return forecast.forecast_id
    if path == ["location", "lat"]:
        return (forecast.location or {}).get("lat")
    if path == ["location", "lon"]:
        return (forecast.location or {}).get("lon")
    if path == ["forecastFor", "from"]:
        interval = forecast.forecast_for
        return interval.from_.isoformat() if interval and interval.from_ else None
    if path == ["forecastFor", "to"]:
        interval = forecast.forecast_for
        return interval.to.isoformat() if interval and interval.to else None
    if path[:1] == ["value"]:
        return forecast.value.get(path[-1])
    return None


def _travel_link_value(link: "TravelLink", binding: FieldBinding) -> Any:
    path = binding.meta.binding.split(".")[1:]
    mapping = {
        ("linkId",): link.link_id,
        ("fromLocationRef",): link.from_location_ref,
        ("toLocationRef",): link.to_location_ref,
        ("travelTimeS",): link.travel_time_s,
        ("distanceKm",): link.distance_km,
        ("networkMode",): link.network_mode,
    }
    return mapping.get(tuple(path))


def _cost_rate_value(rate: "CostRate", binding: FieldBinding) -> Any:
    path = binding.meta.binding.split(".")[1:]
    key = tuple(path)
    if key == ("validFrom",):
        return rate.valid_from.isoformat() if rate.valid_from else None
    if key == ("validTo",):
        return rate.valid_to.isoformat() if rate.valid_to else None
    mapping = {
        ("costRateId",): rate.cost_rate_id,
        ("rateType",): rate.rate_type,
        ("unitPrice",): rate.unit_price_eur,
        ("perUnit",): rate.per_unit,
    }
    return mapping.get(key)


def _selected_domains(
    registry: FileRegistry,
    domains: Optional[list[str]] = None,
) -> set[str]:
    return set(domains if domains is not None else registry.active_domains)


def _tables_for_entity(
    registry: FileRegistry,
    entity: str,
    domains: Optional[list[str]] = None,
) -> list:
    """Binding tables of the active-domain contracts mapping one entity.

    Tables are resolved by canonical entity (and disambiguated by asset role at
    the call site), never by contract id. With multiple domains selected the
    tables are unioned so a shared-fleet snapshot can project every canonical
    field demanded by any selected pack.
    """
    active = _selected_domains(registry, domains)
    tables = []
    for cid in registry.list_contracts():
        entry = registry.get_entry(cid)
        if active and entry.domain not in active:
            continue
        if not entry.mapping_ref:
            continue
        table = load_binding_table(registry, cid)
        if table.canonical_entity == entity:
            tables.append(table)
    return tables


def _table_for_entity(
    registry: FileRegistry,
    entity: str,
    domains: Optional[list[str]] = None,
):
    """Binding table of the first active-domain contract mapping one entity."""
    tables = _tables_for_entity(registry, entity, domains)
    return tables[0] if tables else None


def _table_for_role(tables: list, role: str):
    """First binding table whose mapping declares the given asset role."""
    return next((t for t in tables if t.asset_role == role), None)


def _task_value(task: "Task", binding: FieldBinding) -> Any:
    path = binding.meta.binding.split(".")[1:]
    mapping = {
        ("taskId",): task.task_id,
        ("orderId",): task.order_id,
        ("alternativeGroupRef",): task.alternative_group_ref,
        ("locationRef",): task.location_ref,
        ("operationType",): task.operation_type,
        ("areaHa",): task.area_ha,
        ("workQuantity",): task.work_quantity,
        ("workQuantityUnit",): task.work_quantity_unit,
        ("serviceDurationMinutes",): task.service_duration_minutes,
        ("loadDemand",): task.load_demand_kg,
        ("loadMaterial",): task.load_material,
        ("pickupLocationRef",): task.pickup_location_ref,
        ("dependsOnTaskRef",): task.depends_on_task_ref,
        ("penaltyPerDay",): task.penalty_per_day_eur,
        ("priorityClass",): task.priority_class,
        ("status",): task.status,
        ("revenueValue",): task.revenue_value_eur,
    }
    key = tuple(path)
    if key == ("deadline",):
        return task.deadline.isoformat() if task.deadline else None
    if key == ("timeWindows",):
        return [
            f"{w.from_.isoformat()}/{w.to.isoformat() if w.to else ''}"
            for w in task.time_windows
        ]
    return mapping.get(key)


def _project(bindings: list[FieldBinding], value_fn) -> dict[str, Any]:
    """Build one canonical row, keyed by canonical field name (not source column)."""
    row: dict[str, Any] = {}
    for binding in bindings:
        key = _canonical_key(binding)
        if key is None:
            continue
        value = value_fn(binding)
        if value is not None:
            row[key] = value
    return row


def _project_many(tables: list[Any], value_fn) -> dict[str, Any]:
    """Project with the union of several selected-domain binding tables."""
    row: dict[str, Any] = {}
    for table in tables:
        row.update(_project(table.bindings, value_fn))
    return row


def build_solver_inputs(
    snapshot: "PlanningSnapshot",
    registry: Optional[FileRegistry] = None,
    domains: Optional[list[str]] = None,
) -> dict[str, list[Any]]:
    """Reconstruct the typed canonical-row payload the solver chain consumes.

    Each section is projected binding-by-binding into a canonical dict (the
    declarative mapping stays the single source of projection) and then capped
    with its frozen row dataclass via from_canonical_dict. Binding tables are
    resolved from the selected domain set by canonical entity and asset role;
    selecting several domains makes the projection demand-driven over the union
    of their canonical bindings while the solver rows remain domain-neutral.
    """
    registry = registry or FileRegistry()

    asset_tables = _tables_for_entity(registry, "asset", domains)
    location_tables = _tables_for_entity(registry, "location", domains)
    veh_ts = [t for t in asset_tables if t.asset_role == ROLE_PRIME_MOVER]
    imp_ts = [t for t in asset_tables if t.asset_role == ROLE_RELATED]
    ops_ts = [t for t in asset_tables if t.asset_role == ROLE_OPERATOR]
    dep_ts = [t for t in location_tables if t.asset_role == ROLE_DEPOT]
    fld_ts = [t for t in location_tables if t.asset_role != ROLE_DEPOT]
    ord_ts = _tables_for_entity(registry, "task", domains)

    inv_lookup = {
        (p.inventory_location_ref, p.material_type): p.available_quantity
        for p in snapshot.inventory
    }

    def assets_with_role(role: str) -> list["Asset"]:
        return [a for a in snapshot.assets if role in a.roles]

    rows: dict[str, list[Any]] = {
        SECTION_PRIME_MOVERS: [
            PrimeMoverRow.from_canonical_dict(
                _project_many(veh_ts, lambda b, a=a: _asset_value(a, b))
            )
            for a in assets_with_role(ROLE_PRIME_MOVER)
        ]
        if veh_ts
        else [],
        SECTION_RELATED: [
            RelatedRow.from_canonical_dict(
                _project_many(imp_ts, lambda b, a=a: _asset_value(a, b))
            )
            for a in assets_with_role(ROLE_RELATED)
        ]
        if imp_ts
        else [],
        SECTION_OPERATORS: [
            OperatorRow.from_canonical_dict(
                _project_many(ops_ts, lambda b, a=a: _asset_value(a, b))
            )
            for a in assets_with_role(ROLE_OPERATOR)
        ]
        if ops_ts
        else [],
        SECTION_SITES: [
            SiteRow.from_canonical_dict(
                _project_many(fld_ts, lambda b, l=l: _location_value(l, b, inv_lookup))
            )
            for l in snapshot.locations
            if l.location_type == "field"
        ]
        if fld_ts
        else [],
        SECTION_DEPOTS: [
            DepotRow.from_canonical_dict(
                _project_many(dep_ts, lambda b, l=l: _location_value(l, b, inv_lookup))
            )
            for l in snapshot.locations
            if l.location_type == "depot"
        ]
        if dep_ts
        else [],
        SECTION_TASKS: [
            TaskRow.from_canonical_dict(
                _project_many(ord_ts, lambda b, t=t: _task_value(t, b))
            )
            for t in snapshot.tasks
        ]
        if ord_ts
        else [],
    }
    forecast_tables = _tables_for_entity(registry, "forecast", domains)
    rows[SECTION_FORECASTS] = (
        [
            ForecastRow.from_canonical_dict(
                _project_many(forecast_tables, lambda b, f=f: _forecast_value(f, b))
            )
            for f in snapshot.forecasts
        ]
        if forecast_tables
        else []
    )
    travel_tables = _tables_for_entity(registry, "travel-link", domains)
    rows[SECTION_TRAVEL_LINKS] = (
        [
            TravelLinkRow.from_canonical_dict(
                _project_many(travel_tables, lambda b, l=l: _travel_link_value(l, b))
            )
            for l in snapshot.travel_links
        ]
        if travel_tables
        else []
    )
    rate_tables = _tables_for_entity(registry, "cost-rate", domains)
    rows[SECTION_COST_RATES] = (
        [
            CostRateRow.from_canonical_dict(
                _project_many(rate_tables, lambda b, r=r: _cost_rate_value(r, b))
            )
            for r in snapshot.cost_rates
        ]
        if rate_tables
        else []
    )
    logger.info(
        "Projected canonical solver inputs: %s",
        {k: len(v) for k, v in rows.items()},
    )
    return rows


def to_canonical_row(
    row: dict[str, Any], contract_id: str, registry: FileRegistry
) -> Any:
    """Project one physical source row into its typed canonical solver row.

    Renames physical columns to canonical keys via the contract mapping, then
    caps with the contract's row dataclass (absent fields fall to defaults).
    """
    table = load_binding_table(registry, contract_id)
    out: dict[str, Any] = {}
    for binding in table.bindings:
        key = _CANONICAL_KEY.get(binding.meta.binding)
        if key is not None and binding.source_field in row:
            out[key] = row[binding.source_field]
    return _CONTRACT_ROW_CLASS[contract_id].from_canonical_dict(out)


def to_canonical_rows(
    rows: list[dict[str, Any]], contract_id: str, registry: Optional[FileRegistry] = None
) -> list[Any]:
    """Translate raw physical rows for one contract into typed canonical rows."""
    registry = registry or FileRegistry()
    return [to_canonical_row(r, contract_id, registry) for r in rows]
