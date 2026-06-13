"""Travel time and field service duration calculations for OR-Tools routing.

Travel times come from the travel network (canonical travel-link entities):
the lookup is the all-pairs shortest-path closure over the directed link
graph, so a location pair connected only through intermediate stops still
gets a network time. Pairs without any network path fall back to haversine
distance at the average field travel speed, so a sparse network is valid
input.
"""

import heapq
import logging
import math
from typing import Any, Optional

from fl_op.core.constants import EARTH_RADIUS_KM, TRAVEL_NETWORK_MAX_COMPOSE_NODES

logger = logging.getLogger(__name__)

_SECONDS_PER_KM = 240  # ~15 km/h average field travel speed -> 240 s/km

# Bounds and fallback for the quantity-driven service-duration estimate.
_OP_HOURS_MIN = 0.5
_OP_HOURS_MAX = 24.0
_OP_HOURS_FALLBACK = 1.0

# Canonical area unit; an empty work-quantity unit is its legacy alias.
_AREA_UNIT = "ha"
_AREA_LIKE_UNITS = ("", _AREA_UNIT)

# (from_location_ref, to_location_ref) -> travel time in integer seconds.
TravelLookup = dict[tuple[str, str], int]


def build_travel_lookup(travel_links: list[Any]) -> TravelLookup:
    """Index travel-link rows by directed location pair (positive times only),
    then close the graph under shortest paths so multi-hop connections count."""
    lookup: TravelLookup = {}
    for link in travel_links:
        seconds = _nonnegative(link.travel_time_s)
        if seconds <= 0:
            continue
        lookup[(str(link.from_location_ref), str(link.to_location_ref))] = max(
            1, int(seconds)
        )
    return _compose_shortest_paths(lookup)


def _compose_shortest_paths(direct: TravelLookup) -> TravelLookup:
    """All-pairs shortest-path closure over the directed link graph.

    One Dijkstra pass per source node; a direct link longer than a composed
    route is replaced by the composed time (the lookup answers "fastest road
    time", not "longest declared edge"). Networks beyond the node cap keep
    the direct lookup only, so an oversized graph degrades gracefully
    instead of stalling the solve.
    """
    nodes = sorted({node for pair in direct for node in pair})
    if len(nodes) > TRAVEL_NETWORK_MAX_COMPOSE_NODES:
        logger.warning(
            "Travel network has %d nodes (cap %d); skipping shortest-path "
            "composition, direct links only",
            len(nodes),
            TRAVEL_NETWORK_MAX_COMPOSE_NODES,
        )
        return direct

    adjacency: dict[str, list[tuple[str, int]]] = {}
    for (from_ref, to_ref), seconds in direct.items():
        adjacency.setdefault(from_ref, []).append((to_ref, seconds))

    composed: TravelLookup = {}
    for source in nodes:
        best: dict[str, int] = {source: 0}
        heap: list[tuple[int, str]] = [(0, source)]
        while heap:
            dist, node = heapq.heappop(heap)
            if dist > best.get(node, dist):
                continue
            for neighbor, seconds in adjacency.get(node, []):
                candidate = dist + seconds
                if candidate < best.get(neighbor, candidate + 1):
                    best[neighbor] = candidate
                    heapq.heappush(heap, (candidate, neighbor))
        for target, dist in best.items():
            if target != source:
                composed[(source, target)] = dist
    return composed


def _haversine_s(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Travel time in integer seconds between two lat/lon points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    km = 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(max(0.0, a)))
    return max(1, int(km * _SECONDS_PER_KM))


def travel_seconds(
    from_ref: str,
    to_ref: str,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    travel_lookup: Optional[TravelLookup] = None,
) -> int:
    """Travel time between two locations: network link first, haversine fallback.

    A missing directed link falls back to the reverse direction (road links
    are usually symmetric) before the geometric estimate.
    """
    if travel_lookup and from_ref and to_ref and from_ref != to_ref:
        seconds = travel_lookup.get((from_ref, to_ref)) or travel_lookup.get(
            (to_ref, from_ref)
        )
        if seconds:
            return seconds
    return _haversine_s(lat1, lon1, lat2, lon2)


def _estimate_operation_seconds(order: Any, implement: Any) -> int:
    """Estimate service duration for one order and implement.

    Precedence: an explicit service-duration override wins; otherwise the
    generic work quantity drives the estimate (area is its legacy alias).
    An implement work rate declared for the quantity's unit converts any
    unit kind (m3, items, ha) into effort directly; area-like quantities
    without a declared rate use the width-times-speed coverage model; other
    units without a rate fall back to the nominal effort.
    """
    explicit_minutes = _nonnegative(order.service_duration_min)
    if explicit_minutes > 0:
        return int(explicit_minutes * 60)

    quantity = _nonnegative(order.work_quantity)
    unit = str(order.work_quantity_unit or "").strip()
    if quantity <= 0:
        quantity = _nonnegative(order.area)
        unit = ""

    rate = _work_rate_for(implement, unit)
    if quantity > 0 and rate > 0:
        op_hours = max(_OP_HOURS_MIN, min(quantity / rate, _OP_HOURS_MAX))
        return int(op_hours * 3600)

    if unit not in _AREA_LIKE_UNITS:
        return int(_OP_HOURS_FALLBACK * 3600)

    working_width = _nonnegative(implement.working_width)
    op_speed = _nonnegative(implement.max_speed)
    if working_width > 0 and op_speed > 0:
        op_hours = quantity / (working_width / 1000 * op_speed * 10)
        op_hours = max(_OP_HOURS_MIN, min(op_hours, _OP_HOURS_MAX))
    else:
        op_hours = _OP_HOURS_FALLBACK
    return int(op_hours * 3600)


def _work_rate_for(implement: Any, unit: str) -> float:
    """Declared work rate (quantity per hour) for one unit; 0 when absent.

    An empty unit is the legacy area alias, so it matches a declared "ha"
    rate the same way an explicit "ha" quantity does.
    """
    rates = getattr(implement, "work_rates", None)
    if not isinstance(rates, dict):
        return 0.0
    return _nonnegative(rates.get(unit or _AREA_UNIT))


def _nonnegative(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0
