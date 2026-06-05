"""Solver-payload bridge: canonical objects -> dict rows the OR-Tools chain expects.

The existing solver chain consumes plain dict rows keyed by source CSV column
names. Rather than carry raw source rows forward (which would violate "no direct
optimization on raw source data"), the bridge reconstructs each row from the
canonical objects using the SAME x-optimization bindings that produced them. The
reverse direction (canonical path -> source field) is read straight off the
binding table, so every bound column is reproduced with its exact name.

Only entities that survived quality policy exist as canonical objects, so the
solver sees the validated, normalized projection of the snapshot, never raw data.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.xopt import FieldBinding
from fl_op.mapping.bindings import load_binding_table

if TYPE_CHECKING:
    from fl_op.canonical.asset import Asset
    from fl_op.canonical.location import Location
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.canonical.task import Task

logger = logging.getLogger(__name__)


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
    if path[:1] == ["inventory"]:
        return inv_lookup.get((loc.location_id, path[-1]), 0.0)
    return None


def _task_value(task: "Task", binding: FieldBinding) -> Any:
    path = binding.meta.binding.split(".")[1:]
    mapping = {
        ("taskId",): task.task_id,
        ("orderId",): task.order_id,
        ("locationRef",): task.location_ref,
        ("operationType",): task.operation_type,
        ("areaHa",): task.area_ha,
        ("penaltyPerDay",): task.penalty_per_day_eur,
        ("priorityClass",): task.priority_class,
        ("status",): task.status,
        ("revenueValue",): task.revenue_value_eur,
    }
    key = tuple(path)
    if key == ("deadline",):
        return task.deadline.isoformat() if task.deadline else None
    return mapping.get(key)


def _project(bindings: list[FieldBinding], value_fn) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for binding in bindings:
        row[binding.source_field] = value_fn(binding)
    return row


def to_solver_rows(
    snapshot: "PlanningSnapshot", registry: Optional[FileRegistry] = None
) -> dict[str, list[dict[str, Any]]]:
    """Reconstruct the dict-row payload the existing solver chain consumes."""
    registry = registry or FileRegistry()

    veh_t = load_binding_table(registry, "vehicles")
    imp_t = load_binding_table(registry, "implements")
    ops_t = load_binding_table(registry, "operators")
    fld_t = load_binding_table(registry, "fields")
    dep_t = load_binding_table(registry, "depots")
    ord_t = load_binding_table(registry, "orders")

    inv_lookup = {
        (p.inventory_location_ref, p.material_type): p.available_quantity
        for p in snapshot.inventory
    }

    def assets_with_role(role: str) -> list["Asset"]:
        return [a for a in snapshot.assets if role in a.roles]

    rows: dict[str, list[dict[str, Any]]] = {
        "vehicles": [
            _project(veh_t.bindings, lambda b, a=a: _asset_value(a, b))
            for a in assets_with_role("mobile-prime-mover")
        ],
        "implements": [
            _project(imp_t.bindings, lambda b, a=a: _asset_value(a, b))
            for a in assets_with_role("implement")
        ],
        "operators": [
            _project(ops_t.bindings, lambda b, a=a: _asset_value(a, b))
            for a in assets_with_role("operator")
        ],
        "fields": [
            _project(fld_t.bindings, lambda b, l=l: _location_value(l, b, inv_lookup))
            for l in snapshot.locations
            if l.location_type == "field"
        ],
        "depots": [
            _project(dep_t.bindings, lambda b, l=l: _location_value(l, b, inv_lookup))
            for l in snapshot.locations
            if l.location_type == "depot"
        ],
        "orders": [
            _project(ord_t.bindings, lambda b, t=t: _task_value(t, b))
            for t in snapshot.tasks
        ],
    }
    logger.info(
        "Projected solver payload: %s",
        {k: len(v) for k, v in rows.items()},
    )
    return rows
