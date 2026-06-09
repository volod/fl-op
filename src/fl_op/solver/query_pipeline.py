"""Query-contract pipeline: estimate feasibility and margin for a new order."""

import json
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.canonical.enums import ReasonCode
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.solver.feasibility import build_compat_matrix
from fl_op.solver.query import (
    TimeWindow,
    _build_vehicle_time_index,
    _compute_conflict_risk,
    _estimate_operation_window,
)

logger = logging.getLogger(__name__)

_TOP_CANDIDATES = 3


def run_query(data_dir: str, schedule_dir: str, order_path: str) -> None:
    """Estimate feasibility and top vehicle assignments for a new order.

    Output directory: $DATA_DIR/query-contract/<ISO-timestamp>/
    """
    from fl_op.solver.greedy import vectorized_score
    from fl_op.solver.preprocessing import filter_feasible_vehicle_implement_pairs

    data_path = pathlib.Path(data_dir)
    codec = get_codec(detect_format(data_path))
    sched_path = pathlib.Path(schedule_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "query-contract" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(order_path) as fh:
        new_order = json.load(fh)

    from fl_op.contracts.registry import FileRegistry
    from fl_op.solver.inputs import to_canonical_row, to_canonical_rows

    registry = FileRegistry()
    # Translate raw physical rows (and the new order) into canonical-keyed rows so
    # the solver functions operate on the domain-neutral vocabulary.
    vehicles_raw = to_canonical_rows(
        codec.read(locate_source(data_path, "vehicles.csv", codec)), "vehicles", registry
    )
    implements_raw = to_canonical_rows(
        codec.read(locate_source(data_path, "implements.csv", codec)), "implements", registry
    )
    fields_raw = to_canonical_rows(
        codec.read(locate_source(data_path, "fields.csv", codec)), "fields", registry
    )
    new_order = to_canonical_row(new_order, "orders", registry)

    schedule_file = sched_path / "schedule.json"
    dispatch_packages: list[dict[str, Any]] = []
    if schedule_file.exists():
        with schedule_file.open() as fh:
            dispatch_packages = json.load(fh).get("schedule", [])

    time_index = _build_vehicle_time_index(dispatch_packages)

    vehicle_index = {v["asset_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["asset_id"]: i for i, im in enumerate(implements_raw)}

    compat, _ = build_compat_matrix(vehicles_raw, implements_raw)

    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        [new_order], vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )

    if not feasible_pairs.get(new_order["task_id"]):
        result: dict[str, Any] = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "task_id": new_order.get("task_id"),
            "feasible": False,
            "reason_code": ReasonCode.NO_COMPATIBLE_BUNDLE.value,
            "candidates": [],
        }
    else:
        scored = vectorized_score(
            [new_order], vehicles_raw, implements_raw, fields_raw,
            feasible_pairs, vehicle_index, implement_index,
        )
        oid = new_order["task_id"]
        scored_pairs = scored.get(oid, [])
        est_start, est_end = _estimate_operation_window(new_order)

        idx_to_vehicle = {idx: v for v in vehicles_raw for idx in [vehicle_index[v["asset_id"]]]}
        idx_to_implement = {idx: im for im in implements_raw for idx in [implement_index[im["asset_id"]]]}

        seen_vehicles: set[str] = set()
        candidates: list[dict[str, Any]] = []
        for score, v_idx, i_idx in scored_pairs:
            vid = idx_to_vehicle.get(v_idx, {}).get("asset_id", "")
            iid = idx_to_implement.get(i_idx, {}).get("asset_id", "")
            if vid in seen_vehicles:
                continue
            seen_vehicles.add(vid)
            candidates.append(
                {
                    "prime_asset_id": vid,
                    "related_asset_id": iid,
                    "estimated_margin_eur": round(score, 2),
                    "schedule_conflict_risk": _compute_conflict_risk(vid, est_start, est_end, time_index),
                }
            )
            if len(candidates) == _TOP_CANDIDATES:
                break

        candidates.sort(key=lambda c: (-c["estimated_margin_eur"], c["prime_asset_id"]))
        candidates = candidates[:_TOP_CANDIDATES]

        result = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "task_id": oid,
            "feasible": len(candidates) > 0,
            "candidates": candidates,
        }

    run_metadata = {
        "timestamp": ts,
        "data_dir": str(data_path),
        "schedule_dir": str(sched_path),
        "order_path": order_path,
    }
    output = {"run_metadata": run_metadata, **result}

    out_file = out_dir / "query_result.json"
    with out_file.open("w") as fh:
        json.dump(output, fh, indent=2)

    logger.info("Query result written to %s", out_file)
    print(json.dumps(result, indent=2))
