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
        cluster_telemetry: Optional[list[dict[str, Any]]] = None,
        material_reservations: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.dispatch = dispatch
        self.infeasible = infeasible
        self.kpis = kpis
        self.greedy_assignment = greedy_assignment
        self.n_clusters = n_clusters
        # One machine-readable solve record per cluster (solve_telemetry.py).
        self.cluster_telemetry = cluster_telemetry or []
        # Canonical material-reservation records from cluster-admission
        # charging, settled against the final dispatch (confirmed/released).
        self.material_reservations = material_reservations or []


def _scored_for_cluster_tasks(
    scored: dict[str, list[tuple[float, int, int]]],
    clusters: list[dict[str, Any]],
) -> dict[str, list[tuple[float, int, int]]]:
    """Keep scored candidates for tasks still admitted to the cluster set."""
    admitted_task_ids = {
        task_id
        for cluster in clusters
        for task_id in cluster.get("task_ids", [])
    }
    return {
        task_id: candidates
        for task_id, candidates in scored.items()
        if task_id in admitted_task_ids
    }


def _collapse_alternative_infeasible(
    dispatch: list[dict[str, Any]],
    infeasible: list[dict[str, Any]],
    all_orders: list[Any],
) -> list[dict[str, Any]]:
    """Suppress sibling variant failures and report one record per failed group."""
    group_by_task = {
        o.task_id: str(getattr(o, "alternative_group_ref", "") or "")
        for o in all_orders
    }
    members_by_group: dict[str, list[str]] = {}
    for task_id, group in group_by_task.items():
        if group:
            members_by_group.setdefault(group, []).append(task_id)
    if not members_by_group:
        return infeasible

    served_groups = {
        group_by_task.get(str(pkg.get("task_id", "")), "")
        for pkg in dispatch
    }
    served_groups.discard("")

    grouped_failures: dict[str, list[dict[str, Any]]] = {}
    collapsed: list[dict[str, Any]] = []
    for record in infeasible:
        task_id = str(record.get("task_id", ""))
        group = group_by_task.get(task_id, "")
        if not group and task_id in members_by_group:
            group = task_id
        if not group:
            collapsed.append(record)
            continue
        if group in served_groups:
            continue
        grouped_failures.setdefault(group, []).append(record)

    for group, records in sorted(grouped_failures.items()):
        reasons = sorted({str(r.get("reason_code", "UNKNOWN")) for r in records})
        variants = sorted({str(r.get("task_id", "")) for r in records})
        first = records[0]
        collapsed.append(
            {
                "task_id": group,
                "cluster_id": first.get("cluster_id", ""),
                "reason_code": reasons[0] if len(reasons) == 1 else "UNKNOWN",
                "detail": (
                    "No delivery alternative was assigned for "
                    f"{group}: {', '.join(variants)}"
                ),
            }
    )
    return collapsed


def run_solver_chain(
    rows: dict[str, list[Any]],
    matrix_out_dir: Optional[pathlib.Path] = None,
    enforcement: Optional[Any] = None,
    held_windows: Optional[dict[str, list[tuple[int, int]]]] = None,
    parameters: Optional[Any] = None,
    now: Optional[Any] = None,
) -> SolverChainResult:
    """Run preprocess -> allocate -> greedy -> pool on typed canonical rows.

    `rows` must contain the canonical sections: prime_movers, related_equipment,
    tasks, depots, sites, operators (operators may be empty; forecasts feed
    weather enforcement; travel_links feed routing travel times; cost_rates
    feed energy/material pricing). Each row is a frozen solver-row dataclass
    (PrimeMoverRow, RelatedRow, TaskRow, ...) read by canonical field name,
    never by domain-specific physical column name.

    ``enforcement`` (an EnforcementPolicy built from the optimization profile)
    activates the declared profile constraints: weather windows, operator
    qualification, and material availability. Without it the chain behaves as
    before, so the raw batch pipeline is unaffected.

    ``held_windows`` maps a vehicle asset_id to busy [start, end) epoch-second
    intervals held by frozen/carried rolling assignments; the routing model
    blocks those intervals as vehicle breaks so a held vehicle is reused only
    in a real non-overlapping gap.

    ``parameters`` (a SolverParameters instance) overrides the tunable solver
    parameters for this run; None reproduces the engine constants.

    ``now`` (a timezone-aware datetime) is the planning time origin: cost-rate
    validity, time-window and restriction filters, routing deadlines, and
    held-window offsets are all computed against it. Adapters pass the
    snapshot effective time so replayed and synthetic timelines are exact;
    None falls back to wall-clock now (the raw batch pipeline).
    """
    from fl_op.core.constants import (
        FERTILIZER_COST_EUR_PER_KG,
        ELECTRICITY_COST_EUR_PER_KWH,
        FUEL_COST_EUR_PER_L,
        LABOR_COST_EUR_PER_H,
        MACHINE_WEAR_COST_EUR_PER_H,
        RATE_TYPE_ELECTRICITY,
        RATE_TYPE_FUEL,
        RATE_TYPE_LABOR,
        RATE_TYPE_MACHINE_WEAR,
        RATE_TYPE_MATERIAL,
        RATE_TYPE_TOLL,
        TOLL_COST_EUR_PER_KM,
    )
    from fl_op.solver.aggregator import _compute_kpis
    from fl_op.solver.cluster_pool import pool_solve
    from fl_op.solver.cost_rates import ResourcePrices, resolve_unit_price
    from fl_op.solver.enforcement import (
        EnforcementPolicy,
        apply_material_limits,
        apply_operator_qualification,
        apply_weather_filter,
        finalize_material_reservations,
    )
    from fl_op.solver.feasibility import cached_compat_matrix, save_compat_matrix
    from fl_op.solver.greedy import greedy_assign, vectorized_score
    from fl_op.solver.inputs import (
        SECTION_COST_RATES,
        SECTION_DEPOTS,
        SECTION_FORECASTS,
        SECTION_OPERATORS,
        SECTION_PRIME_MOVERS,
        SECTION_RELATED,
        SECTION_SITES,
        SECTION_TASKS,
        SECTION_TRAVEL_LINKS,
    )
    from fl_op.solver.preprocessing import (
        cached_cluster_specs,
        cached_feasible_vehicle_implement_pairs,
    )
    from fl_op.solver.allocation import allocate_resources
    from fl_op.solver.allocation.scoring import build_free_capacity
    from fl_op.solver.restrictions import apply_location_restrictions
    from fl_op.solver.task_relations import (
        apply_dependency_filter,
        apply_time_window_filter,
        enforce_dependency_outcomes,
    )
    from fl_op.solver.parameters import SolverParameters
    from fl_op.solver.travel_time import build_travel_lookup
    from datetime import datetime, timezone

    enforcement = enforcement or EnforcementPolicy()
    parameters = parameters or SolverParameters()
    vehicles_raw = rows[SECTION_PRIME_MOVERS]
    implements_raw = rows[SECTION_RELATED]
    orders_raw = rows[SECTION_TASKS]
    all_orders_initial = list(orders_raw)
    depots_raw = rows[SECTION_DEPOTS]
    fields_raw = rows[SECTION_SITES]
    operators_raw = rows.get(SECTION_OPERATORS, [])
    forecasts_raw = rows.get(SECTION_FORECASTS, [])
    travel_lookup = build_travel_lookup(rows.get(SECTION_TRAVEL_LINKS, []))
    cost_rates_raw = rows.get(SECTION_COST_RATES, [])

    now = now or datetime.now(tz=timezone.utc)
    fuel_price = resolve_unit_price(
        cost_rates_raw, RATE_TYPE_FUEL, now, FUEL_COST_EUR_PER_L
    )
    material_price = resolve_unit_price(
        cost_rates_raw, RATE_TYPE_MATERIAL, now, FERTILIZER_COST_EUR_PER_KG
    )
    electricity_price = resolve_unit_price(
        cost_rates_raw,
        RATE_TYPE_ELECTRICITY,
        now,
        ELECTRICITY_COST_EUR_PER_KWH,
    )
    labor_price = resolve_unit_price(
        cost_rates_raw, RATE_TYPE_LABOR, now, LABOR_COST_EUR_PER_H
    )
    machine_wear_price = resolve_unit_price(
        cost_rates_raw, RATE_TYPE_MACHINE_WEAR, now, MACHINE_WEAR_COST_EUR_PER_H
    )
    toll_price = resolve_unit_price(
        cost_rates_raw, RATE_TYPE_TOLL, now, TOLL_COST_EUR_PER_KM
    )
    resource_prices = ResourcePrices(
        fuel_eur_per_l=fuel_price,
        material_eur_per_kg=material_price,
        electricity_eur_per_kwh=electricity_price,
        labor_eur_per_h=labor_price,
        machine_wear_eur_per_h=machine_wear_price,
        toll_eur_per_km=toll_price,
    )

    orders_raw, enforcement_infeasible, weather_blocked = apply_weather_filter(
        orders_raw, fields_raw, forecasts_raw, enforcement.weather, now=now
    )
    orders_raw, window_infeasible = apply_time_window_filter(orders_raw, now=now)
    enforcement_infeasible.extend(window_infeasible)
    orders_raw, restriction_infeasible = apply_location_restrictions(
        orders_raw, fields_raw, now=now
    )
    enforcement_infeasible.extend(restriction_infeasible)
    orders_raw, dependency_infeasible = apply_dependency_filter(
        orders_raw, {record["task_id"] for record in enforcement_infeasible}
    )
    enforcement_infeasible.extend(dependency_infeasible)

    vehicle_index = {v.asset_id: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im.asset_id: i for i, im in enumerate(implements_raw)}
    order_index = {o.task_id: o for o in orders_raw}

    compat, power_margin = cached_compat_matrix(vehicles_raw, implements_raw)
    if matrix_out_dir is not None:
        save_compat_matrix(compat, power_margin, matrix_out_dir / "matrix")

    feasible_pairs = cached_feasible_vehicle_implement_pairs(
        orders_raw, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    location_coords = {
        loc.location_id: (float(loc.lat), float(loc.lon))
        for loc in (*fields_raw, *depots_raw)
    }
    scored = vectorized_score(
        orders_raw, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
        fuel_price_eur_per_l=fuel_price,
        resource_prices=resource_prices,
        score_weight_margin=parameters.score_weight_margin,
        score_weight_reposition=parameters.score_weight_reposition,
        travel_lookup=travel_lookup,
        optimization_objective=parameters.optimization_objective,
        location_coords=location_coords,
    )
    clusters = cached_cluster_specs(
        orders_raw, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
        target_size=parameters.cluster_target_size,
        travel_lookup=travel_lookup,
    )
    # Held assets stay allocatable but discounted: their free share of the
    # capacity horizon scales candidate scores and operator rewards.
    free_capacity = build_free_capacity(held_windows, int(now.timestamp()))
    clusters = allocate_resources(
        clusters, orders_raw, operators_raw, power_margin,
        vehicle_index, implement_index, feasible_pairs, scored,
        free_capacity=free_capacity,
        count_priority=parameters.assignment_count_priority,
    )
    if enforcement.operator_qualification:
        operators_by_id = {op.asset_id: op for op in operators_raw}
        enforcement_infeasible.extend(
            apply_operator_qualification(
                clusters, order_index, operators_by_id, free_capacity, now
            )
        )
    material_infeasible, material_reservations = apply_material_limits(
        clusters, order_index, depots_raw, enforcement.material_demand
    )
    enforcement_infeasible.extend(material_infeasible)
    greedy_assignment = greedy_assign(
        _scored_for_cluster_tasks(scored, clusters), vehicle_index, implement_index
    )

    all_dispatch, all_infeasible, cluster_telemetry = pool_solve(
        clusters, orders_raw, vehicles_raw, implements_raw, fields_raw, depots_raw,
        greedy_assignment, vehicle_index, implement_index, held_windows,
        travel_lookup, parameters.cluster_solve_time_limit_s,
        int(now.timestamp()),
        weather_blocked=weather_blocked,
        resource_prices=resource_prices,
        lns_time_limit_s=parameters.lns_time_limit_s,
        optimization_objective=parameters.optimization_objective,
    )
    all_dispatch, all_infeasible = enforce_dependency_outcomes(
        all_dispatch, [*enforcement_infeasible, *all_infeasible], orders_raw
    )
    all_infeasible = _collapse_alternative_infeasible(
        all_dispatch, all_infeasible, all_orders_initial
    )
    material_reservations = finalize_material_reservations(
        material_reservations, all_dispatch
    )
    kpis = _compute_kpis(
        all_dispatch, all_infeasible, orders_raw, greedy_assignment,
        fuel_price_eur_per_l=fuel_price,
        material_price_eur_per_kg=material_price,
        resource_prices=resource_prices,
        vehicles=vehicles_raw,
        implements=implements_raw,
        fields=fields_raw,
        travel_lookup=travel_lookup,
        planning_origin=now,
        optimization_objective=parameters.optimization_objective,
    )

    return SolverChainResult(
        dispatch=all_dispatch,
        infeasible=all_infeasible,
        kpis=kpis,
        greedy_assignment=greedy_assignment,
        n_clusters=len(clusters),
        cluster_telemetry=cluster_telemetry,
        material_reservations=material_reservations,
    )
