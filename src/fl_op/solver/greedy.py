"""Greedy warm-start scorer for OR-Tools initial solution hints.

vectorized_score() computes a score matrix over all feasible (V-I pair, order)
combinations in a single numpy broadcast — no Python-level loops over pairs.

Score = gross_margin_estimate - repositioning_cost

greedy_assign() returns a dict {order_id: (vehicle_id, implement_id)} by
taking the top-1 scoring V-I pair for each order.
"""

import logging
import math
from typing import Any

import numpy as np

from fl_op.core.constants import (
    EARTH_RADIUS_KM,
    FUEL_COST_EUR_PER_L,
    SCORE_WEIGHT_MARGIN,
    SCORE_WEIGHT_REPOSITION,
)

logger = logging.getLogger(__name__)

# Assumed average field operation hours per hectare for margin estimation
_OPERATION_H_PER_HA = 1.0


def _haversine_km(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Haversine distance in km between two lat/lon points."""
    r = EARTH_RADIUS_KM
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _estimate_gross_margin(
    order: dict[str, Any],
    implement: dict[str, Any],
) -> float:
    """Rough gross revenue estimate for completing this order with this implement."""
    area = float(order.get("area_ha", 0))
    revenue = float(order.get("estimated_revenue_eur", 0))
    return revenue if revenue > 0 else area * 200.0  # fallback: 200 EUR/ha


def _estimate_repositioning_cost(
    vehicle: dict[str, Any],
    field: dict[str, Any],
) -> float:
    """Diesel cost to drive from vehicle's current position to the field centroid."""
    dist_km = _haversine_km(
        float(vehicle.get("current_lat", 0)),
        float(vehicle.get("current_lon", 0)),
        float(field.get("centroid_lat", 0)),
        float(field.get("centroid_lon", 0)),
    )
    speed_kmh = float(vehicle.get("travel_speed_kmh", 15))
    hours = dist_km / speed_kmh if speed_kmh > 0 else 0
    fuel_l_per_h = float(vehicle.get("fuel_consumption_l_per_h", 18))
    return hours * fuel_l_per_h * FUEL_COST_EUR_PER_L


def vectorized_score(
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, list[tuple[float, int, int]]]:
    """Return {order_id: [(score, v_idx, i_idx), ...]} sorted descending by score.

    Vectorises over all orders and their feasible pairs using numpy broadcast.
    """
    field_map = {f["field_id"]: f for f in fields}
    idx_to_vehicle = {idx: v for v in vehicles for idx in [vehicle_index[v["vehicle_id"]]]}
    idx_to_implement = {idx: im for im in implements for idx in [implement_index[im["implement_id"]]]}

    # Pre-compute vehicle current positions as arrays for batch distance calculation
    v_lats = np.array([float(v.get("current_lat", 0)) for v in vehicles])
    v_lons = np.array([float(v.get("current_lon", 0)) for v in vehicles])
    v_speeds = np.array([float(v.get("travel_speed_kmh", 15)) for v in vehicles])
    v_consumptions = np.array([float(v.get("fuel_consumption_l_per_h", 18)) for v in vehicles])

    results: dict[str, list[tuple[float, int, int]]] = {}

    for order in orders:
        oid = order["order_id"]
        field = field_map.get(order.get("field_id", ""))
        if field is None:
            results[oid] = []
            continue

        f_lat = float(field.get("centroid_lat", 0))
        f_lon = float(field.get("centroid_lon", 0))

        pairs = feasible_pairs.get(oid, [])
        if not pairs:
            results[oid] = []
            continue

        v_indices = np.array([p[0] for p in pairs])
        i_indices = np.array([p[1] for p in pairs])

        # Vectorized haversine repositioning cost for all vehicles in pairs
        lat1 = np.radians(v_lats[v_indices])
        lat2 = math.radians(f_lat)
        lon1 = np.radians(v_lons[v_indices])
        lon2 = math.radians(f_lon)
        dphi = lat2 - lat1
        dlambda = lon2 - lon1
        a = np.sin(dphi / 2) ** 2 + np.cos(lat1) * math.cos(lat2) * np.sin(dlambda / 2) ** 2
        dist_km = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a.clip(0, 1)))
        hours = dist_km / v_speeds[v_indices].clip(1)
        reposition_cost = hours * v_consumptions[v_indices] * FUEL_COST_EUR_PER_L

        # Gross margin: per-order constant for all pairs
        gross_margins = np.full(len(pairs), _estimate_gross_margin(order, {}))

        scores = (
            SCORE_WEIGHT_MARGIN * gross_margins
            - SCORE_WEIGHT_REPOSITION * reposition_cost
        )

        scored_pairs = sorted(
            zip(scores.tolist(), v_indices.tolist(), i_indices.tolist()),
            key=lambda x: -x[0],
        )
        results[oid] = scored_pairs

    return results


def greedy_assign(
    scored: dict[str, list[tuple[float, int, int]]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, tuple[int, int]]:
    """Greedy top-1 assignment: {order_id: (v_idx, i_idx)}.

    Claims the best-scoring (v_idx, i_idx) pair for each order.
    Once an implement index is claimed, it is not reused.
    """
    claimed_implements: set[int] = set()
    assignment: dict[str, tuple[int, int]] = {}

    for oid, candidates in scored.items():
        for _score, v_idx, i_idx in candidates:
            if i_idx not in claimed_implements:
                claimed_implements.add(i_idx)
                assignment[oid] = (v_idx, i_idx)
                break

    logger.debug(
        "Greedy assign: %d/%d orders assigned a V-I pair",
        len(assignment),
        len(scored),
    )
    return assignment
