"""Full solve pipeline: load data -> preprocess -> allocate -> greedy -> pool -> write."""

import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.core.telemetry import RunTelemetry
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.solver.aggregator import _write_json, _write_report

logger = logging.getLogger(__name__)


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
    from fl_op.solver.chain import run_solver_chain

    telemetry = RunTelemetry()
    data_path = pathlib.Path(data_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "solve" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    codec = get_codec(detect_format(data_path))
    logger.info("Loading data from %s (format: %s)", data_path, codec.extension.lstrip("."))
    rows = {
        name: codec.read(locate_source(data_path, f"{name}.csv", codec))
        for name in ("vehicles", "implements", "orders", "depots", "fields", "operators")
    }

    logger.info(
        "Loaded: %d vehicles, %d implements, %d orders, %d depots, %d fields",
        len(rows["vehicles"]), len(rows["implements"]), len(rows["orders"]),
        len(rows["depots"]), len(rows["fields"]),
    )

    if not rows["orders"]:
        logger.error("No orders found in %s. Check the data directory.", data_path)
        sys.exit(1)
    telemetry.mark_phase("load_data")

    result = run_solver_chain(rows, matrix_out_dir=out_dir)
    all_dispatch = result.dispatch
    all_infeasible = result.infeasible
    kpis = result.kpis
    telemetry.mark_phase("cluster_solving")

    _check_cross_cluster_vehicle_overlap(all_dispatch)

    run_metadata = {
        "timestamp": ts,
        "data_dir": str(data_path),
        "n_clusters": result.n_clusters,
        "n_vehicles": len(rows["vehicles"]),
        "n_implements": len(rows["implements"]),
        "n_orders": len(rows["orders"]),
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
