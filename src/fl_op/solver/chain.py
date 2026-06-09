"""Shared solver chain used by CLI pipelines and solver adapters.

The chain consumes dict rows keyed by source column names and runs the current
preprocess -> pre-allocate -> greedy -> pool stages. CLI pipelines and canonical
solver adapters call this same function, so solver orchestration has one code path.
"""

import logging
import pathlib
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SolverChainResult:
    """Plain container for the chain outputs (no Pydantic, process-boundary safe)."""

    def __init__(
        self,
        dispatch: list[dict[str, Any]],
        infeasible: list[dict[str, Any]],
        kpis: dict[str, Any],
        greedy_assignment: dict[str, tuple[int, int]],
        n_clusters: int,
    ) -> None:
        self.dispatch = dispatch
        self.infeasible = infeasible
        self.kpis = kpis
        self.greedy_assignment = greedy_assignment
        self.n_clusters = n_clusters


def run_solver_chain(
    rows: dict[str, list[dict[str, Any]]],
    matrix_out_dir: Optional[pathlib.Path] = None,
) -> SolverChainResult:
    """Run preprocess -> allocate -> greedy -> pool on canonical dict rows.

    `rows` must contain the canonical sections: prime_movers, related_equipment,
    tasks, depots, sites, operators (operators may be empty). Each row is keyed by
    canonical field names (asset_id, rated_power, task_id, ...), never by
    domain-specific physical column names.
    """
    from fl_op.solver.aggregator import _compute_kpis
    from fl_op.solver.cluster_pool import pool_solve
    from fl_op.solver.feasibility import build_compat_matrix, save_compat_matrix
    from fl_op.solver.greedy import greedy_assign, vectorized_score
    from fl_op.solver.inputs import (
        SECTION_DEPOTS,
        SECTION_OPERATORS,
        SECTION_PRIME_MOVERS,
        SECTION_RELATED,
        SECTION_SITES,
        SECTION_TASKS,
    )
    from fl_op.solver.preprocessing import (
        build_cluster_specs,
        filter_feasible_vehicle_implement_pairs,
    )
    from fl_op.solver.allocation import allocate_resources

    vehicles_raw = rows[SECTION_PRIME_MOVERS]
    implements_raw = rows[SECTION_RELATED]
    orders_raw = rows[SECTION_TASKS]
    depots_raw = rows[SECTION_DEPOTS]
    fields_raw = rows[SECTION_SITES]
    operators_raw = rows.get(SECTION_OPERATORS, [])

    vehicle_index = {v["asset_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["asset_id"]: i for i, im in enumerate(implements_raw)}
    order_index = {o["task_id"]: o for o in orders_raw}

    compat, power_margin = build_compat_matrix(vehicles_raw, implements_raw)
    if matrix_out_dir is not None:
        save_compat_matrix(compat, power_margin, matrix_out_dir / "matrix")

    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        orders_raw, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    scored = vectorized_score(
        orders_raw, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
    )
    clusters = build_cluster_specs(
        orders_raw, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
    )
    clusters = allocate_resources(
        clusters, orders_raw, operators_raw, power_margin,
        vehicle_index, implement_index, feasible_pairs, scored,
    )
    greedy_assignment = greedy_assign(scored, vehicle_index, implement_index)

    all_dispatch, all_infeasible = pool_solve(
        clusters, orders_raw, vehicles_raw, implements_raw, fields_raw, depots_raw,
        greedy_assignment, vehicle_index, implement_index,
    )
    kpis = _compute_kpis(all_dispatch, all_infeasible, orders_raw, greedy_assignment)

    return SolverChainResult(
        dispatch=all_dispatch,
        infeasible=all_infeasible,
        kpis=kpis,
        greedy_assignment=greedy_assignment,
        n_clusters=len(clusters),
    )
