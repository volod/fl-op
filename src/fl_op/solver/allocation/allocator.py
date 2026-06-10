"""Global pre-allocation pass: reserves implements and operators to clusters."""

import logging
from typing import Any

import numpy as np

from fl_op.solver.types import ClusterSpec
from fl_op.solver.allocation.candidates import (
    collect_pair_candidates,
    reserve_best_candidates,
)
from fl_op.solver.allocation.limits import cluster_resource_limit
from fl_op.solver.allocation.operators import assign_operator, index_operators_by_depot
from fl_op.solver.allocation.scoring import build_scored_lookup
from fl_op.solver.allocation.state import (
    MAX_VEHICLE_ASSIGNMENTS,
    AllocationState,
)

logger = logging.getLogger(__name__)


def allocate_resources(
    clusters: list[ClusterSpec],
    orders: list[Any],
    operators: list[Any],
    power_margin: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    scored_pairs: dict[str, list[tuple[float, int, int]]] | None = None,
) -> list[ClusterSpec]:
    """Mutate clusters with allocated_prime_related; return sorted list."""
    sorted_clusters = sorted(
        clusters,
        key=lambda c: (-c["total_penalty_per_day"], c["cluster_id"]),
    )
    order_map = {o.task_id: o for o in orders}
    idx_to_vehicle = {idx: vehicle_id for vehicle_id, idx in vehicle_index.items()}
    idx_to_implement = {
        idx: implement_id for implement_id, idx in implement_index.items()
    }
    scored_lookup = build_scored_lookup(scored_pairs)
    depot_operators = index_operators_by_depot(operators)
    state = AllocationState()

    for cluster in sorted_clusters:
        cluster_orders = [
            order_map[oid] for oid in cluster["task_ids"] if oid in order_map
        ]
        pair_candidates = collect_pair_candidates(
            cluster_orders,
            feasible_pairs,
            idx_to_vehicle,
            idx_to_implement,
            power_margin,
            scored_lookup,
            state,
            max_vehicle_uses=1,
        )
        if not pair_candidates:
            pair_candidates = collect_pair_candidates(
                cluster_orders,
                feasible_pairs,
                idx_to_vehicle,
                idx_to_implement,
                power_margin,
                scored_lookup,
                state,
                max_vehicle_uses=MAX_VEHICLE_ASSIGNMENTS,
            )

        allocated = reserve_best_candidates(
            pair_candidates,
            cluster_resource_limit(cluster_orders),
            state,
        )
        assign_operator(cluster, operators, depot_operators, state)
        cluster["allocated_prime_related"] = allocated
        _log_cluster_allocation(cluster, allocated)

    return sorted_clusters


def _log_cluster_allocation(cluster: ClusterSpec, allocated: dict[str, list[str]]) -> None:
    if not allocated:
        logger.warning(
            "Cluster %s (%d orders): no implements could be allocated",
            cluster["cluster_id"],
            len(cluster["task_ids"]),
        )
        return
    logger.debug(
        "Cluster %s: allocated %d vehicles, operator=%s",
        cluster["cluster_id"],
        len(allocated),
        cluster.get("operator_ref", "none"),  # type: ignore[call-overload]
    )
