"""CP-SAT global assignment model for cluster pre-allocation.

Assigns scarce vehicles, implements, and operators across all clusters at
once, so a high-penalty cluster no longer starves a later cluster when an
alternative resource mix can serve both (the failure mode of the greedy
reservation loop it replaces).

Objective construction: candidate rewards are shifted so that every reward
exceeds the total spread of candidate scores. Maximizing therefore allocates
as many (vehicle, implement) bundles as the per-cluster and per-resource
limits admit first, and only then breaks ties by the shared greedy score --
the same intent as the greedy pass (clusters need resources before margin
fine-tuning), made globally optimal. Operators form an independent block of
the same model: rewards count certified coverage of the cluster's operation
types with a depot-match tiebreak, so qualification enforcement loses as few
tasks as possible.

Returns None (caller falls back to greedy) when the candidate set exceeds
GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES or CP-SAT yields no feasible solution
within its time budget.
"""

import logging
from typing import Any, Optional

import numpy as np

from fl_op.core import constants
from fl_op.solver.allocation.limits import cluster_resource_limit
from fl_op.solver.allocation.scoring import FreeCapacity, ScoredLookup, score_vi_pair
from fl_op.solver.allocation.state import MAX_VEHICLE_ASSIGNMENTS
from fl_op.solver.enforcement import ops_set
from fl_op.solver.types import ClusterSpec

logger = logging.getLogger(__name__)

# One cluster's prepared model inputs: (cluster, {(vehicle_id, implement_id):
# score}, resource limit, cluster operation types).
_ClusterCandidates = tuple[ClusterSpec, dict[tuple[str, str], float], int, set[str]]


def allocate_resources_global(
    clusters: list[ClusterSpec],
    orders: list[Any],
    operators: list[Any],
    power_margin: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    scored_lookup: ScoredLookup | None,
    free_capacity: FreeCapacity | None = None,
    count_priority: float = constants.GLOBAL_ASSIGNMENT_COUNT_PRIORITY,
) -> Optional[list[ClusterSpec]]:
    """Solve the global assignment; mutate and return penalty-sorted clusters.

    Returns None when the model is oversized or CP-SAT finds no solution, so
    the caller can fall back to the greedy reservation loop.
    """
    from ortools.sat.python import cp_model

    sorted_clusters = sorted(
        clusters,
        key=lambda c: (-c["total_penalty_per_day"], c["cluster_id"]),
    )
    order_map = {o.task_id: o for o in orders}
    idx_to_vehicle = {idx: vid for vid, idx in vehicle_index.items()}
    idx_to_implement = {idx: iid for iid, idx in implement_index.items()}

    cluster_candidates = _collect_cluster_candidates(
        sorted_clusters,
        order_map,
        feasible_pairs,
        idx_to_vehicle,
        idx_to_implement,
        power_margin,
        scored_lookup,
        free_capacity,
    )
    n_candidates = sum(len(cands) for _, cands, _, _ in cluster_candidates)
    if n_candidates > constants.GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES:
        logger.info(
            "Global assignment skipped: %d candidates exceed the %d model cap",
            n_candidates,
            constants.GLOBAL_ASSIGNMENT_MAX_MODEL_CANDIDATES,
        )
        return None

    model = cp_model.CpModel()
    pair_vars, pair_terms = _add_pair_assignment_block(
        model, cluster_candidates, count_priority
    )
    operator_vars, operator_terms = _add_operator_assignment_block(
        model, cluster_candidates, operators, free_capacity
    )
    model.Maximize(sum([*pair_terms, *operator_terms]))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = constants.GLOBAL_ASSIGNMENT_TIME_LIMIT_S
    solver.parameters.relative_gap_limit = constants.GLOBAL_ASSIGNMENT_RELATIVE_GAP
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = constants.GLOBAL_ASSIGNMENT_RANDOM_SEED
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        logger.warning(
            "Global assignment found no solution (status %s)",
            solver.StatusName(status),
        )
        return None

    _apply_solution(solver, cluster_candidates, pair_vars, operator_vars)
    logger.info(
        "Global assignment: %d clusters, %d pair candidates, status %s",
        len(sorted_clusters),
        n_candidates,
        solver.StatusName(status),
    )
    return sorted_clusters


def _collect_cluster_candidates(
    sorted_clusters: list[ClusterSpec],
    order_map: dict[str, Any],
    feasible_pairs: dict[str, list[tuple[int, int]]],
    idx_to_vehicle: dict[int, str],
    idx_to_implement: dict[int, str],
    power_margin: np.ndarray,
    scored_lookup: ScoredLookup | None,
    free_capacity: FreeCapacity | None = None,
) -> list[_ClusterCandidates]:
    """Score every feasible (vehicle, implement) pair per cluster, keep top-K."""
    cluster_candidates: list[_ClusterCandidates] = []
    for cluster in sorted_clusters:
        cluster_orders = [
            order_map[oid] for oid in cluster["task_ids"] if oid in order_map
        ]
        candidates: dict[tuple[str, str], float] = {}
        for order in cluster_orders:
            for v_idx, i_idx in feasible_pairs.get(order.task_id, []):
                vehicle_id = idx_to_vehicle.get(v_idx)
                implement_id = idx_to_implement.get(i_idx)
                if vehicle_id is None or implement_id is None:
                    continue
                score = score_vi_pair(
                    order, power_margin, v_idx, i_idx, scored_lookup,
                    free_capacity, vehicle_id, implement_id,
                )
                key = (vehicle_id, implement_id)
                candidates[key] = candidates.get(key, 0.0) + score
        top = _truncate_with_diversity(candidates)
        operations = {o.operation_type for o in cluster_orders}
        cluster_candidates.append(
            (cluster, top, cluster_resource_limit(cluster_orders), operations)
        )
    return cluster_candidates


def _truncate_with_diversity(
    candidates: dict[tuple[str, str], float],
) -> dict[tuple[str, str], float]:
    """Keep the top-K pairs while spreading them across vehicles/implements.

    A plain score cut keeps the nearest vehicle paired with every implement
    (the vehicle dominates the score), so the truncated set offers the model
    almost no vehicle alternatives. Capping pairs per vehicle and per
    implement keeps the list small and contestable at the same time.
    """
    top: dict[tuple[str, str], float] = {}
    vehicle_counts: dict[str, int] = {}
    implement_counts: dict[str, int] = {}
    for (vehicle_id, implement_id), score in sorted(
        candidates.items(), key=lambda item: (-item[1], item[0])
    ):
        if len(top) >= constants.GLOBAL_ASSIGNMENT_CANDIDATES_PER_CLUSTER:
            break
        if (
            vehicle_counts.get(vehicle_id, 0)
            >= constants.GLOBAL_ASSIGNMENT_PAIRS_PER_VEHICLE
        ):
            continue
        if (
            implement_counts.get(implement_id, 0)
            >= constants.GLOBAL_ASSIGNMENT_PAIRS_PER_IMPLEMENT
        ):
            continue
        top[(vehicle_id, implement_id)] = score
        vehicle_counts[vehicle_id] = vehicle_counts.get(vehicle_id, 0) + 1
        implement_counts[implement_id] = implement_counts.get(implement_id, 0) + 1
    return top


def _add_pair_assignment_block(
    model: Any,
    cluster_candidates: list[_ClusterCandidates],
    count_priority: float = constants.GLOBAL_ASSIGNMENT_COUNT_PRIORITY,
) -> tuple[dict[tuple[int, str, str], Any], list[Any]]:
    """Add x[cluster, vehicle, implement] variables, limits, and reward terms."""
    all_scores = [
        score for _, cands, _, _ in cluster_candidates for score in cands.values()
    ]
    if not all_scores:
        return {}, []
    min_score = min(all_scores)
    scale = constants.GLOBAL_ASSIGNMENT_SCORE_SCALE
    max_scaled = int(round((max(all_scores) - min_score) * scale))
    # At full count priority every reward exceeds the total score spread, so
    # allocation count dominates the objective and scores only break ties.
    # Lower priorities shrink that count bias toward pure score maximization
    # (0.0: a contested resource goes to the highest-scoring cluster even if
    # another cluster then stays unallocated).
    count_priority = min(1.0, max(0.0, count_priority))
    base_reward = int(round((max_scaled + 1) * count_priority))

    pair_vars: dict[tuple[int, str, str], Any] = {}
    implement_uses: dict[str, list[Any]] = {}
    vehicle_uses: dict[str, list[Any]] = {}
    objective_terms: list[Any] = []

    for c_idx, (_cluster, candidates, limit, _ops) in enumerate(cluster_candidates):
        cluster_vars: list[Any] = []
        cluster_vehicle_vars: dict[str, list[Any]] = {}
        for (vehicle_id, implement_id), score in sorted(candidates.items()):
            var = model.NewBoolVar(f"x_{c_idx}_{vehicle_id}_{implement_id}")
            pair_vars[(c_idx, vehicle_id, implement_id)] = var
            cluster_vars.append(var)
            cluster_vehicle_vars.setdefault(vehicle_id, []).append(var)
            implement_uses.setdefault(implement_id, []).append(var)
            vehicle_uses.setdefault(vehicle_id, []).append(var)
            reward = base_reward + int(round((score - min_score) * scale))
            objective_terms.append(reward * var)
        if cluster_vars:
            model.Add(sum(cluster_vars) <= limit)
        for vehicle_vars in cluster_vehicle_vars.values():
            model.Add(sum(vehicle_vars) <= 1)

    for implement_vars in implement_uses.values():
        model.Add(sum(implement_vars) <= 1)
    for vehicle_vars in vehicle_uses.values():
        model.Add(sum(vehicle_vars) <= MAX_VEHICLE_ASSIGNMENTS)

    return pair_vars, objective_terms


def _add_operator_assignment_block(
    model: Any,
    cluster_candidates: list[_ClusterCandidates],
    operators: list[Any],
    free_capacity: FreeCapacity | None = None,
) -> tuple[dict[tuple[int, str], Any], list[Any]]:
    """Add y[cluster, operator] variables: one operator per cluster at most."""
    if not operators:
        return {}, []

    free_capacity = free_capacity or {}
    rewards: dict[tuple[int, str], int] = {}
    for c_idx, (cluster, _cands, _limit, operations) in enumerate(cluster_candidates):
        for operator in operators:
            coverage = len(operations & ops_set(operator.certified_operations))
            reward = coverage * constants.OPERATOR_COVERAGE_REWARD
            if operator.home_depot_ref == cluster["depot_ref"]:
                reward += constants.OPERATOR_DEPOT_MATCH_REWARD
            # Hold-aware discount: among equally covering operators the one
            # with the freer calendar wins (coverage still dominates).
            busy_share = 1.0 - free_capacity.get(operator.asset_id, 1.0)
            reward -= int(round(constants.OPERATOR_HOLD_DISCOUNT_REWARD * busy_share))
            rewards[(c_idx, operator.asset_id)] = reward
    if not rewards:
        return {}, []
    # Same count-first construction as the pair block: staffing one more
    # cluster always beats a better-qualified operator elsewhere. The base
    # shift spans the reward spread so it stays count-first even when hold
    # discounts push some rewards negative.
    base_reward = max(rewards.values()) - min(rewards.values()) + 1

    operator_vars: dict[tuple[int, str], Any] = {}
    operator_uses: dict[str, list[Any]] = {}
    objective_terms: list[Any] = []
    for c_idx in range(len(cluster_candidates)):
        cluster_vars: list[Any] = []
        for operator in operators:
            var = model.NewBoolVar(f"y_{c_idx}_{operator.asset_id}")
            operator_vars[(c_idx, operator.asset_id)] = var
            cluster_vars.append(var)
            operator_uses.setdefault(operator.asset_id, []).append(var)
            objective_terms.append(
                (base_reward + rewards[(c_idx, operator.asset_id)]) * var
            )
        model.Add(sum(cluster_vars) <= 1)
    for op_vars in operator_uses.values():
        model.Add(sum(op_vars) <= 1)

    return operator_vars, objective_terms


def _apply_solution(
    solver: Any,
    cluster_candidates: list[_ClusterCandidates],
    pair_vars: dict[tuple[int, str, str], Any],
    operator_vars: dict[tuple[int, str], Any],
) -> None:
    """Write chosen pairs and operators back onto the cluster specs."""
    for c_idx, (cluster, candidates, _limit, _ops) in enumerate(cluster_candidates):
        allocated: dict[str, list[str]] = {}
        for vehicle_id, implement_id in sorted(candidates):
            var = pair_vars.get((c_idx, vehicle_id, implement_id))
            if var is not None and solver.Value(var):
                allocated[vehicle_id] = [implement_id]
        cluster["allocated_prime_related"] = allocated

    for (c_idx, operator_id), var in operator_vars.items():
        if solver.Value(var):
            cluster = cluster_candidates[c_idx][0]
            cluster["operator_ref"] = operator_id  # type: ignore[typeddict-unknown-key]
