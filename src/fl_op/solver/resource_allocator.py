"""Global pre-allocation pass: reserves implements and operators to clusters.

Prevents cross-cluster double-assignment of physical resources (a sprayer cannot
be in two clusters simultaneously).

Algorithm:
  1. Sort clusters by total_penalty_per_day descending (penalty-weighted urgency).
     Tiebreak by cluster_id for determinism.
  2. For each cluster in order:
     a. For each order in the cluster, find all feasible V-I pairs (from compat matrix).
     b. For each physical vehicle_id in the cluster, collapse to the single highest-scoring
        V-I pair that uses an as-yet-unclaimed implement and operator.
     c. Reserve the chosen implement_id and operator_id.
  3. Return clusters with allocated_vehicle_implements populated; clusters with no
     valid allocations retain an empty dict (they will be marked infeasible by solver).
"""

import logging
import math
from typing import Any

import numpy as np

from fl_op.core.constants import (
    MAX_PAIRS_PER_ORDER,
    PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER,
    PREALLOC_ORDERS_PER_RESOURCE,
)
from fl_op.models.types import ClusterSpec

logger = logging.getLogger(__name__)


def _score_vi_pair(
    vehicle: dict[str, Any],
    implement: dict[str, Any],
    order: dict[str, Any],
    power_margin: np.ndarray,
    v_idx: int,
    i_idx: int,
) -> float:
    """Greedy score: positive power_margin percentage (higher headroom = better)."""
    return float(power_margin[v_idx, i_idx])


def allocate_resources(
    clusters: list[ClusterSpec],
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    operators: list[dict[str, Any]],
    compat: np.ndarray,
    power_margin: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    feasible_pairs: dict[str, list[tuple[int, int]]],
) -> list[ClusterSpec]:
    """Mutate clusters in-place with allocated_vehicle_implements; return sorted list.

    allocated_vehicle_implements: {vehicle_id: [implement_id]} (one implement per vehicle).
    """
    # Sort: highest penalty-sum first; tiebreak by cluster_id (lexicographic)
    sorted_clusters = sorted(
        clusters,
        key=lambda c: (-c["total_penalty_per_day"], c["cluster_id"]),
    )

    # Build fast lookup dicts
    vehicle_map: dict[str, dict[str, Any]] = {v["vehicle_id"]: v for v in vehicles}
    implement_map: dict[str, dict[str, Any]] = {im["implement_id"]: im for im in implements}
    # Map vehicle_index inverse: idx -> vehicle_id
    idx_to_vehicle: dict[int, str] = {idx: vid for vid, idx in vehicle_index.items()}
    idx_to_implement: dict[int, str] = {idx: iid for iid, idx in implement_index.items()}

    # Operators indexed by depot for quick assignment
    depot_operators: dict[str, list[dict[str, Any]]] = {}
    for op in operators:
        depot_operators.setdefault(op["depot_id"], []).append(op)

    claimed_implements: set[str] = set()
    claimed_vehicles: set[str] = set()
    claimed_operators: set[str] = set()

    for cluster in sorted_clusters:
        depot_id = cluster["depot_id"]
        cluster_orders = [o for o in orders if o["order_id"] in cluster["order_ids"]]

        # Gather feasible V-I pairs across orders in this cluster, then choose
        # a bounded, diverse set. Do not truncate before filtering claimed
        # resources: pair lists are implement-major, so early truncation can
        # collapse a cluster to one implement and starve later clusters.
        pair_candidates: dict[tuple[str, str], float] = {}

        for order in cluster_orders:
            pairs = feasible_pairs.get(order["order_id"], [])
            for v_idx, i_idx in pairs:
                vid = idx_to_vehicle.get(v_idx)
                iid = idx_to_implement.get(i_idx)
                if vid is None or iid is None:
                    continue
                if vid in claimed_vehicles or iid in claimed_implements:
                    continue
                score = float(power_margin[v_idx, i_idx])
                key = (vid, iid)
                if key not in pair_candidates or score > pair_candidates[key]:
                    pair_candidates[key] = score

        # Pick at most one implement per vehicle and one vehicle per implement.
        # The limit keeps the downstream routing model bounded while still
        # allowing enough resource diversity for multi-order clusters.
        desired_resources = math.ceil(
            len(cluster_orders) / PREALLOC_ORDERS_PER_RESOURCE
        )
        if len(cluster_orders) > 1:
            desired_resources = max(
                desired_resources,
                PREALLOC_MIN_RESOURCES_PER_MULTI_ORDER_CLUSTER,
            )
        cluster_resource_limit = min(
            len(cluster_orders),
            MAX_PAIRS_PER_ORDER,
            desired_resources,
        )
        sorted_candidates = sorted(
            pair_candidates.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
        allocated: dict[str, list[str]] = {}
        reserved_implements: set[str] = set()
        for (vid, iid), _score in sorted_candidates:
            if len(allocated) >= cluster_resource_limit:
                break
            if vid in allocated or iid in reserved_implements:
                continue
            claimed_implements.add(iid)
            claimed_vehicles.add(vid)
            reserved_implements.add(iid)
            allocated[vid] = [iid]

        # Assign one operator per cluster (highest available from depot)
        available_ops = [
            op
            for op in depot_operators.get(depot_id, [])
            if op["operator_id"] not in claimed_operators
        ]
        if not available_ops:
            available_ops = [
                op for op in operators if op["operator_id"] not in claimed_operators
            ]
        if available_ops:
            # Stable assignment: first available (list is deterministic from input order)
            op = available_ops[0]
            claimed_operators.add(op["operator_id"])
            # Store operator assignment in cluster metadata
            cluster["operator_id"] = op["operator_id"]  # type: ignore[typeddict-unknown-key]

        cluster["allocated_vehicle_implements"] = allocated

        if not allocated:
            logger.warning(
                "Cluster %s (%d orders): no implements could be allocated",
                cluster["cluster_id"],
                len(cluster["order_ids"]),
            )
        else:
            logger.debug(
                "Cluster %s: allocated %d vehicles, operator=%s",
                cluster["cluster_id"],
                len(allocated),
                cluster.get("operator_id", "none"),  # type: ignore[call-overload]
            )

    return sorted_clusters
