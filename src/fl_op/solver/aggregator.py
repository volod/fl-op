"""Parallel cluster solving + result aggregation.

Runs cluster_solver.solve_cluster() in a multiprocessing.Pool(start_method='spawn',
maxtasksperchild=1).  Per-task timeout via AsyncResult.get(timeout=).
WorkerLostError or TimeoutError marks the cluster infeasible("solver_timeout").
"""

import json
import logging
import multiprocessing
import pathlib
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import (
    ARTIFACT_SCHEMA_VERSION,
    CLUSTER_SOLVE_TIME_LIMIT_S,
    FUEL_COST_EUR_PER_L,
)
from fl_op.core.telemetry import RunTelemetry
from fl_op.models.types import ClusterSpec

logger = logging.getLogger(__name__)

# Headroom above solver time limit before the pool task is killed
_TASK_TIMEOUT_S = CLUSTER_SOLVE_TIME_LIMIT_S + 30


def _worker_fn(args: tuple) -> tuple[list[dict], list[dict]]:
    """Top-level function for Pool worker — must be picklable (module-level)."""
    from fl_op.solver.cluster_solver import solve_cluster

    (
        cluster_dict,
        orders,
        vehicles,
        implements,
        fields,
        depots,
        greedy_assignment,
        vehicle_index,
        implement_index,
    ) = args
    result = solve_cluster(
        cluster_dict,
        orders,
        vehicles,
        implements,
        fields,
        depots,
        greedy_assignment,
        vehicle_index,
        implement_index,
    )
    assert len(result) == 2, "Worker must return (dispatch_packages, infeasible_orders)"
    return result


def pool_solve(
    clusters: list[ClusterSpec],
    orders: list[dict[str, Any]],
    vehicles: list[dict[str, Any]],
    implements: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    depots: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Solve all clusters in parallel; return (all_dispatch, all_infeasible)."""
    ctx = multiprocessing.get_context("spawn")
    n_workers = max(1, multiprocessing.cpu_count())

    all_dispatch: list[dict[str, Any]] = []
    all_infeasible: list[dict[str, Any]] = []

    # Convert ClusterSpec to plain dict for pickling
    cluster_dicts = [dict(c) for c in clusters]

    args_list = [
        (
            cd,
            orders,
            vehicles,
            implements,
            fields,
            depots,
            greedy_assignment,
            vehicle_index,
            implement_index,
        )
        for cd in cluster_dicts
    ]

    with ctx.Pool(processes=n_workers, maxtasksperchild=1) as pool:
        async_results = [pool.apply_async(_worker_fn, (args,)) for args in args_list]

        for ar, cluster_dict in zip(async_results, cluster_dicts):
            cluster_id = cluster_dict.get("cluster_id", "?")
            try:
                dispatch, infeasible = ar.get(timeout=_TASK_TIMEOUT_S)
                assert len(dispatch) + len(infeasible) >= 0  # type check
                all_dispatch.extend(dispatch)
                all_infeasible.extend(infeasible)
            except multiprocessing.TimeoutError:
                logger.warning("Cluster %s timed out", cluster_id)
                all_infeasible.extend(
                    [
                        {
                            "order_id": oid,
                            "cluster_id": cluster_id,
                            "reason": "solver_timeout",
                            "detail": f"worker did not complete within {_TASK_TIMEOUT_S}s",
                        }
                        for oid in cluster_dict.get("order_ids", [])
                    ]
                )
            except Exception as exc:
                logger.error(
                    "Cluster %s worker lost or crashed: %s", cluster_id, exc
                )
                all_infeasible.extend(
                    [
                        {
                            "order_id": oid,
                            "cluster_id": cluster_id,
                            "reason": "solver_timeout",
                            "detail": f"worker crashed: {exc}",
                        }
                        for oid in cluster_dict.get("order_ids", [])
                    ]
                )

    return all_dispatch, all_infeasible


def _compute_kpis(
    dispatch_packages: list[dict[str, Any]],
    infeasible_orders: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    greedy_assignment: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    total_margin = sum(d.get("estimated_margin_eur", 0) for d in dispatch_packages)
    total_fuel = sum(d.get("estimated_fuel_l", 0) for d in dispatch_packages)
    total_fertilizer = sum(d.get("estimated_fertilizer_kg", 0) for d in dispatch_packages)

    order_map = {o["order_id"]: o for o in orders}

    # Greedy baseline: sum estimated_revenue for orders that had a greedy assignment
    greedy_baseline = sum(
        float(order_map[oid].get("estimated_revenue_eur", 0))
        - float(order_map[oid].get("area_ha", 0)) * FUEL_COST_EUR_PER_L
        for oid in greedy_assignment
        if oid in order_map
    )

    infeasibility_reasons: dict[str, int] = {}
    for inf in infeasible_orders:
        r = inf.get("reason", "unknown")
        infeasibility_reasons[r] = infeasibility_reasons.get(r, 0) + 1

    return {
        "n_dispatched": len(dispatch_packages),
        "n_infeasible": len(infeasible_orders),
        "total_estimated_margin_eur": round(total_margin, 2),
        "greedy_baseline_margin_eur": round(greedy_baseline, 2),
        "solver_improvement_eur": round(total_margin - greedy_baseline, 2),
        "total_fuel_l": round(total_fuel, 2),
        "total_fertilizer_kg": round(total_fertilizer, 2),
        "infeasibility_reasons": infeasibility_reasons,
    }


def _write_json(obj: Any, path: pathlib.Path) -> None:
    with path.open("w") as fh:
        json.dump(obj, fh, indent=2, default=str)


def _write_report(
    dispatch_packages: list[dict[str, Any]],
    infeasible_orders: list[dict[str, Any]],
    kpis: dict[str, Any],
    path: pathlib.Path,
) -> None:
    lines = [
        "Fleet Optimization Schedule Report",
        "=" * 40,
        f"Dispatched:   {kpis['n_dispatched']}",
        f"Infeasible:   {kpis['n_infeasible']}",
        f"Total margin: {kpis['total_estimated_margin_eur']:.2f} EUR",
        f"Greedy base:  {kpis['greedy_baseline_margin_eur']:.2f} EUR",
        f"Improvement:  {kpis['solver_improvement_eur']:.2f} EUR",
        f"Total fuel:   {kpis['total_fuel_l']:.1f} L",
        "",
        "Infeasibility reasons:",
    ]
    for reason, count in sorted(kpis["infeasibility_reasons"].items()):
        lines.append(f"  {reason}: {count}")

    if infeasible_orders:
        lines.append("")
        lines.append("Infeasible orders (first 20):")
        for inf in infeasible_orders[:20]:
            lines.append(
                f"  {inf['order_id']}: {inf['reason']} - {inf['detail']}"
            )

    path.write_text("\n".join(lines) + "\n")


def run_solve(data_dir: str) -> None:
    """Full solve pipeline: load -> preprocess -> pre-allocate -> greedy -> pool -> write."""
    import csv
    import sys

    from fl_op.models.compat_matrix import build_compat_matrix, save_compat_matrix
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.greedy import vectorized_score, greedy_assign
    from fl_op.solver.preprocessing import build_cluster_specs
    from fl_op.solver.resource_allocator import allocate_resources

    telemetry = RunTelemetry()
    data_path = pathlib.Path(data_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = pathlib.Path(".data") / "solve" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading data from %s", data_path)

    def load_csv(name: str) -> list[dict[str, Any]]:
        p = data_path / name
        if not p.exists():
            return []
        with p.open() as fh:
            return list(csv.DictReader(fh))

    vehicles_raw = load_csv("vehicles.csv")
    implements_raw = load_csv("implements.csv")
    orders_raw = load_csv("orders.csv")
    depots_raw = load_csv("depots.csv")
    fields_raw = load_csv("fields.csv")

    logger.info(
        "Loaded: %d vehicles, %d implements, %d orders, %d depots, %d fields",
        len(vehicles_raw), len(implements_raw), len(orders_raw),
        len(depots_raw), len(fields_raw),
    )

    if not orders_raw:
        logger.error("[error] No orders found in %s. Check the data directory.", data_path)
        sys.exit(1)
    telemetry.mark_phase("load_data")

    # Build index mappings
    vehicle_index: dict[str, int] = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index: dict[str, int] = {im["implement_id"]: i for i, im in enumerate(implements_raw)}
    order_index: dict[str, dict[str, Any]] = {o["order_id"]: o for o in orders_raw}

    # Build compat matrix
    logger.info("Building compatibility matrix (%d x %d)", len(vehicles_raw), len(implements_raw))
    vehicles_parsed = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_parsed = [Implement.model_validate(im) for im in implements_raw]
    compat, power_margin = build_compat_matrix(vehicles_parsed, implements_parsed)
    save_compat_matrix(compat, power_margin, out_dir / "matrix")
    telemetry.mark_phase("compatibility_matrix")

    # Phase 4: cluster specs
    logger.info("Building cluster specs")
    clusters = build_cluster_specs(
        orders_raw, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
    )

    # Get feasible pairs from preprocessing
    from fl_op.solver.preprocessing import filter_feasible_vehicle_implement_pairs
    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        orders_raw, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    telemetry.mark_phase("preprocessing")

    # Phase 4: resource allocation
    logger.info("Allocating resources across %d clusters", len(clusters))
    operators_raw = load_csv("operators.csv")
    clusters = allocate_resources(
        clusters, orders_raw, vehicles_raw, implements_raw, operators_raw,
        compat, power_margin, vehicle_index, implement_index, feasible_pairs,
    )
    telemetry.mark_phase("resource_allocation")

    # Phase 5: greedy warm-start
    logger.info("Computing greedy warm-start scores")
    scored = vectorized_score(
        orders_raw, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
    )
    greedy_assignment = greedy_assign(scored, vehicle_index, implement_index)
    telemetry.mark_phase("greedy_warm_start")

    # Phase 6+7: pool solve
    logger.info("Launching cluster solvers (%d clusters)", len(clusters))
    all_dispatch, all_infeasible = pool_solve(
        clusters, orders_raw, vehicles_raw, implements_raw, fields_raw, depots_raw,
        greedy_assignment, vehicle_index, implement_index,
    )
    telemetry.mark_phase("cluster_solving")

    # KPIs
    kpis = _compute_kpis(all_dispatch, all_infeasible, orders_raw, greedy_assignment)

    # Detect cross-cluster vehicle overlap (should not happen; log warning if it does)
    seen_vehicles_per_dispatch: dict[str, str] = {}
    for dp in all_dispatch:
        vid = dp["vehicle_id"]
        oid = dp["order_id"]
        if vid in seen_vehicles_per_dispatch and seen_vehicles_per_dispatch[vid] != dp["cluster_id"]:
            logger.warning(
                "Cross-cluster vehicle overlap: vehicle %s in cluster %s and %s",
                vid, seen_vehicles_per_dispatch[vid], dp["cluster_id"],
            )
        seen_vehicles_per_dispatch[vid] = dp["cluster_id"]

    # Write outputs
    run_metadata = {
        "timestamp": ts,
        "data_dir": str(data_path),
        "n_clusters": len(clusters),
        "n_vehicles": len(vehicles_raw),
        "n_implements": len(implements_raw),
        "n_orders": len(orders_raw),
    }
    run_telemetry = telemetry.snapshot()
    _write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "run_telemetry": run_telemetry,
            "schedule": all_dispatch,
        },
        out_dir / "schedule.json",
    )
    _write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "run_telemetry": run_telemetry,
            "infeasible_orders": all_infeasible,
        },
        out_dir / "infeasible_orders.json",
    )
    _write_json(
        {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_metadata": run_metadata,
            "run_telemetry": run_telemetry,
            **kpis,
        },
        out_dir / "schedule_kpis.json",
    )
    _write_report(all_dispatch, all_infeasible, kpis, out_dir / "schedule_report.txt")

    logger.info(
        "Solve complete: %d dispatched, %d infeasible -> %s",
        kpis["n_dispatched"], kpis["n_infeasible"], out_dir,
    )

    if kpis["n_dispatched"] == 0:
        reasons = kpis.get("infeasibility_reasons", {})
        top3 = sorted(reasons.items(), key=lambda x: -x[1])[:3]
        print(f"0 served / {kpis['n_infeasible']} rejected")
        print("Top infeasibility reasons:")
        for reason, count in top3:
            print(f"  {reason}: {count}")
        print("Try: fl-op generate-data --vehicles N --implements N (increase fleet size)")
        sys.exit(1)
