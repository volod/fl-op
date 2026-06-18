"""Centralized geodesic and geometry helpers for the engine.

This module is the single source of truth for distance, travel-time, and
nearest-neighbor geometry. It wraps three mature libraries so call sites do not
hand-roll trigonometry:

- ``pyproj`` provides the geodesic engine. A module-level ``Geod`` is configured
  as a perfect sphere with mean Earth radius ``EARTH_RADIUS_KM``, so its
  distances match the legacy haversine formula to within floating-point noise
  (~1e-11 km). The same engine handles both scalar and vectorized inputs, which
  removes the duplicated numpy broadcast blocks that used to live in the data
  and solver layers.
- ``shapely`` provides geometry primitives (points, lines, bounding boxes) for
  the map-based interface control work: serializable WGS-84 features that a UI
  layer can consume directly.
- ``scikit-learn`` ``BallTree`` provides the haversine-metric spatial index used
  for nearest-depot assignment.

Coordinate convention: callers pass ``(lat, lon)`` in degrees. Shapely geometry
uses ``(x=lon, y=lat)`` so emitted features are GeoJSON/map friendly.
"""

import heapq
import math

import numpy as np
from pyproj import Geod
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from sklearn.neighbors import BallTree

from fl_op.core.constants import (
    EARTH_RADIUS_KM,
    FALLBACK_TRAVEL_SPEED_KMH,
    METERS_PER_DEGREE_LAT,
)

# Spherical geodesic engine: a sphere of mean Earth radius reproduces the legacy
# haversine results while giving us pyproj's vectorized, well-tested inverse
# solver. Radius is expressed in meters for pyproj; distances are returned in
# meters and converted to km at the boundary.
_SPHERE_RADIUS_M = EARTH_RADIUS_KM * 1000.0
_GEOD_SPHERE = Geod(a=_SPHERE_RADIUS_M, b=_SPHERE_RADIUS_M)

_SECONDS_PER_HOUR = 3600.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points (spherical Earth)."""
    _, _, dist_m = _GEOD_SPHERE.inv(lon1, lat1, lon2, lat2)
    return float(dist_m) / 1000.0


def haversine_km_vector(
    lats1: np.ndarray | float,
    lons1: np.ndarray | float,
    lats2: np.ndarray | float,
    lons2: np.ndarray | float,
) -> np.ndarray:
    """Vectorized great-circle distances in km, broadcasting scalar endpoints.

    Any argument may be a scalar or array; all four are broadcast to a common
    shape, so distances from many points to one point (or pairwise) compute in
    a single call.
    """
    lons1_b, lats1_b, lons2_b, lats2_b = (
        np.ascontiguousarray(arr, dtype=float)
        for arr in np.broadcast_arrays(
            np.asarray(lons1, dtype=float),
            np.asarray(lats1, dtype=float),
            np.asarray(lons2, dtype=float),
            np.asarray(lats2, dtype=float),
        )
    )
    _, _, dist_m = _GEOD_SPHERE.inv(lons1_b, lats1_b, lons2_b, lats2_b)
    return np.asarray(dist_m, dtype=float) / 1000.0


def travel_time_seconds(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    speed_kmh: float = FALLBACK_TRAVEL_SPEED_KMH,
) -> int:
    """Geometric travel time in integer seconds at ``speed_kmh`` (min 1 s).

    Used as the fallback leg duration when no travel-network link connects the
    pair. ``speed_kmh`` defaults to the engine fallback speed but can be passed
    per-vehicle to differentiate genuinely faster movers.
    """
    safe_speed = speed_kmh if speed_kmh > 0 else FALLBACK_TRAVEL_SPEED_KMH
    seconds_per_km = _SECONDS_PER_HOUR / safe_speed
    return max(1, int(haversine_km(lat1, lon1, lat2, lon2) * seconds_per_km))


def nearest_indices(
    query_lats: np.ndarray,
    query_lons: np.ndarray,
    ref_lats: np.ndarray,
    ref_lons: np.ndarray,
) -> np.ndarray:
    """Index of the nearest reference point for each query point (haversine).

    Returns an integer array of length ``len(query_lats)`` indexing into the
    reference arrays. Uses a haversine-metric BallTree, so it scales to large
    point sets without an all-pairs distance matrix.
    """
    ref_coords = np.radians(np.column_stack([ref_lats, ref_lons]))
    query_coords = np.radians(np.column_stack([query_lats, query_lons]))
    tree = BallTree(ref_coords, metric="haversine")
    _, indices = tree.query(query_coords, k=1)
    return indices[:, 0]


# ---------------------------------------------------------------------------
# Map-interface geometry primitives (shapely, WGS-84, x=lon / y=lat)
# ---------------------------------------------------------------------------


def to_point(lat: float, lon: float) -> Point:
    """WGS-84 point with map/GeoJSON axis order (x=lon, y=lat)."""
    return Point(lon, lat)


def to_linestring(latlon_coords: list[tuple[float, float]]) -> LineString:
    """Polyline from ``(lat, lon)`` pairs, emitted as map-order (lon, lat)."""
    return LineString([(lon, lat) for lat, lon in latlon_coords])


def segment_min_distance_m(
    seg_a: tuple[tuple[float, float], tuple[float, float]],
    seg_b: tuple[tuple[float, float], tuple[float, float]],
) -> float:
    """Minimum distance in meters between two short ``(lat, lon)`` segments.

    Each segment is a pair of ``(lat, lon)`` endpoints. Both are projected into
    a shared local east-north meter frame (longitude scaled by ``cos(lat0)``)
    centered on their combined centroid, then shapely measures the segment-to-
    segment distance. This is the lateral separation used for UAV vehicle-to-
    vehicle deconfliction; the local planar projection is accurate for the small
    regions airspace separation operates over. A degenerate (zero-length)
    segment is treated as a point.
    """
    pts = [seg_a[0], seg_a[1], seg_b[0], seg_b[1]]
    lat0 = sum(lat for lat, _ in pts) / len(pts)
    lon0 = sum(lon for _, lon in pts) / len(pts)
    scale = max(1e-6, math.cos(math.radians(lat0)))

    def proj(point: tuple[float, float]) -> tuple[float, float]:
        lat, lon = point
        return (
            (lon - lon0) * METERS_PER_DEGREE_LAT * scale,
            (lat - lat0) * METERS_PER_DEGREE_LAT,
        )

    def geom(seg: tuple[tuple[float, float], tuple[float, float]]):
        p0, p1 = proj(seg[0]), proj(seg[1])
        return Point(p0) if p0 == p1 else LineString([p0, p1])

    return float(geom(seg_a).distance(geom(seg_b)))


def shortest_path_around_polygons(
    start: tuple[float, float],
    end: tuple[float, float],
    restricted_polygons: list[list[tuple[float, float]]],
) -> list[tuple[float, float]]:
    """Shortest visible path from ``start`` to ``end`` around polygon interiors.

    Points and polygon vertices use the public ``(lat, lon)`` convention. The
    visibility graph contains the endpoints and every obstacle vertex; an edge
    is admitted only when its interior does not enter a restricted polygon.
    Geodesic distance weights the graph, while Shapely performs the visibility
    predicates in map-order coordinates. Polygon boundaries are traversable,
    which lets a shortest path follow obstacle edges without entering them.

    An empty result means an endpoint lies inside a restricted polygon or no
    visible path exists. Callers can then treat the route as infeasible or use a
    domain-specific fallback.
    """
    polygons: list[Polygon] = []
    for ring in restricted_polygons:
        if len(ring) < 3:
            continue
        polygon = Polygon([(lon, lat) for lat, lon in ring])
        repaired = polygon if polygon.is_valid else polygon.buffer(0)
        candidates = [repaired] if isinstance(repaired, Polygon) else [
            geom for geom in repaired.geoms if isinstance(geom, Polygon)
        ]
        polygons.extend(
            candidate
            for candidate in candidates
            if not candidate.is_empty and candidate.area > 0
        )
    if not polygons or start == end:
        return [start, end]

    start_point = Point(start[1], start[0])
    end_point = Point(end[1], end[0])
    if any(poly.contains(start_point) or poly.contains(end_point) for poly in polygons):
        return []

    direct = LineString([(start[1], start[0]), (end[1], end[0])])
    if all(direct.relate_pattern(poly, "F********") for poly in polygons):
        return [start, end]

    points = [start, end]
    seen = {start, end}
    for polygon in polygons:
        for lon, lat in list(polygon.exterior.coords)[:-1]:
            point = (float(lat), float(lon))
            if point not in seen:
                seen.add(point)
                points.append(point)

    adjacency: list[list[tuple[int, float]]] = [[] for _ in points]
    for left in range(len(points)):
        for right in range(left + 1, len(points)):
            segment = LineString(
                [
                    (points[left][1], points[left][0]),
                    (points[right][1], points[right][0]),
                ]
            )
            if not all(segment.relate_pattern(poly, "F********") for poly in polygons):
                continue
            distance = haversine_km(*points[left], *points[right])
            adjacency[left].append((right, distance))
            adjacency[right].append((left, distance))

    best = [math.inf] * len(points)
    predecessor = [-1] * len(points)
    best[0] = 0.0
    queue: list[tuple[float, int]] = [(0.0, 0)]
    while queue:
        distance, node = heapq.heappop(queue)
        if distance > best[node]:
            continue
        if node == 1:
            break
        for neighbor, edge_distance in adjacency[node]:
            candidate = distance + edge_distance
            if candidate < best[neighbor]:
                best[neighbor] = candidate
                predecessor[neighbor] = node
                heapq.heappush(queue, (candidate, neighbor))

    if not math.isfinite(best[1]):
        return []
    path_indices = [1]
    while path_indices[-1] != 0:
        path_indices.append(predecessor[path_indices[-1]])
    return [points[index] for index in reversed(path_indices)]


def path_distance_km(path: list[tuple[float, float]]) -> float:
    """Geodesic length of a ``(lat, lon)`` polyline in kilometres."""
    return sum(haversine_km(*start, *end) for start, end in zip(path, path[1:]))


def reroute_path_around_polygons(
    path: list[tuple[float, float]],
    restricted_polygons: list[list[tuple[float, float]]],
) -> list[tuple[float, float]]:
    """Replace each blocked polyline segment with its obstacle-avoiding path."""
    if len(path) < 2 or not restricted_polygons:
        return list(path)
    rerouted = [path[0]]
    for start, end in zip(path, path[1:]):
        segment_path = shortest_path_around_polygons(
            start, end, restricted_polygons
        )
        if not segment_path:
            return []
        rerouted.extend(segment_path[1:])
    return rerouted


def unrestricted_area_fraction(
    site_polygon: list[tuple[float, float]],
    restricted_polygons: list[list[tuple[float, float]]],
) -> float:
    """Fraction of a site polygon's area not covered by restricted areas.

    Rings are ``(x=lon, y=lat)`` coordinate lists (as ``parse_polygon`` emits).
    Returns 1.0 when there is no overlap (or the site has no positive area) and
    0.0 when restricted areas fully cover the site. Planar area in degree units
    is sufficient here because the result is a ratio over a small region, so the
    latitude-scale distortion cancels.
    """
    if len(site_polygon) < 3 or not restricted_polygons:
        return 1.0
    site = Polygon(site_polygon)
    if not site.is_valid:
        site = site.buffer(0)
    total = site.area
    if total <= 0:
        return 1.0
    polys = []
    for ring in restricted_polygons:
        if len(ring) < 3:
            continue
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        polys.append(poly)
    if not polys:
        return 1.0
    covered = site.intersection(unary_union(polys)).area
    return max(0.0, min(1.0, (total - covered) / total))


def swept_polygon(
    path_latlon: list[tuple[float, float]],
    width_m: float,
) -> list[tuple[float, float]]:
    """Coverage swath of one execution pass: a path swept by an implement width.

    ``path_latlon`` is the pass centreline as ``(lat, lon)`` points (a single
    point is allowed: a spot coverage). The path is buffered by half the swath
    width, returning the covered polygon ring as ``(x=lon, y=lat)`` points
    matching ``parse_polygon`` output. The buffer is computed in a longitude
    space scaled by ``cos(latitude)`` so the swath is metrically round in both
    directions rather than stretched east-west away from the equator; the
    half-width in degrees uses the mean meters-per-degree of latitude. Returns
    an empty list for an empty path or a non-positive width.
    """
    if width_m <= 0 or not path_latlon:
        return []
    lat0 = sum(lat for lat, _ in path_latlon) / len(path_latlon)
    scale = max(1e-6, math.cos(math.radians(lat0)))
    half_deg = (width_m / 2.0) / METERS_PER_DEGREE_LAT
    scaled = [(lon * scale, lat) for lat, lon in path_latlon]
    base = Point(scaled[0]) if len(scaled) == 1 else LineString(scaled)
    buffered = base.buffer(half_deg)
    if buffered.is_empty:
        return []
    return [(x / scale, y) for x, y in buffered.exterior.coords]


def polygon_rings_area_km2(rings: list[list[tuple[float, float]]]) -> float:
    """Geodesic area (km2) of the union of polygon rings (``(x=lon, y=lat)``).

    Overlapping rings are merged before measuring, so passes that cover the
    same ground are not double-counted -- the reason coverage is tracked as
    geometry rather than a running sum of per-pass areas. Returns 0.0 when no
    ring has a positive area.
    """
    polys = []
    for ring in rings:
        if len(ring) < 3:
            continue
        poly = Polygon(ring)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        polys.append(poly)
    if not polys:
        return 0.0
    area_m2, _ = _GEOD_SPHERE.geometry_area_perimeter(unary_union(polys))
    return abs(float(area_m2)) / 1.0e6


def bounding_box(
    lats: np.ndarray | list[float],
    lons: np.ndarray | list[float],
):
    """Axis-aligned bounding box ``(min_lon, min_lat, max_lon, max_lat)``.

    Returned as a shapely polygon suitable for map viewport fitting and
    spatial filtering.
    """
    lat_arr = np.asarray(lats, dtype=float)
    lon_arr = np.asarray(lons, dtype=float)
    return box(
        float(lon_arr.min()),
        float(lat_arr.min()),
        float(lon_arr.max()),
        float(lat_arr.max()),
    )
