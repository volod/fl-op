"""Query-contract: estimate feasibility and margin for a new order.

No OR-Tools solver call. Uses:
  - Compat matrix for power/operation pre-filter
  - Greedy scoring for margin estimate
  - In-memory dict[vehicle_id, list[TimeWindow]] index for conflict risk
Returns top-3 vehicle assignments sorted by estimated_margin_EUR descending.
"""

import csv
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any, NamedTuple

from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.models.compat_matrix import build_compat_matrix

logger = logging.getLogger(__name__)


class TimeWindow(NamedTuple):
    start: str  # ISO-8601
    end: str  # ISO-8601
    order_id: str


def _build_vehicle_time_index(
    dispatch_packages: list[dict[str, Any]],
) -> dict[str, list[TimeWindow]]:
    """Build {vehicle_id: [TimeWindow, ...]} from schedule for O(1) conflict lookup."""
    index: dict[str, list[TimeWindow]] = {}
    for dp in dispatch_packages:
        vid = dp.get("vehicle_id", "")
        tw = TimeWindow(
            start=dp.get("scheduled_start", ""),
            end=dp.get("scheduled_end", ""),
            order_id=dp.get("order_id", ""),
        )
        index.setdefault(vid, []).append(tw)
    return index


def _windows_overlap(start1: str, end1: str, start2: str, end2: str) -> bool:
    """Return True if two ISO-8601 time windows overlap."""
    try:
        s1 = datetime.fromisoformat(start1)
        e1 = datetime.fromisoformat(end1)
        s2 = datetime.fromisoformat(start2)
        e2 = datetime.fromisoformat(end2)
        return s1 < e2 and s2 < e1
    except (ValueError, TypeError):
        return False


def _compute_conflict_risk(
    vehicle_id: str,
    new_start: str,
    new_end: str,
    time_index: dict[str, list[TimeWindow]],
) -> str:
    """Return 'low', 'medium', or 'high' conflict risk string."""
    windows = time_index.get(vehicle_id, [])
    if not windows:
        return "low"
    overlapping = sum(
        1 for tw in windows if _windows_overlap(new_start, new_end, tw.start, tw.end)
    )
    if overlapping == 0:
        return "low"
    if overlapping <= 2:
        return "medium"
    return "high"


def _estimate_operation_window(order: dict[str, Any]) -> tuple[str, str]:
    """Estimate start/end for the new order based on 'now' and deadline."""
    now = datetime.now(tz=timezone.utc)
    deadline_str = order.get("deadline", "")
    try:
        deadline = datetime.fromisoformat(deadline_str)
    except (ValueError, TypeError):
        from datetime import timedelta
        deadline = now + timedelta(days=7)
    # Estimate start as now; end as deadline (worst case window for conflict check)
    return now.isoformat(), deadline.isoformat()


def run_query(
    data_dir: str,
    schedule_dir: str,
    order_path: str,
) -> None:
    from fl_op.models.implement import Implement
    from fl_op.models.vehicle import Vehicle
    from fl_op.solver.greedy import vectorized_score
    from fl_op.solver.preprocessing import filter_feasible_vehicle_implement_pairs

    data_path = pathlib.Path(data_dir)
    sched_path = pathlib.Path(schedule_dir)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = pathlib.Path(".data") / "query-contract" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load new order
    with open(order_path) as fh:
        new_order = json.load(fh)

    # Load data
    def load_csv(name: str) -> list[dict[str, Any]]:
        p = data_path / name
        if not p.exists():
            return []
        with p.open() as fh:
            return list(csv.DictReader(fh))

    vehicles_raw = load_csv("vehicles.csv")
    implements_raw = load_csv("implements.csv")
    fields_raw = load_csv("fields.csv")

    # Load existing schedule for conflict index
    schedule_file = sched_path / "schedule.json"
    dispatch_packages: list[dict[str, Any]] = []
    if schedule_file.exists():
        with schedule_file.open() as fh:
            sched_data = json.load(fh)
        dispatch_packages = sched_data.get("schedule", [])

    time_index = _build_vehicle_time_index(dispatch_packages)

    vehicle_index = {v["vehicle_id"]: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im["implement_id"]: i for i, im in enumerate(implements_raw)}

    vehicles_parsed = [Vehicle.model_validate(v) for v in vehicles_raw]
    implements_parsed = [Implement.model_validate(im) for im in implements_raw]
    compat, _ = build_compat_matrix(vehicles_parsed, implements_parsed)

    # Pre-filter for the single new order
    feasible_pairs = filter_feasible_vehicle_implement_pairs(
        [new_order], vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )

    if not feasible_pairs.get(new_order["order_id"]):
        result = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "order_id": new_order.get("order_id"),
            "feasible": False,
            "reason": "no_compatible_vehicle_implement_pair",
            "candidates": [],
        }
    else:
        scored = vectorized_score(
            [new_order], vehicles_raw, implements_raw, fields_raw,
            feasible_pairs, vehicle_index, implement_index,
        )
        oid = new_order["order_id"]
        scored_pairs = scored.get(oid, [])

        # Estimate operation window for conflict risk
        est_start, est_end = _estimate_operation_window(new_order)

        # Build top-3 with stable tiebreak by vehicle_id
        # Group by vehicle_id; for each vehicle take best scoring pair
        idx_to_vehicle = {idx: v for v in vehicles_raw for idx in [vehicle_index[v["vehicle_id"]]]}
        idx_to_implement = {idx: im for im in implements_raw for idx in [implement_index[im["implement_id"]]]}

        seen_vehicles: set[str] = set()
        candidates: list[dict[str, Any]] = []

        for score, v_idx, i_idx in scored_pairs:
            vid = idx_to_vehicle.get(v_idx, {}).get("vehicle_id", "")
            iid = idx_to_implement.get(i_idx, {}).get("implement_id", "")
            if vid in seen_vehicles:
                continue
            seen_vehicles.add(vid)
            risk = _compute_conflict_risk(vid, est_start, est_end, time_index)
            candidates.append(
                {
                    "vehicle_id": vid,
                    "implement_id": iid,
                    "estimated_margin_eur": round(score, 2),
                    "schedule_conflict_risk": risk,
                }
            )
            if len(candidates) == 3:
                break

        # Stable tiebreak by vehicle_id within same score
        candidates.sort(key=lambda c: (-c["estimated_margin_eur"], c["vehicle_id"]))
        candidates = candidates[:3]

        result = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "order_id": oid,
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
