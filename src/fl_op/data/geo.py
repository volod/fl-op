"""Geographic sampling helpers for synthetic dataset generation."""

import numpy as np
from sklearn.neighbors import BallTree

from fl_op.core.constants import EARTH_RADIUS_KM

_REGION_CENTER_LAT = 48.5  # Central Ukraine approximate centroid
_REGION_CENTER_LON = 32.0
_REGION_RADIUS_KM = 400.0


def _random_points_in_circle(
    rng: np.random.Generator,
    n: int,
    center_lat: float,
    center_lon: float,
    radius_km: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (lats, lons) arrays of n points uniformly sampled inside a circle."""
    r = radius_km * np.sqrt(rng.uniform(0, 1, n))
    theta = rng.uniform(0, 2 * np.pi, n)
    d_lat = np.degrees(r / EARTH_RADIUS_KM) * np.cos(theta)
    d_lon = (
        np.degrees(r / EARTH_RADIUS_KM)
        * np.sin(theta)
        / np.cos(np.radians(center_lat))
    )
    return center_lat + d_lat, center_lon + d_lon


def _nearest_depot_ids(
    field_lats: np.ndarray,
    field_lons: np.ndarray,
    depot_lats: np.ndarray,
    depot_lons: np.ndarray,
    depot_ids: list[str],
) -> list[str]:
    """Return the nearest depot_id for each field centroid using haversine BallTree."""
    depot_coords = np.radians(np.column_stack([depot_lats, depot_lons]))
    field_coords = np.radians(np.column_stack([field_lats, field_lons]))
    tree = BallTree(depot_coords, metric="haversine")
    _, indices = tree.query(field_coords, k=1)
    return [depot_ids[idx[0]] for idx in indices]
