"""Input preparation for one cluster solve."""

from dataclasses import dataclass, field
from typing import Any, Optional

from fl_op.canonical.enums import ReasonCode
from fl_op.solver.cluster.infeasible import mark_all_infeasible
from fl_op.solver.enforcement import BlockedWindows
from fl_op.solver.travel_time import TravelLookup, operation_set


@dataclass(frozen=True)
class ClusterContext:
    """Prepared plain-dict state needed by the OR-Tools routing model."""

    cluster_id: str
    task_ids: list[str]
    depot_id: str
    cluster_orders: list[dict[str, Any]]
    field_map: dict[str, dict[str, Any]]
    # Every known location (sites + depots/hubs) keyed by id, for resolving
    # pickup locations that sit outside the cluster's site table.
    pickup_location_map: dict[str, dict[str, Any]]
    routing_vehicles: list[dict[str, Any]]
    depot_lat: float
    depot_lon: float
    # Directed (from, to) location-pair travel times from the travel network.
    travel_lookup: TravelLookup = field(default_factory=dict)
    # task_id -> blocked epoch intervals (non-compliant weather windows) the
    # routing model must keep execution out of, occupancy-aware.
    weather_blocked: BlockedWindows = field(default_factory=dict)
    # Tasks removed before routing because the allocated bundle cannot serve
    # their operation type.
    pre_infeasible: list[dict[str, Any]] = field(default_factory=list)


def prepare_cluster_context(
    cluster_dict: dict[str, Any],
    all_orders: list[dict[str, Any]],
    all_vehicles: list[dict[str, Any]],
    all_implements: list[dict[str, Any]],
    all_fields: list[dict[str, Any]],
    all_depots: list[dict[str, Any]],
    travel_lookup: Optional[TravelLookup] = None,
    weather_blocked: Optional[BlockedWindows] = None,
) -> tuple[Optional[ClusterContext], Optional[tuple[list[dict], list[dict]]]]:
    """Build routing context or return an early infeasibility result."""
    cluster_id = cluster_dict.get("cluster_id", "")
    task_ids = cluster_dict.get("task_ids", [])
    depot_id = cluster_dict.get("depot_ref", "")
    allocated: dict[str, list[str]] = cluster_dict.get("allocated_prime_related", {})

    if not task_ids:
        return None, ([], [])

    order_map = {o.task_id: o for o in all_orders}
    field_map = {f.location_id: f for f in all_fields}
    depot_map = {d.location_id: d for d in all_depots}
    # Pickups may reference a depot/hub outside the site table; resolve against
    # both. Sites win id collisions (they carry the work-area geometry).
    pickup_location_map = {**depot_map, **field_map}
    vehicle_map = {v.asset_id: v for v in all_vehicles}
    implement_map = {im.asset_id: im for im in all_implements}

    cluster_orders = [order_map[oid] for oid in task_ids if oid in order_map]
    if not cluster_orders:
        return None, mark_all_infeasible(
            cluster_dict, ReasonCode.UNKNOWN, "orders not found in dataset"
        )

    depot = depot_map.get(depot_id)
    if depot is None:
        return None, mark_all_infeasible(
            cluster_dict, ReasonCode.LOCATION_DATA_INVALID, f"depot {depot_id} not found"
        )

    routing_vehicles = _routing_vehicles(allocated, vehicle_map, implement_map)
    if not routing_vehicles:
        return None, mark_all_infeasible(
            cluster_dict,
            ReasonCode.NO_COMPATIBLE_BUNDLE,
            "allocation pre-pass found no feasible pairs",
        )
    cluster_orders, pre_infeasible = _filter_orders_by_allocated_bundles(
        cluster_id, cluster_orders, routing_vehicles
    )
    if not cluster_orders:
        return None, ([], pre_infeasible)

    return ClusterContext(
        cluster_id=cluster_id,
        task_ids=[order.task_id for order in cluster_orders],
        depot_id=depot_id,
        cluster_orders=cluster_orders,
        field_map=field_map,
        pickup_location_map=pickup_location_map,
        routing_vehicles=routing_vehicles,
        depot_lat=float(depot.lat),
        depot_lon=float(depot.lon),
        travel_lookup=travel_lookup or {},
        weather_blocked=weather_blocked or {},
        pre_infeasible=pre_infeasible,
    ), None


def _routing_vehicles(
    allocated: dict[str, list[str]],
    vehicle_map: dict[str, dict[str, Any]],
    implement_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    routing_vehicles: list[dict[str, Any]] = []
    for vehicle_id, implement_ids in allocated.items():
        if not implement_ids:
            continue
        vehicle = vehicle_map.get(vehicle_id)
        implement = implement_map.get(implement_ids[0])
        if vehicle is not None and implement is not None:
            routing_vehicles.append({"prime": vehicle, "related": implement})
    return routing_vehicles


def _filter_orders_by_allocated_bundles(
    cluster_id: str,
    cluster_orders: list[Any],
    routing_vehicles: list[dict[str, Any]],
) -> tuple[list[Any], list[dict[str, Any]]]:
    compatible: list[Any] = []
    incompatible: list[dict[str, Any]] = []
    for order in cluster_orders:
        op = str(getattr(order, "operation_type", "") or "").upper()
        if any(_bundle_supports_operation(rv, op) for rv in routing_vehicles):
            compatible.append(order)
            continue
        incompatible.append(
            {
                "task_id": order.task_id,
                "cluster_id": cluster_id,
                "reason_code": ReasonCode.NO_COMPATIBLE_BUNDLE.value,
                "detail": (
                    "allocated bundle cannot serve operation "
                    f"{op} for task {order.task_id}"
                ),
            }
        )
    return compatible, incompatible


def _bundle_supports_operation(routing_vehicle: dict[str, Any], operation: str) -> bool:
    prime_ops = operation_set(
        getattr(routing_vehicle.get("prime"), "compatible_operations", [])
    )
    related_ops = operation_set(
        getattr(routing_vehicle.get("related"), "compatible_operations", [])
    )
    return (not prime_ops or operation in prime_ops) and (
        not related_ops or operation in related_ops
    )
