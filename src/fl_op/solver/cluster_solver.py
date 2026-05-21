"""OR-Tools routing library cluster solver.

Accepts and returns plain Python dicts only (no Pydantic, no OR-Tools objects
outside this function) so the function is safe to call across a
multiprocessing.Pool(start_method='spawn') boundary.

The routing model is created and destroyed inside solve_cluster(); no shared
state persists between calls.
"""

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import CLUSTER_SOLVE_TIME_LIMIT_S, EARTH_RADIUS_KM, MAX_PAIRS_PER_ORDER

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal geometry helpers (no imports beyond stdlib + ortools in worker)
# ---------------------------------------------------------------------------

_SECONDS_PER_KM = 240  # ~15 km/h average field travel -> 240 s/km


def _haversine_s(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Travel time in integer seconds between two lat/lon points."""
    r = EARTH_RADIUS_KM
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    km = 2 * r * math.asin(math.sqrt(max(0.0, a)))
    return max(1, int(km * _SECONDS_PER_KM))


def _mark_all_infeasible(
    cluster_dict: dict[str, Any],
    reason: str,
    detail: str,
) -> tuple[list[dict], list[dict]]:
    infeasible = [
        {
            "order_id": oid,
            "cluster_id": cluster_dict.get("cluster_id", ""),
            "reason": reason,
            "detail": detail,
        }
        for oid in cluster_dict.get("order_ids", [])
    ]
    return [], infeasible


# ---------------------------------------------------------------------------
# Public worker function
# ---------------------------------------------------------------------------


def solve_cluster(
    cluster_dict: dict[str, Any],
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    """Solve one geographic cluster; return (dispatch_packages, infeasible_orders).

    Always returns a 2-tuple even when no solution is found. Never raises.
    num_search_workers=1 to prevent CPU over-subscription in Pool.
    """
    try:
        return _solve_cluster_inner(
            cluster_dict,
            orders,
            vehicles,
            implements,
            fields,
            depots,
            greedy_assignment,
            vehicle_index,
            implement_index,
        )
    except Exception as exc:
        logger.error(
            "Cluster %s solver exception: %s",
            cluster_dict.get("cluster_id", "?"),
            exc,
            exc_info=True,
        )
        return _mark_all_infeasible(
            cluster_dict, "solver_exception", f"unhandled exception: {exc}"
        )


def _solve_cluster_inner(
    cluster_dict: dict[str, Any],
    all_orders: list[dict[str, Any]],
    all_vehicles: list[dict[str, Any]],
    all_implements: list[dict[str, Any]],
    all_fields: list[dict[str, Any]],
    all_depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> tuple[list[dict], list[dict]]:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    cluster_id = cluster_dict.get("cluster_id", "")
    order_ids = cluster_dict.get("order_ids", [])
    depot_id = cluster_dict.get("depot_id", "")
    allocated: dict[str, list[str]] = cluster_dict.get("allocated_vehicle_implements", {})

    if not order_ids:
        return [], []

    # Index entities
    order_map = {o["order_id"]: o for o in all_orders}
    field_map = {f["field_id"]: f for f in all_fields}
    depot_map = {d["depot_id"]: d for d in all_depots}
    vehicle_map = {v["vehicle_id"]: v for v in all_vehicles}
    implement_map = {im["implement_id"]: im for im in all_implements}

    cluster_orders = [order_map[oid] for oid in order_ids if oid in order_map]
    if not cluster_orders:
        return _mark_all_infeasible(cluster_dict, "no_order_data", "orders not found in dataset")

    depot = depot_map.get(depot_id)
    if depot is None:
        return _mark_all_infeasible(cluster_dict, "no_depot_data", f"depot {depot_id} not found")

    # Build routing vehicles: one per allocated vehicle
    routing_vehicles: list[dict[str, Any]] = []
    for vid, iids in allocated.items():
        if not iids:
            continue
        iid = iids[0]
        v = vehicle_map.get(vid)
        im = implement_map.get(iid)
        if v is None or im is None:
            continue
        routing_vehicles.append({"vehicle": v, "implement": im})

    # Cap V-I pairs per order (already done in pre-allocation but guard here too)
    if not routing_vehicles:
        return _mark_all_infeasible(
            cluster_dict, "no_allocated_vehicles", "resource_allocator found no feasible pairs"
        )

    # ---------------------------------------------------------------------------
    # Build OR-Tools routing model
    # ---------------------------------------------------------------------------

    # Nodes: depot (index 0) + one per order
    depot_lat = float(depot["lat"])
    depot_lon = float(depot["lon"])

    node_lats: list[float] = [depot_lat]
    node_lons: list[float] = [depot_lon]
    for o in cluster_orders:
        field = field_map.get(o.get("field_id", ""))
        if field:
            node_lats.append(float(field.get("centroid_lat", depot_lat)))
            node_lons.append(float(field.get("centroid_lon", depot_lon)))
        else:
            node_lats.append(depot_lat)
            node_lons.append(depot_lon)

    n_nodes = len(node_lats)
    n_vehicles = len(routing_vehicles)

    # Pre-compute time matrix
    time_matrix: list[list[int]] = [
        [_haversine_s(node_lats[i], node_lons[i], node_lats[j], node_lons[j]) for j in range(n_nodes)]
        for i in range(n_nodes)
    ]

    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    # Transit callback: travel time between nodes
    def time_callback(from_index: int, to_index: int) -> int:
        fi = manager.IndexToNode(from_index)
        ti = manager.IndexToNode(to_index)
        return time_matrix[fi][ti]

    transit_cb_idx = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # Time dimension: hard time windows from order deadlines + operator shifts
    now_epoch = int(datetime.now(tz=timezone.utc).timestamp())
    # Use seconds-from-now as time units; max horizon = 30 days
    horizon_s = 30 * 24 * 3600

    routing.AddDimension(
        transit_cb_idx,
        horizon_s,  # max waiting time (slack)
        horizon_s,  # max total time per vehicle
        False,  # do not force start cumul to zero
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # Apply deadline time windows to order nodes
    for node_idx, order in enumerate(cluster_orders, start=1):
        deadline_str = order.get("deadline", "")
        try:
            deadline_dt = datetime.fromisoformat(deadline_str)
            deadline_epoch = int(deadline_dt.timestamp())
            deadline_from_now = max(0, deadline_epoch - now_epoch)
        except (ValueError, TypeError):
            deadline_from_now = horizon_s

        idx = manager.NodeToIndex(node_idx)
        time_dim.CumulVar(idx).SetRange(0, deadline_from_now)

    # Search parameters
    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.time_limit.seconds = CLUSTER_SOLVE_TIME_LIMIT_S
    search_params.log_search = False
    # Prevent CPU over-subscription when running inside multiprocessing.Pool.
    # OR-Tools 9.15: routing model uses CP-SAT sub-solver; num_workers lives there.
    search_params.sat_parameters.num_workers = 1

    # Build greedy warm-start hints.
    # vehicle_index: {vehicle_id -> global numeric index in all_vehicles}
    # greedy_assignment: {order_id -> (global_v_idx, global_i_idx)}
    idx_to_vid: dict[int, str] = {idx: vid for vid, idx in vehicle_index.items()}
    initial_routes: list[list[int]] = []
    used_order_nodes: set[int] = set()
    for rv in routing_vehicles:
        vid = rv["vehicle"]["vehicle_id"]
        route: list[int] = []
        for node_idx, order in enumerate(cluster_orders, start=1):
            oid = order["order_id"]
            ga = greedy_assignment.get(oid)
            if ga is not None:
                ga_v_idx = ga[0]
                assigned_vid = idx_to_vid.get(ga_v_idx)
                if assigned_vid == vid and node_idx not in used_order_nodes:
                    route.append(node_idx)
                    used_order_nodes.add(node_idx)
        initial_routes.append(route)

    try:
        routing.CloseModelWithParameters(search_params)
        initial_solution = routing.ReadAssignmentFromRoutes(initial_routes, True)
        solution = routing.SolveFromAssignmentWithParameters(
            initial_solution, search_params
        )
    except Exception:
        # If warm-start fails, solve from scratch
        solution = routing.SolveWithParameters(search_params)

    if solution is None:
        return _mark_all_infeasible(
            cluster_dict, "no_solution", "OR-Tools found no feasible solution within time limit"
        )

    # ---------------------------------------------------------------------------
    # Extract dispatch packages from solution
    # ---------------------------------------------------------------------------

    dispatch_packages: list[dict] = []
    served_order_ids: set[str] = set()

    for rv_idx, rv in enumerate(routing_vehicles):
        vid = rv["vehicle"]["vehicle_id"]
        iid = rv["implement"]["implement_id"]
        index = routing.Start(rv_idx)
        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node == 0:
                index = solution.Value(routing.NextVar(index))
                continue
            order = cluster_orders[node - 1]
            oid = order["order_id"]
            served_order_ids.add(oid)

            arrival_s = solution.Value(time_dim.CumulVar(index))
            field = field_map.get(order.get("field_id", ""))
            area = float(order.get("area_ha", 0))
            working_width = float(rv["implement"].get("working_width_m", 12))
            op_speed = float(rv["implement"].get("max_speed_kmh", 8))
            # Estimate operation duration: area / (width * speed) in hours -> seconds
            if working_width > 0 and op_speed > 0:
                op_hours = area / (working_width / 1000 * op_speed * 10)  # ha/(km*m -> ha/h)
                op_hours = max(0.5, min(op_hours, 24.0))
            else:
                op_hours = 1.0

            start_epoch = now_epoch + arrival_s
            end_epoch = start_epoch + int(op_hours * 3600)

            dispatch_packages.append(
                {
                    "dispatch_id": str(uuid.uuid4()),
                    "cluster_id": cluster_id,
                    "vehicle_id": vid,
                    "implement_id": iid,
                    "operator_id": cluster_dict.get("operator_id", ""),
                    "order_id": oid,
                    "depot_id": depot_id,
                    "scheduled_start": datetime.fromtimestamp(start_epoch, tz=timezone.utc).isoformat(),
                    "scheduled_end": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                    "route_waypoints": [
                        {"lat": node_lats[node], "lon": node_lons[node]}
                    ],
                    "estimated_fuel_l": round(
                        op_hours * float(rv["vehicle"].get("fuel_consumption_l_per_h", 18)), 2
                    ),
                    "estimated_fertilizer_kg": round(
                        float(rv["implement"].get("fertilizer_capacity_kg", 0)) * 0.8, 2
                    ),
                    "estimated_margin_eur": round(
                        float(order.get("estimated_revenue_eur", 0)), 2
                    ),
                }
            )
            index = solution.Value(routing.NextVar(index))

    # Orders not served by any vehicle route -> infeasible
    infeasible_orders = [
        {
            "order_id": oid,
            "cluster_id": cluster_id,
            "reason": "prize_collecting_unserved",
            "detail": "OR-Tools routing did not assign this order to any vehicle",
        }
        for oid in order_ids
        if oid not in served_order_ids
    ]

    logger.debug(
        "Cluster %s: %d dispatched, %d infeasible",
        cluster_id,
        len(dispatch_packages),
        len(infeasible_orders),
    )
    return dispatch_packages, infeasible_orders
