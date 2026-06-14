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

import numpy as np
from pyproj import Geod
from shapely.geometry import LineString, Point, box
from sklearn.neighbors import BallTree

from fl_op.core.constants import EARTH_RADIUS_KM, FALLBACK_TRAVEL_SPEED_KMH

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
