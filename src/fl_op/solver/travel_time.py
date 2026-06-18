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
import ast
from collections.abc import Mapping
from typing import Any, Optional

from fl_op.core.constants import (
    AIR_TRAVEL_CIRCUITY,
    FALLBACK_TRAVEL_SPEED_KMH,
    GROUND_TRAVEL_CIRCUITY,
    ROUTE_GEOMETRY_MAX_LENGTH_RATIO,
    TRAVEL_NETWORK_MAX_COMPOSE_NODES,
)
from fl_op.core.geometry import path_distance_km, travel_time_seconds

logger = logging.getLogger(__name__)

# Bounds and fallback for the quantity-driven service-duration estimate.
_OP_HOURS_MIN = 0.5
_OP_HOURS_MAX = 24.0
_OP_HOURS_FALLBACK = 1.0

# Canonical area unit; an empty work-quantity unit is its legacy alias.
_AREA_UNIT = "ha"
_AREA_LIKE_UNITS = ("", _AREA_UNIT)

# (from_location_ref, to_location_ref) -> travel time in integer seconds.
TravelPairLookup = dict[tuple[str, str], int]
TravelPath = list[tuple[float, float]]
TravelPathLookup = dict[tuple[str, str], TravelPath]
# Per-direct-link cost measures: (network distance km, directed toll EUR).
TravelMeasure = tuple[float, float]
TravelMeasureLookup = dict[tuple[str, str], TravelMeasure]


class ModeAwareTravelLookup(dict[tuple[str, str], int]):
    """Backward-compatible travel lookup with optional per-mode networks.

    The dict itself exposes the aggregate fastest known path, so legacy callers
    that use ``lookup[(from, to)]`` or ``lookup.get(...)`` behave as before.
    Mode-aware callers use ``get_seconds(..., mode)`` to avoid road/air leakage.
    """

    def __init__(
        self,
        aggregate: Optional[Mapping[tuple[str, str], int]] = None,
        by_mode: Optional[Mapping[str, Mapping[tuple[str, str], int]]] = None,
        aggregate_paths: Optional[Mapping[tuple[str, str], TravelPath]] = None,
        paths_by_mode: Optional[Mapping[str, Mapping[tuple[str, str], TravelPath]]] = None,
        aggregate_measures: Optional[Mapping[tuple[str, str], TravelMeasure]] = None,
        measures_by_mode: Optional[Mapping[str, Mapping[tuple[str, str], TravelMeasure]]] = None,
    ) -> None:
        super().__init__(aggregate or {})
        self.by_mode: dict[str, TravelPairLookup] = {
            _normalise_mode(mode): dict(lookup)
            for mode, lookup in (by_mode or {}).items()
        }
        self.aggregate_paths: TravelPathLookup = {
            pair: list(path) for pair, path in (aggregate_paths or {}).items()
        }
        self.paths_by_mode: dict[str, TravelPathLookup] = {
            _normalise_mode(mode): {
                pair: list(path) for pair, path in lookup.items()
            }
            for mode, lookup in (paths_by_mode or {}).items()
        }
        # Direct-link cost measures (distance km, toll EUR), resolved like paths.
        self.aggregate_measures: TravelMeasureLookup = dict(aggregate_measures or {})
        self.measures_by_mode: dict[str, TravelMeasureLookup] = {
            _normalise_mode(mode): dict(lookup)
            for mode, lookup in (measures_by_mode or {}).items()
        }
        # True when any link declares a positive toll, so the routing model can
        # skip building per-vehicle toll matrices for an entirely untolled network.
        self.has_tolls: bool = any(
            toll > 0 for _, toll in self.aggregate_measures.values()
        )

    def get_seconds(
        self,
        from_ref: str,
        to_ref: str,
        travel_mode: Optional[str] = None,
    ) -> Optional[int]:
        mode = _normalise_mode(travel_mode)
        pair = (from_ref, to_ref)
        if mode and mode != "any":
            mode_lookup = self.by_mode.get(mode)
            if mode_lookup and pair in mode_lookup:
                return mode_lookup[pair]
            any_lookup = self.by_mode.get("any")
            if any_lookup and pair in any_lookup:
                return any_lookup[pair]
            return None
        return self.get(pair)

    def get_path(
        self,
        from_ref: str,
        to_ref: str,
        travel_mode: Optional[str] = None,
    ) -> Optional[TravelPath]:
        """Declared or composed route geometry for the selected network path."""
        mode = _normalise_mode(travel_mode)
        pair = (from_ref, to_ref)
        if mode and mode != "any":
            path = self.paths_by_mode.get(mode, {}).get(pair)
            if path is not None:
                return list(path)
            path = self.paths_by_mode.get("any", {}).get(pair)
            return list(path) if path is not None else None
        path = self.aggregate_paths.get(pair)
        return list(path) if path is not None else None

    def get_measure(
        self,
        from_ref: str,
        to_ref: str,
        travel_mode: Optional[str] = None,
    ) -> Optional[TravelMeasure]:
        """Direct-link (distance_km, toll_eur) for one pair, when a link exists."""
        mode = _normalise_mode(travel_mode)
        pair = (from_ref, to_ref)
        if mode and mode != "any":
            measure = self.measures_by_mode.get(mode, {}).get(pair)
            if measure is not None:
                return measure
            return self.measures_by_mode.get("any", {}).get(pair)
        return self.aggregate_measures.get(pair)


TravelLookup = dict[tuple[str, str], int] | ModeAwareTravelLookup


def build_travel_lookup(travel_links: list[Any]) -> ModeAwareTravelLookup:
    """Index travel-link rows by directed location pair (positive times only),
    then close the graph under shortest paths so multi-hop connections count."""
    direct_any: TravelPairLookup = {}
    direct_by_mode: dict[str, TravelPairLookup] = {}
    paths_any: TravelPathLookup = {}
    paths_by_mode: dict[str, TravelPathLookup] = {}
    measures_any: TravelMeasureLookup = {}
    measures_by_mode: dict[str, TravelMeasureLookup] = {}
    for link in travel_links:
        seconds = _nonnegative(link.travel_time_s)
        if seconds <= 0:
            continue
        pair = (str(link.from_location_ref), str(link.to_location_ref))
        value = max(1, int(seconds))
        mode = _normalise_mode(getattr(link, "network_mode", "any"))
        target = direct_any if mode == "any" else direct_by_mode.setdefault(mode, {})
        path_target = paths_any if mode == "any" else paths_by_mode.setdefault(mode, {})
        measure_target = (
            measures_any if mode == "any" else measures_by_mode.setdefault(mode, {})
        )
        existing = target.get(pair)
        if existing is None or value < existing:
            target[pair] = value
            measure_target[pair] = (
                max(0.0, float(getattr(link, "distance_km", 0.0) or 0.0)),
                max(0.0, float(getattr(link, "toll_eur", 0.0) or 0.0)),
            )
            path = _coerce_route_geometry(getattr(link, "route_geometry", None))
            if path and not _route_geometry_matches_distance(
                path, getattr(link, "distance_km", 0.0), getattr(link, "link_id", "")
            ):
                path = []
            if path:
                path_target[pair] = path
            else:
                path_target.pop(pair, None)

    any_lookup, any_paths = _compose_network(direct_any, paths_any)
    by_mode: dict[str, TravelPairLookup] = {"any": any_lookup}
    composed_paths_by_mode: dict[str, TravelPathLookup] = {"any": any_paths}
    for mode, direct in direct_by_mode.items():
        merged = dict(direct_any)
        merged_paths = dict(paths_any)
        for pair, seconds in direct.items():
            existing = merged.get(pair)
            if existing is None or seconds < existing:
                merged[pair] = seconds
                if pair in paths_by_mode.get(mode, {}):
                    merged_paths[pair] = paths_by_mode[mode][pair]
                else:
                    merged_paths.pop(pair, None)
        by_mode[mode], composed_paths_by_mode[mode] = _compose_network(
            merged, merged_paths
        )

    aggregate: TravelPairLookup = {}
    aggregate_paths: TravelPathLookup = {}
    for mode, lookup in by_mode.items():
        for pair, seconds in lookup.items():
            existing = aggregate.get(pair)
            if existing is None or seconds < existing:
                aggregate[pair] = seconds
                path = composed_paths_by_mode.get(mode, {}).get(pair)
                if path is not None:
                    aggregate_paths[pair] = path
                else:
                    aggregate_paths.pop(pair, None)
    aggregate_measures: TravelMeasureLookup = {}
    for mode_measures in measures_by_mode.values():
        aggregate_measures.update(mode_measures)
    aggregate_measures.update(measures_any)
    return ModeAwareTravelLookup(
        aggregate,
        by_mode,
        aggregate_paths,
        composed_paths_by_mode,
        aggregate_measures,
        {"any": measures_any, **measures_by_mode},
    )


def _compose_shortest_paths(direct: TravelPairLookup) -> TravelPairLookup:
    """All-pairs shortest-path closure over the directed link graph.

    One Dijkstra pass per source node; a direct link longer than a composed
    route is replaced by the composed time (the lookup answers "fastest road
    time", not "longest declared edge"). Networks beyond the node cap keep
    the direct lookup only, so an oversized graph degrades gracefully
    instead of stalling the solve.
    """
    return _compose_network(direct, {})[0]


def _compose_network(
    direct: TravelPairLookup,
    direct_paths: TravelPathLookup,
) -> tuple[TravelPairLookup, TravelPathLookup]:
    """Shortest-path closure plus composed geometry when every edge declares it."""
    if not direct:
        return {}, {}
    nodes = sorted({node for pair in direct for node in pair})
    if len(nodes) > TRAVEL_NETWORK_MAX_COMPOSE_NODES:
        logger.warning(
            "Travel network has %d nodes (cap %d); skipping shortest-path "
            "composition, direct links only",
            len(nodes),
            TRAVEL_NETWORK_MAX_COMPOSE_NODES,
        )
        return dict(direct), dict(direct_paths)

    adjacency: dict[str, list[tuple[str, int]]] = {}
    for (from_ref, to_ref), seconds in direct.items():
        adjacency.setdefault(from_ref, []).append((to_ref, seconds))

    composed: TravelPairLookup = {}
    composed_paths: TravelPathLookup = {}
    for source in nodes:
        best: dict[str, int] = {source: 0}
        predecessor: dict[str, str] = {}
        heap: list[tuple[int, str]] = [(0, source)]
        while heap:
            dist, node = heapq.heappop(heap)
            if dist > best.get(node, dist):
                continue
            for neighbor, seconds in adjacency.get(node, []):
                candidate = dist + seconds
                if candidate < best.get(neighbor, candidate + 1):
                    best[neighbor] = candidate
                    predecessor[neighbor] = node
                    heapq.heappush(heap, (candidate, neighbor))
        for target, dist in best.items():
            if target != source:
                composed[(source, target)] = dist
                edges: list[tuple[str, str]] = []
                cursor = target
                while cursor != source and cursor in predecessor:
                    previous = predecessor[cursor]
                    edges.append((previous, cursor))
                    cursor = previous
                edges.reverse()
                if cursor == source and edges and all(
                    edge in direct_paths for edge in edges
                ):
                    path: TravelPath = []
                    for edge in edges:
                        segment = direct_paths[edge]
                        path.extend(segment if not path else segment[1:])
                    composed_paths[(source, target)] = path
    return composed, composed_paths


def _coerce_route_geometry(raw: Any) -> TravelPath:
    """Normalize a canonical/stringified ``[[lat, lon], ...]`` route polyline."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    path: TravelPath = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            continue
        try:
            vertex = (float(pair[0]), float(pair[1]))
        except (TypeError, ValueError):
            continue
        # Drop consecutive duplicates so the polyline carries no zero-length
        # segments; degenerate vertices distort length and detour computations.
        if not path or vertex != path[-1]:
            path.append(vertex)
    return path if len(path) >= 2 else []


def _route_geometry_matches_distance(
    path: TravelPath, distance_km: float, link_id: str
) -> bool:
    """Whether a polyline's traced length is consistent with the link distance.

    A declared route polyline should trace roughly the route its link measures;
    one whose geodesic length dwarfs the declared distance describes a different
    (or malformed) path and is dropped so it never seeds an obstacle detour.
    Skipped when the link declares no positive distance to validate against.
    """
    if not distance_km or distance_km <= 0:
        return True
    traced_km = path_distance_km(path)
    if traced_km <= distance_km * ROUTE_GEOMETRY_MAX_LENGTH_RATIO:
        return True
    logger.warning(
        "Dropping travel-link %s route geometry: traced length %.3f km exceeds "
        "declared distance %.3f km by over %.1fx",
        link_id or "<unknown>",
        traced_km,
        float(distance_km),
        ROUTE_GEOMETRY_MAX_LENGTH_RATIO,
    )
    return False


def vehicle_fallback_speed_kmh(prime: Any) -> float:
    """A prime mover's declared travel speed for the geometric fallback leg.

    Falls back to the engine speed when the mover declares none, so a vehicle
    with no ``travel_speed`` behaves exactly as before. Only the no-network
    (haversine) leg uses this: network links carry vehicle-independent declared
    times, so per-vehicle speed differentiates exactly where the engine has no
    measured time to defer to.
    """
    speed = _nonnegative(getattr(prime, "travel_speed", 0.0))
    return speed if speed > 0 else FALLBACK_TRAVEL_SPEED_KMH


def mode_circuity(travel_mode: Optional[str]) -> float:
    """Fallback circuity multiplier for a travel mode (air direct, ground detours).

    Air flies straight (1.0); road and the unspecified "any" ground default
    share the configurable ground factor, since most movers type as "any".
    """
    return AIR_TRAVEL_CIRCUITY if _normalise_mode(travel_mode) == "air" else GROUND_TRAVEL_CIRCUITY


def _haversine_s(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    speed_kmh: float = FALLBACK_TRAVEL_SPEED_KMH,
    circuity: float = 1.0,
) -> int:
    """Travel time in integer seconds between two lat/lon points.

    Geometric fallback at ``speed_kmh`` (the engine average ground speed by
    default, or a prime mover's declared travel speed), scaled by ``circuity``
    so a ground mover's straight-line estimate reflects real detours; delegates
    to the centralized geodesic helper so all distance math shares one
    implementation.
    """
    base = travel_time_seconds(lat1, lon1, lat2, lon2, speed_kmh)
    return max(1, int(round(base * circuity)))


def travel_seconds(
    from_ref: str,
    to_ref: str,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    travel_lookup: Optional[TravelLookup] = None,
    travel_mode: Optional[str] = None,
    fallback_speed_kmh: float = FALLBACK_TRAVEL_SPEED_KMH,
) -> int:
    """Travel time between two locations: network link first, haversine fallback.

    A missing directed link falls back to the reverse direction (road links
    are usually symmetric) before the geometric estimate. ``fallback_speed_kmh``
    sets the geometric leg's speed (per-vehicle when supplied) and ``travel_mode``
    its circuity (air direct, ground detours); network legs keep their declared,
    vehicle-independent times.
    """
    if travel_lookup and from_ref and to_ref and from_ref != to_ref:
        seconds = _lookup_seconds(
            travel_lookup, from_ref, to_ref, travel_mode
        ) or _lookup_seconds(travel_lookup, to_ref, from_ref, travel_mode)
        if seconds:
            return seconds
    return _haversine_s(
        lat1, lon1, lat2, lon2, fallback_speed_kmh, mode_circuity(travel_mode)
    )


def network_seconds(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str] = None,
) -> Optional[int]:
    """Directed network time for one pair, without reverse or geometry fallback."""
    if not travel_lookup or not from_ref or not to_ref or from_ref == to_ref:
        return None
    return _lookup_seconds(travel_lookup, from_ref, to_ref, travel_mode)


def network_path(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str] = None,
) -> Optional[TravelPath]:
    """Directed network polyline for one pair, when link geometry is declared."""
    if not isinstance(travel_lookup, ModeAwareTravelLookup):
        return None
    return travel_lookup.get_path(from_ref, to_ref, travel_mode)


def _network_measure(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str],
) -> Optional[TravelMeasure]:
    if (
        not isinstance(travel_lookup, ModeAwareTravelLookup)
        or not from_ref
        or not to_ref
        or from_ref == to_ref
    ):
        return None
    return travel_lookup.get_measure(from_ref, to_ref, travel_mode)


def network_distance_km(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str] = None,
) -> Optional[float]:
    """Directed network-link distance (km) for one pair, when a link exists."""
    measure = _network_measure(travel_lookup, from_ref, to_ref, travel_mode)
    if measure is None or measure[0] <= 0:
        return None
    return measure[0]


def network_toll_eur(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str] = None,
) -> Optional[float]:
    """Directed per-link toll (EUR) for one pair, when a travel link exists.

    Returns the link's toll (0.0 for an untolled link) so callers can tell a
    declared-untolled network leg from an off-network leg (None) that should
    fall back to the fleet per-kilometre rate.
    """
    measure = _network_measure(travel_lookup, from_ref, to_ref, travel_mode)
    return None if measure is None else measure[1]


def _lookup_seconds(
    travel_lookup: TravelLookup,
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str] = None,
) -> Optional[int]:
    if isinstance(travel_lookup, ModeAwareTravelLookup):
        return travel_lookup.get_seconds(from_ref, to_ref, travel_mode)
    return travel_lookup.get((from_ref, to_ref))


def travel_network_nodes(travel_lookup: Optional[TravelLookup]) -> set[str]:
    """Every location ref that appears as a node in the travel network.

    Used to map an arbitrary position (a vehicle's current location) onto the
    network: the candidate access points are exactly the refs the lookup knows
    a path for.
    """
    if not travel_lookup:
        return set()
    nodes: set[str] = set()
    if isinstance(travel_lookup, ModeAwareTravelLookup):
        pairs: list[tuple[str, str]] = list(travel_lookup.keys())
        for lookup in travel_lookup.by_mode.values():
            pairs.extend(lookup.keys())
    else:
        pairs = list(travel_lookup.keys())
    for from_ref, to_ref in pairs:
        nodes.add(from_ref)
        nodes.add(to_ref)
    return nodes


def travel_mode_for_vehicle(vehicle: Any) -> str:
    """Resolve a vehicle's travel network mode from type or operations."""
    asset_type = str(getattr(vehicle, "asset_type", "") or "").upper()
    if "UAV" in asset_type:
        return "air"
    if "UGV" in asset_type:
        return "road"
    ops = operation_set(getattr(vehicle, "compatible_operations", []))
    if "UAV_DELIVERY" in ops:
        return "air"
    if "UGV_DELIVERY" in ops:
        return "road"
    return "any"


def travel_mode_for_operation(operation_type: str) -> str:
    op = str(operation_type or "").upper()
    if op.startswith("UAV_") or op == "UAV_DELIVERY":
        return "air"
    if op.startswith("UGV_") or op == "UGV_DELIVERY":
        return "road"
    return "any"


def iter_travel_lookup_items(
    travel_lookup: Optional[TravelLookup],
) -> list[list[Any]]:
    """Stable representation for cache keys."""
    if not travel_lookup:
        return []
    if isinstance(travel_lookup, ModeAwareTravelLookup):
        rows: list[list[Any]] = []
        for mode, lookup in sorted(travel_lookup.by_mode.items()):
            for (from_ref, to_ref), seconds in sorted(lookup.items()):
                rows.append([mode, from_ref, to_ref, int(seconds)])
        return rows
    return [
        ["any", from_ref, to_ref, int(seconds)]
        for (from_ref, to_ref), seconds in sorted(travel_lookup.items())
    ]


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


def _normalise_mode(mode: Optional[str]) -> str:
    value = str(mode or "any").strip().lower()
    return value if value in {"road", "air", "any"} else "any"


def _as_strings(raw: Any) -> set[str]:
    if isinstance(raw, str):
        import ast

        try:
            raw = ast.literal_eval(raw)
        except Exception:
            raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        raw = [raw]
    return {str(item).upper() for item in raw if str(item or "")}


def operation_set(raw: Any) -> set[str]:
    """Return normalized operation codes from lists or stringified lists."""
    return _as_strings(raw)
