"""Obstacle-aware and time-dependent geography for routing arcs."""

import dataclasses
import logging
from typing import Any, Optional

from fl_op.core.constants import (
    FALLBACK_TRAVEL_SPEED_KMH,
    ROUTE_GEOMETRY_ENDPOINT_TOLERANCE_KM,
    ROUTING_HORIZON_S,
)
from fl_op.core.geometry import (
    haversine_km,
    path_distance_km,
    reroute_path_around_polygons,
)
from fl_op.solver.restrictions import parse_polygon
from fl_op.solver.task_relations import parse_time_windows
from fl_op.solver.travel_time import (
    TravelLookup,
    mode_circuity,
    network_path,
    network_seconds,
    operation_set,
    travel_seconds,
)

logger = logging.getLogger(__name__)

LatLon = tuple[float, float]
PolygonRing = list[LatLon]


@dataclasses.dataclass(frozen=True, slots=True)
class RouteRestriction:
    """One operation-compatible polygon and its active epoch intervals."""

    polygon: PolygonRing
    # Empty means always active; otherwise closed epoch-second intervals.
    windows: tuple[tuple[int, int], ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class RestrictionSegment:
    """A horizon sub-interval over which the active-polygon set is constant.

    Offsets are seconds from the planning origin (``now_epoch``); the interval is
    half-open ``[start_offset_s, end_offset_s)``. ``polygons`` is the full set of
    polygons (unconditional plus timed-active) in force throughout the segment.
    """

    start_offset_s: int
    end_offset_s: int
    polygons: tuple[PolygonRing, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class ArcRoute:
    """Travel duration and concrete path selected for one routing arc."""

    seconds: int
    path: tuple[LatLon, ...]
    detoured: bool = False


def route_restrictions_for_vehicle(
    locations: list[Any],
    routing_vehicle: dict[str, Any],
    now_epoch: int,
    horizon_s: int = ROUTING_HORIZON_S,
) -> list[RouteRestriction]:
    """Operation-compatible polygons with clamped route-activation windows."""
    operations = _bundle_operations(routing_vehicle)
    if not operations:
        return []
    horizon_end = now_epoch + horizon_s
    restrictions: list[RouteRestriction] = []
    for location in locations:
        prohibited = operation_set(getattr(location, "restricted_operations", []))
        if not operations.intersection(prohibited):
            continue
        polygon = _location_polygon(location)
        if not polygon:
            continue
        parsed_windows = parse_time_windows(
            getattr(location, "restriction_windows", None)
        )
        windows: list[tuple[int, int]] = []
        for start, end in parsed_windows:
            start_epoch = max(now_epoch, int(start.timestamp()))
            end_epoch = horizon_end if end is None else min(
                horizon_end, int(end.timestamp())
            )
            if end_epoch >= start_epoch:
                windows.append((start_epoch, end_epoch))
        # Declared windows all outside the planning horizon mean inactive, not
        # unconditional. No declared windows means the polygon is always active.
        if parsed_windows and not windows:
            continue
        restrictions.append(RouteRestriction(polygon, tuple(windows)))
    return restrictions


def restricted_polygons_for_vehicle(
    locations: list[Any],
    routing_vehicle: dict[str, Any],
) -> list[PolygonRing]:
    """All compatible polygons, retained as a backward-compatible helper."""
    operations = _bundle_operations(routing_vehicle)
    if not operations:
        return []
    polygons: list[PolygonRing] = []
    for location in locations:
        prohibited = operation_set(getattr(location, "restricted_operations", []))
        polygon = _location_polygon(location)
        if operations.intersection(prohibited) and polygon:
            polygons.append(polygon)
    return polygons


def unconditional_polygons(
    restrictions: list[RouteRestriction],
) -> list[PolygonRing]:
    """Polygons with no activation windows."""
    return [restriction.polygon for restriction in restrictions if not restriction.windows]


def active_polygons(
    restrictions: list[RouteRestriction],
    start_epoch: int,
    end_epoch: int,
) -> list[PolygonRing]:
    """Polygons active during any part of a modeled arc-occupancy interval."""
    polygons: list[PolygonRing] = []
    for restriction in restrictions:
        if not restriction.windows or any(
            start_epoch <= window_end and end_epoch >= window_start
            for window_start, window_end in restriction.windows
        ):
            polygons.append(restriction.polygon)
    return polygons


def horizon_restriction_segments(
    restrictions: list[RouteRestriction],
    now_epoch: int,
    horizon_s: int = ROUTING_HORIZON_S,
) -> list[RestrictionSegment]:
    """Partition ``[0, horizon_s)`` into intervals with a constant active set.

    Cut points are every timed-window edge clamped to the horizon; within each
    resulting interval the active-polygon set (unconditional plus any timed
    window covering it) does not change. Always returns at least one segment
    spanning the whole horizon. The segments are the time buckets a single-pass
    time-expanded model replicates nodes across.
    """
    cuts = {0, horizon_s}
    for restriction in restrictions:
        for window_start, window_end in restriction.windows:
            for edge in (window_start - now_epoch, window_end - now_epoch + 1):
                if 0 < edge < horizon_s:
                    cuts.add(edge)
    ordered = sorted(cuts)
    segments: list[RestrictionSegment] = []
    for start_offset, end_offset in zip(ordered, ordered[1:]):
        instant = now_epoch + start_offset
        polygons = tuple(active_polygons(restrictions, instant, instant))
        segments.append(
            RestrictionSegment(start_offset, end_offset, polygons)
        )
    return segments


def arc_route(
    from_ref: str,
    to_ref: str,
    start: LatLon,
    end: LatLon,
    travel_lookup: Optional[TravelLookup],
    travel_mode: Optional[str],
    fallback_speed_kmh: float,
    restricted_polygons: list[PolygonRing],
) -> ArcRoute:
    """Resolve network/fallback travel and reroute declared geometry if blocked."""
    network_time, declared_path = _network_route(
        travel_lookup, from_ref, to_ref, travel_mode
    )
    if network_time is not None:
        # A temporal matrix entry without geometry remains usable but cannot be
        # spatially rewritten. Geometry-bearing links are validated below.
        if not declared_path:
            return ArcRoute(network_time, (start, end))
        if not _endpoints_match(declared_path, start, end):
            # Declared geometry whose ends do not meet this arc's endpoints is
            # not topology-trustworthy for the pair (mismatched ref, wrong
            # direction, stale coordinates). Ignore it and route the straight
            # network arc, which is still obstacle-rerouted below.
            logger.warning(
                "Ignoring travel-link route geometry for %s->%s: endpoints "
                "diverge from the arc by more than %.3f km",
                from_ref,
                to_ref,
                ROUTE_GEOMETRY_ENDPOINT_TOLERANCE_KM,
            )
            declared_path = [start, end]
        rerouted = reroute_path_around_polygons(
            declared_path, restricted_polygons
        )
        if not rerouted:
            return ArcRoute(network_time, tuple(declared_path))
        base_distance = path_distance_km(declared_path)
        rerouted_distance = path_distance_km(rerouted)
        ratio = rerouted_distance / base_distance if base_distance > 0 else 1.0
        seconds = max(network_time, int(round(network_time * ratio)))
        return ArcRoute(
            max(1, seconds),
            tuple(rerouted),
            rerouted != declared_path,
        )

    base_seconds = travel_seconds(
        from_ref,
        to_ref,
        *start,
        *end,
        travel_lookup,
        travel_mode,
        fallback_speed_kmh,
    )
    base_path = [start, end]
    rerouted = reroute_path_around_polygons(base_path, restricted_polygons)
    if not rerouted:
        return ArcRoute(base_seconds, tuple(base_path))
    speed = (
        fallback_speed_kmh
        if fallback_speed_kmh > 0
        else FALLBACK_TRAVEL_SPEED_KMH
    )
    seconds = (
        path_distance_km(rerouted)
        / speed
        * 3600.0
        * mode_circuity(travel_mode)
    )
    return ArcRoute(
        max(1, int(round(seconds))),
        tuple(rerouted),
        rerouted != base_path,
    )


def obstacle_aware_travel_seconds(
    from_ref: str,
    to_ref: str,
    start: LatLon,
    end: LatLon,
    travel_lookup: Optional[TravelLookup],
    travel_mode: Optional[str],
    fallback_speed_kmh: float,
    restricted_polygons: list[PolygonRing],
) -> int:
    """Compatibility wrapper returning only an obstacle-aware arc duration."""
    return arc_route(
        from_ref,
        to_ref,
        start,
        end,
        travel_lookup,
        travel_mode,
        fallback_speed_kmh,
        restricted_polygons,
    ).seconds


def detour_waypoints(
    from_ref: str,
    to_ref: str,
    start: LatLon,
    end: LatLon,
    travel_lookup: Optional[TravelLookup],
    travel_mode: Optional[str],
    restricted_polygons: list[PolygonRing],
) -> list[LatLon]:
    """Intermediate vertices from declared geometry and obstacle rerouting."""
    route = arc_route(
        from_ref,
        to_ref,
        start,
        end,
        travel_lookup,
        travel_mode,
        FALLBACK_TRAVEL_SPEED_KMH,
        restricted_polygons,
    )
    return list(route.path[1:-1])


def _bundle_operations(routing_vehicle: dict[str, Any]) -> set[str]:
    prime_operations = operation_set(
        getattr(routing_vehicle.get("prime"), "compatible_operations", [])
    )
    related_operations = operation_set(
        getattr(routing_vehicle.get("related"), "compatible_operations", [])
    )
    if prime_operations and related_operations:
        return prime_operations.intersection(related_operations)
    return prime_operations or related_operations


def _location_polygon(location: Any) -> PolygonRing:
    # restrictions.parse_polygon normalizes canonical [lat, lon] vertices to
    # map-order (lon, lat); convert back to this module's public order.
    polygon = parse_polygon(getattr(location, "polygon", None))
    return [(lat, lon) for lon, lat in polygon]


def _endpoints_match(path: list[LatLon], start: LatLon, end: LatLon) -> bool:
    """Whether a declared polyline's ends meet the arc's endpoint coordinates."""
    head = path[0]
    tail = path[-1]
    return (
        haversine_km(start[0], start[1], head[0], head[1])
        <= ROUTE_GEOMETRY_ENDPOINT_TOLERANCE_KM
        and haversine_km(end[0], end[1], tail[0], tail[1])
        <= ROUTE_GEOMETRY_ENDPOINT_TOLERANCE_KM
    )


def _network_route(
    travel_lookup: Optional[TravelLookup],
    from_ref: str,
    to_ref: str,
    travel_mode: Optional[str],
) -> tuple[Optional[int], Optional[list[LatLon]]]:
    seconds = network_seconds(travel_lookup, from_ref, to_ref, travel_mode)
    if seconds is not None:
        return seconds, network_path(travel_lookup, from_ref, to_ref, travel_mode)
    seconds = network_seconds(travel_lookup, to_ref, from_ref, travel_mode)
    if seconds is None:
        return None, None
    path = network_path(travel_lookup, to_ref, from_ref, travel_mode)
    return seconds, list(reversed(path)) if path else None
