"""Full solve pipeline: load data -> preprocess -> allocate -> greedy -> pool -> write."""

import csv
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.core.telemetry import RunTelemetry
from fl_op.solver.aggregator import _compute_kpis, _write_json, _write_report
from fl_op.solver.cluster_pool import pool_solve

logger = logging.getLogger(__name__)


def _load_csv(data_path: pathlib.Path, name: str) -> list[dict[str, Any]]:
    p = data_path / name
    if not p.exists():
        return []
    with p.open() as fh:
        return list(csv.DictReader(fh))


def _check_cross_cluster_vehicle_overlap(
    all_dispatch: list[dict[str, Any]],
) -> None:
    """Warn only when the same vehicle has temporally overlapping dispatch windows
    in two different clusters. Sequential reuse of a vehicle across clusters is
    expected and does not produce a warning.
    """
    vehicle_windows: dict[str, list[tuple[float, float, str, str]]] = {}
    for dp in all_dispatch:
        vid = dp["vehicle_id"]
        try:
            s = datetime.fromisoformat(dp["scheduled_start"]).timestamp()
            e = datetime.fromisoformat(dp["scheduled_end"]).timestamp()
        except (ValueError, TypeError, KeyError):
            continue
        vehicle_windows.setdefault(vid, []).append((s, e, dp["cluster_id"], dp["order_id"]))

    for vid, windows in vehicle_windows.items():
        windows.sort()
        for i in range(len(windows) - 1):
            s1, e1, c1, o1 = windows[i]
            s2, e2, c2, o2 = windows[i + 1]
            if s2 < e1 and c1 != c2:
                logger.warning(
                    "Vehicle %s has overlapping cross-cluster schedule: "
                    "order %s (cluster %s, ends %.0fs) overlaps order %s (cluster %s, starts %.0fs)",
                    vid, o1, c1, e1, o2, c2, s2,
                )


def run_solve(data_dir: str) -> None:
    """Full solve pipeline: load -> preprocess -> pre-allocate -> greedy -> pool -> write."""
    from fl_op.models.compat_matrix import build_compat_matrix, save_compat_matrix
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.greedy import greedy_assign, vectorized_score
    from fl_op.solver.preprocessing import build_cluster_specs, filter_feasible_vehicle_implement_pairs
    from fl_op.solver.resource_allocator import allocate_resources

    telemetry = RunTelemetry()
    data_path = pathlib.Path(data_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "solve" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Loading data from %s", data_path)
    vehicles_raw = _load_csv(data_path, "vehicles.csv")
    implements_raw = _load_csv(data_path, "implements.csv")
    orders_raw = _load_csv(data_path, "orders.csv")
    depots_raw = _load_csv(data_path, "depots.csv")
    fields_raw = _load_csv(data_path, "fields.csv")
    operators_raw = _load_csv(data_path, "operators.csv")

    logger.info(
        "Loaded: %d vehicles, %d implements, %d orders, %d depots, %d fields",
        len(vehicles_raw), len(implements_raw), len(orders_raw),
        len(depots_raw), len(fields_raw),
    )

    if not orders_raw:
        logger.error("No orders found in %s. Check the data directory.", data_path)
        sys.exit(1)
    telemetry.mark_phase("load_data")

    vehicle_index = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["implement_id"]: i for i, im in enumerate(implements_raw)}
    order_index = {o["order_id"]: o for o in orders_raw}

    logger.info("Building compatibility matrix (%d x %d)", len(vehicles_raw), len(implements_raw))
    vehicles_parsed = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_parsed = [Implement.model_validate(im) for im in implements_raw]
    compat, power_margin = build_compat_matrix(vehicles_parsed, implements_parsed)
    save_compat_matrix(compat, power_margin, out_dir / "matrix")
    telemetry.mark_phase("compatibility_matrix")

    logger.info("Building cluster specs")
    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        orders_raw, vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )
    clusters = build_cluster_specs(
        orders_raw, fields_raw, depots_raw, vehicles_raw, implements_raw,
        compat, vehicle_index, implement_index, order_index,
    )
    telemetry.mark_phase("preprocessing")

    logger.info("Allocating resources across %d clusters", len(clusters))
    clusters = allocate_resources(
        clusters, orders_raw, vehicles_raw, implements_raw, operators_raw,
        compat, power_margin, vehicle_index, implement_index, feasible_pairs,
    )
    telemetry.mark_phase("resource_allocation")

    logger.info("Computing greedy warm-start scores")
    scored = vectorized_score(
        orders_raw, vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
    )
    greedy_assignment = greedy_assign(scored, vehicle_index, implement_index)
    telemetry.mark_phase("greedy_warm_start")

    logger.info("Launching cluster solvers (%d clusters)", len(clusters))
    all_dispatch, all_infeasible = pool_solve(
        clusters, orders_raw, vehicles_raw, implements_raw, fields_raw, depots_raw,
        greedy_assignment, vehicle_index, implement_index,
    )
    telemetry.mark_phase("cluster_solving")

    kpis = _compute_kpis(all_dispatch, all_infeasible, orders_raw, greedy_assignment)
    _check_cross_cluster_vehicle_overlap(all_dispatch)

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
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata,
         "run_telemetry": run_telemetry, "schedule": all_dispatch},
        out_dir / "schedule.json",
    )
    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata,
         "run_telemetry": run_telemetry, "infeasible_orders": all_infeasible},
        out_dir / "infeasible_orders.json",
    )
    _write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, "run_metadata": run_metadata,
         "run_telemetry": run_telemetry, **kpis},
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
        logger.warning("0 orders dispatched / %d rejected", kpis["n_infeasible"])
        for reason, count in top3:
            logger.warning("  top infeasibility reason: %s: %d", reason, count)
        sys.exit(1)
