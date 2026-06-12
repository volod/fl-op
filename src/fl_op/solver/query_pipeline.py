"""Query-contract pipeline: estimate feasibility and margin for a new order.

``evaluate_query`` is the reusable core (also exposed through the serving
API); ``run_query`` wraps it with the CLI artifact and stdout behavior.
"""

import json
import logging
import pathlib
import hashlib
from datetime import datetime, timezone
from typing import Any

from fl_op.core import constants
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.canonical.enums import ReasonCode
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.solver.feasibility import cached_compat_matrix
from fl_op.solver.query import (
    _build_vehicle_time_index,
    _compute_conflict_risk,
    _estimate_operation_window,
)

logger = logging.getLogger(__name__)

_TOP_CANDIDATES = 3


def evaluate_query(
    data_dir: str, schedule_dir: str, order: dict[str, Any]
) -> dict[str, Any]:
    """Estimate feasibility and top vehicle assignments for one new order dict."""
    from fl_op.contracts.registry import FileRegistry
    from fl_op.solver.greedy import vectorized_score
    from fl_op.solver.inputs import to_canonical_row, to_canonical_rows
    from fl_op.solver.preprocessing import cached_feasible_vehicle_implement_pairs

    data_path = pathlib.Path(data_dir)
    codec = get_codec(detect_format(data_path))
    sched_path = pathlib.Path(schedule_dir)
    cache_key = feasibility_request_cache_key(data_path, sched_path, order, codec)
    cached = _read_feasibility_cache(cache_key)
    if cached is not None:
        logger.info("Feasibility request cache hit: %s", cache_key[:12])
        return cached

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
    new_order = to_canonical_row(order, "orders", registry)

    schedule_file = sched_path / "schedule.json"
    dispatch_packages: list[dict[str, Any]] = []
    if schedule_file.exists():
        with schedule_file.open() as fh:
            dispatch_packages = json.load(fh).get("schedule", [])

    time_index = _build_vehicle_time_index(dispatch_packages)

    vehicle_index = {v.asset_id: i for i, v in enumerate(vehicles_raw)}
    implement_index = {im.asset_id: i for i, im in enumerate(implements_raw)}

    compat, _ = cached_compat_matrix(vehicles_raw, implements_raw)

    feasible_pairs = cached_feasible_vehicle_implement_pairs(
        [new_order], vehicles_raw, implements_raw, compat, vehicle_index, implement_index
    )

    if not feasible_pairs.get(new_order.task_id):
        result = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "task_id": new_order.task_id,
            "feasible": False,
            "reason_code": ReasonCode.NO_COMPATIBLE_BUNDLE.value,
            "candidates": [],
        }
        _write_feasibility_cache(cache_key, result)
        return result

    scored = vectorized_score(
        [new_order], vehicles_raw, implements_raw, fields_raw,
        feasible_pairs, vehicle_index, implement_index,
    )
    oid = new_order.task_id
    scored_pairs = scored.get(oid, [])
    est_start, est_end = _estimate_operation_window(new_order)

    idx_to_vehicle = {idx: v for v in vehicles_raw for idx in [vehicle_index[v.asset_id]]}
    idx_to_implement = {idx: im for im in implements_raw for idx in [implement_index[im.asset_id]]}

    seen_vehicles: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for score, v_idx, i_idx in scored_pairs:
        v_row = idx_to_vehicle.get(v_idx)
        i_row = idx_to_implement.get(i_idx)
        vid = v_row.asset_id if v_row is not None else ""
        iid = i_row.asset_id if i_row is not None else ""
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
    _write_feasibility_cache(cache_key, result)
    return result


def feasibility_request_cache_key(
    data_path: pathlib.Path,
    schedule_path: pathlib.Path,
    order: dict[str, Any],
    codec: Any,
) -> str:
    """Stable hash for one /feasibility request.

    The key includes bytes of every source file the query reads, schedule.json,
    and the request order payload, so a changed dataset/schedule/order misses
    even when the path is reused.
    """
    digest = hashlib.sha256()
    digest.update(b"feasibility-v1")
    for filename in ("vehicles.csv", "implements.csv", "fields.csv"):
        source = locate_source(data_path, filename, codec)
        digest.update(str(source.name).encode("utf-8"))
        digest.update(_file_digest(source).encode("ascii"))
    digest.update(b"schedule")
    digest.update(_file_digest(schedule_path / "schedule.json").encode("ascii"))
    digest.update(
        json.dumps(order, separators=(",", ":"), sort_keys=True, default=str).encode(
            "utf-8"
        )
    )
    return digest.hexdigest()


def _file_digest(path: pathlib.Path) -> str:
    if not path.exists():
        return "missing"
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _feasibility_cache_path(cache_key: str) -> pathlib.Path:
    return DATA_ROOT / constants.FEASIBILITY_CACHE_DIRNAME / f"{cache_key}.json"


def _read_feasibility_cache(cache_key: str) -> dict[str, Any] | None:
    if not constants.FEASIBILITY_CACHE_ENABLED:
        return None
    path = _feasibility_cache_path(cache_key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Feasibility cache read failed (%s); rebuilding", exc)
        return None
    if payload.get("kind") != "feasibility-response":
        return None
    value = payload.get("value")
    return value if isinstance(value, dict) else None


def _write_feasibility_cache(cache_key: str, result: dict[str, Any]) -> None:
    if not constants.FEASIBILITY_CACHE_ENABLED:
        return
    path = _feasibility_cache_path(cache_key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "kind": "feasibility-response",
                    "schema_version": 1,
                    "value": result,
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        _prune_feasibility_cache(path.parent)
    except OSError as exc:
        logger.warning("Feasibility cache write failed (%s); continuing uncached", exc)


def _prune_feasibility_cache(cache_dir: pathlib.Path) -> None:
    try:
        entries = sorted(cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    stale_count = max(0, len(entries) - constants.FEASIBILITY_CACHE_MAX_ENTRIES)
    for stale in entries[:stale_count]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def run_query(data_dir: str, schedule_dir: str, order_path: str) -> None:
    """Estimate feasibility and top vehicle assignments for a new order.

    Output directory: $DATA_DIR/query-contract/<ISO-timestamp>/
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_dir = DATA_ROOT / "query-contract" / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(order_path) as fh:
        new_order = json.load(fh)

    result = evaluate_query(data_dir, schedule_dir, new_order)

    run_metadata = {
        "timestamp": ts,
        "data_dir": str(pathlib.Path(data_dir)),
        "schedule_dir": str(pathlib.Path(schedule_dir)),
        "order_path": order_path,
    }
    output = {"run_metadata": run_metadata, **result}

    out_file = out_dir / "query_result.json"
    with out_file.open("w") as fh:
        json.dump(output, fh, indent=2)

    logger.info("Query result written to %s", out_file)
    print(json.dumps(result, indent=2))
