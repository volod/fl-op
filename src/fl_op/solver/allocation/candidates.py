"""Candidate collection and reservation for resource pre-allocation."""

from typing import Any

import numpy as np

from fl_op.solver.allocation.scoring import FreeCapacity, ScoredLookup, score_vi_pair
from fl_op.solver.allocation.state import AllocationState


def collect_pair_candidates(
    cluster_orders: list[Any],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    idx_to_vehicle: dict[int, str],
    idx_to_implement: dict[int, str],
    power_margin: np.ndarray,
    scored_lookup: ScoredLookup | None,
    state: AllocationState,
    max_vehicle_uses: int,
    free_capacity: FreeCapacity | None = None,
) -> dict[tuple[str, str], float]:
    """Collect scored (vehicle_id, implement_id) candidates for one cluster."""
    candidates: dict[tuple[str, str], float] = {}
    for order in cluster_orders:
        for v_idx, i_idx in feasible_pairs.get(order.task_id, []):
            vehicle_id = idx_to_vehicle.get(v_idx)
            implement_id = idx_to_implement.get(i_idx)
            if vehicle_id is None or implement_id is None:
                continue
            if implement_id in state.claimed_implements:
                continue
            if state.vehicle_assignment_count.get(vehicle_id, 0) >= max_vehicle_uses:
                continue

            score = score_vi_pair(
                order, power_margin, v_idx, i_idx, scored_lookup,
                free_capacity, vehicle_id, implement_id,
            )
            key = (vehicle_id, implement_id)
            candidates[key] = candidates.get(key, 0.0) + score
    return candidates


def reserve_best_candidates(
    pair_candidates: dict[tuple[str, str], float],
    resource_limit: int,
    state: AllocationState,
) -> dict[str, list[str]]:
    """Reserve the best candidates and update global allocation state."""
    sorted_candidates = sorted(
        pair_candidates.items(),
        key=lambda item: (-item[1], item[0][0], item[0][1]),
    )
    allocated: dict[str, list[str]] = {}
    reserved_vehicles: set[str] = set()
    reserved_implements: set[str] = set()
    for (vehicle_id, implement_id), _score in sorted_candidates:
        if len(allocated) >= resource_limit:
            break
        if vehicle_id in reserved_vehicles or implement_id in reserved_implements:
            continue
        state.claimed_implements.add(implement_id)
        state.vehicle_assignment_count[vehicle_id] = (
            state.vehicle_assignment_count.get(vehicle_id, 0) + 1
        )
        reserved_vehicles.add(vehicle_id)
        reserved_implements.add(implement_id)
        allocated[vehicle_id] = [implement_id]
    return allocated
