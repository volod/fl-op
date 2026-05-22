"""Global pre-allocation pass: reserves implements and operators to clusters.

Prevents cross-cluster double-booking of implements (a sprayer cannot be at two
fields simultaneously). Vehicles are assigned using a two-pass strategy:

  Pass 1 (preferred): only vehicles not yet assigned to any cluster.
  Pass 2 (fallback):  allow vehicles already in one cluster, up to
                      _MAX_VEHICLE_ASSIGNMENTS per vehicle.

The fallback applies only when no unassigned vehicle is compatible with a
cluster's operation type. Since each cluster solver runs independently in
parallel, shared vehicles risk scheduling conflicts; the fallback keeps that
risk contained to compatibility-constrained edge cases.

Algorithm:
  1. Sort clusters by total_penalty_per_day descending (penalty-weighted urgency).
     Tiebreak by cluster_id for determinism.
  2. For each cluster in order:
     a. Collect feasible V-I pairs (from compat matrix), skipping claimed implements.
     b. Run pass-1 to prefer unassigned vehicles; run pass-2 if pass-1 yields nothing.
     c. Select the highest-scoring bounded subset (cluster_resource_limit).
     d. Reserve chosen implement_ids globally; track vehicle assignment counts.
  3. Return clusters with allocated_vehicle_implements populated.
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

    # Implements are exclusively claimed: one implement per cluster across the run.
    # Vehicles use a two-pass assignment: prefer unassigned vehicles (pass 1),
    # fall back to limited reuse (pass 2) only when compatibility leaves no choice.
    _MAX_VEHICLE_ASSIGNMENTS = 2

    claimed_implements: set[str] = set()
    claimed_operators: set[str] = set()
    vehicle_assignment_count: dict[str, int] = {}

    for cluster in sorted_clusters:
        depot_id = cluster["depot_id"]
        cluster_orders = [o for o in orders if o["order_id"] in cluster["order_ids"]]

        def _collect_pairs(max_vehicle_uses: int) -> dict[tuple[str, str], float]:
            candidates: dict[tuple[str, str], float] = {}
            for order in cluster_orders:
                for v_idx, i_idx in feasible_pairs.get(order["order_id"], []):
                    vid = idx_to_vehicle.get(v_idx)
                    iid = idx_to_implement.get(i_idx)
                    if vid is None or iid is None:
                        continue
                    if iid in claimed_implements:
                        continue
                    if vehicle_assignment_count.get(vid, 0) >= max_vehicle_uses:
                        continue
                    score = float(power_margin[v_idx, i_idx])
                    key = (vid, iid)
                    if key not in candidates or score > candidates[key]:
                        candidates[key] = score
            return candidates

        # Pass 1: unassigned vehicles only (0 existing cluster assignments)
        pair_candidates = _collect_pairs(max_vehicle_uses=1)
        # Pass 2: allow limited reuse when no unassigned vehicle is compatible
        if not pair_candidates:
            pair_candidates = _collect_pairs(max_vehicle_uses=_MAX_VEHICLE_ASSIGNMENTS)

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
        reserved_vehicles: set[str] = set()
        reserved_implements: set[str] = set()
        for (vid, iid), _score in sorted_candidates:
            if len(allocated) >= cluster_resource_limit:
                break
            if vid in reserved_vehicles or iid in reserved_implements:
                continue
            claimed_implements.add(iid)
            vehicle_assignment_count[vid] = vehicle_assignment_count.get(vid, 0) + 1
            reserved_vehicles.add(vid)
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
