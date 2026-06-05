"""Pre-filter, geographic clustering, and ClusterSpec construction.

Pipeline:
  1. Power + OperationType compatibility filter (vectorised over compat matrix).
  2. Haversine BallTree depot-affinity clustering: each order is assigned to the
     nearest depot; orders within a depot group are split into sub-clusters of
     CLUSTER_TARGET_SIZE.
  3. Returns a list of ClusterSpec TypedDicts ready for solver/allocation.
"""

import logging
from typing import Any

import numpy as np
from sklearn.neighbors import BallTree

from fl_op.core.constants import CLUSTER_TARGET_SIZE
from fl_op.models.enums import OperationType
from fl_op.models.types import ClusterSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compatibility filter
# ---------------------------------------------------------------------------


def filter_feasible_vehicle_implement_pairs(
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, list[tuple[int, int]]]:
    """Return {order_id: [(v_idx, i_idx), ...]} for all compatible V-I pairs.

    A pair is feasible when:
      - compat[v_idx, i_idx] is True (power margin within threshold)
      - The implement's compatible_operations includes the order's operation_type
    """
    # Build a lookup: implement_id -> set of OperationType values
    impl_ops: dict[str, set[str]] = {}
    for im in implements:
        ops_raw = im.get("compatible_operations", [])
        if isinstance(ops_raw, str):
            # CSV stores lists as stringified Python lists; parse conservatively
            import ast

            try:
                ops_raw = ast.literal_eval(ops_raw)
            except Exception:
                ops_raw = [ops_raw]
        impl_ops[im["implement_id"]] = set(ops_raw)

    feasible: dict[str, list[tuple[int, int]]] = {}
    for order in orders:
        op = order["operation_type"]
        oid = order["order_id"]
        pairs: list[tuple[int, int]] = []
        for im in implements:
            if op not in impl_ops.get(im["implement_id"], set()):
                continue
            i_idx = implement_index.get(im["implement_id"])
            if i_idx is None:
                continue
            for v in vehicles:
                v_idx = vehicle_index.get(v["vehicle_id"])
                if v_idx is None:
                    continue
                if compat[v_idx, i_idx]:
                    pairs.append((v_idx, i_idx))
        feasible[oid] = pairs

    n_feasible = sum(len(p) for p in feasible.values())
    logger.debug(
        "Feasibility filter: %d orders, %d total V-I pairs retained",
        len(orders),
        n_feasible,
    )
    return feasible


# ---------------------------------------------------------------------------
# Haversine BallTree depot-affinity clustering
# ---------------------------------------------------------------------------


def cluster_orders_by_depot(
    orders: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Assign each order to the nearest depot; return {depot_id: [order_ids]}.

    Uses sklearn BallTree with haversine metric on field centroids.
    """
    field_map: dict[str, dict[str, Any]] = {f["field_id"]: f for f in fields}

    depot_ids = [d["depot_id"] for d in depots]
    depot_coords = np.radians(
        np.array([[float(d["lat"]), float(d["lon"])] for d in depots])
    )
    tree = BallTree(depot_coords, metric="haversine")

    assignment: dict[str, list[str]] = {did: [] for did in depot_ids}
    for order in orders:
        field = field_map.get(order["field_id"])
        if field is None:
            logger.warning("Order %s has no matching field; skipping", order["order_id"])
            continue
        lat = float(field.get("centroid_lat", 0))
        lon = float(field.get("centroid_lon", 0))
        coords = np.radians([[lat, lon]])
        _, indices = tree.query(coords, k=1)
        nearest_depot = depot_ids[indices[0][0]]
        assignment[nearest_depot].append(order["order_id"])

    return assignment


def _split_into_subclusters(
    order_ids: list[str],
    target_size: int,
) -> list[list[str]]:
    """Split a flat list of order_ids into sub-lists of approximately target_size."""
    if not order_ids:
        return []
    n = len(order_ids)
    n_clusters = max(1, round(n / target_size))
    chunk = max(1, n // n_clusters)
    chunks = [order_ids[i : i + chunk] for i in range(0, n, chunk)]
    return chunks


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_cluster_specs(
    orders: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    order_index: dict[str, dict[str, Any]] | None = None,
) -> list[ClusterSpec]:
    """Produce ClusterSpec list from raw entity dicts and compat matrix.

    Steps:
      1. Depot-affinity clustering via haversine BallTree.
      2. Sub-cluster each depot group to CLUSTER_TARGET_SIZE.
      3. Compute total_penalty_per_day for priority sorting.
      4. Initialise allocated_vehicle_implements to empty (filled by allocation).
    """
    if order_index is None:
        order_index = {o["order_id"]: o for o in orders}

    depot_assignment = cluster_orders_by_depot(orders, fields, depots)

    clusters: list[ClusterSpec] = []
    cluster_seq = 0
    for depot_id, oid_list in depot_assignment.items():
        if not oid_list:
            continue
        subclusters = _split_into_subclusters(oid_list, CLUSTER_TARGET_SIZE)
        for sub in subclusters:
            total_penalty = sum(
                float(order_index[oid].get("penalty_per_day_eur", 0.0)) for oid in sub
            )
            spec: ClusterSpec = {
                "cluster_id": f"cluster_{cluster_seq:06d}",
                "depot_id": depot_id,
                "order_ids": sub,
                "allocated_vehicle_implements": {},
                "total_penalty_per_day": total_penalty,
            }
            clusters.append(spec)
            cluster_seq += 1

    logger.info(
        "Clustering: %d orders -> %d clusters across %d depots",
        len(orders),
        len(clusters),
        len([d for d in depot_assignment if depot_assignment[d]]),
    )
    return clusters
