"""Shared solver chain extracted from the solve pipeline.

The chain consumes dict rows keyed by source column names and runs the existing
preprocess -> pre-allocate -> greedy -> pool stages without modification. Both
the legacy CLI pipelines and the canonical solver adapters call it, so the
working solver internals have a single call site and are never duplicated.
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
    """Run preprocess -> allocate -> greedy -> pool on dict rows; return results.

    `rows` must contain keys: vehicles, implements, orders, depots, fields,
    operators (operators may be empty).
    """
    from fl_op.models.compat_matrix import build_compat_matrix, save_compat_matrix
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.aggregator import _compute_kpis
    from fl_op.solver.cluster_pool import pool_solve
    from fl_op.solver.greedy import greedy_assign, vectorized_score
    from fl_op.solver.preprocessing import (
        build_cluster_specs,
        filter_feasible_vehicle_implement_pairs,
    )
    from fl_op.solver.resource_allocator import allocate_resources

    vehicles_raw = rows["vehicles"]
    implements_raw = rows["implements"]
    orders_raw = rows["orders"]
    depots_raw = rows["depots"]
    fields_raw = rows["fields"]
    operators_raw = rows.get("operators", [])

    vehicle_index = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["implement_id"]: i for i, im in enumerate(implements_raw)}
    order_index = {o["order_id"]: o for o in orders_raw}

    vehicles_parsed = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_parsed = [Implement.model_validate(im) for im in implements_raw]
    compat, power_margin = build_compat_matrix(vehicles_parsed, implements_parsed)
    if matrix_out_dir is not None:
        save_compat_matrix(compat, power_margin, matrix_out_dir / "matrix")

    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        orders_raw, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    clusters = build_cluster_specs(
        orders_raw, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
    )
    clusters = allocate_resources(
        clusters, orders_raw, vehicles_raw, implements_raw, operators_raw,
        compat, power_margin, vehicle_index, implement_index, feasible_pairs,
    )
    scored = vectorized_score(
        orders_raw, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
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
