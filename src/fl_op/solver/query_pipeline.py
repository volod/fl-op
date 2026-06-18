"""Query-contract pipeline: estimate feasibility and margin for a new order.

``evaluate_query`` is the reusable core (also exposed through the serving
API); ``run_query`` wraps it with the CLI artifact and stdout behavior.
"""

import json
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any, Callable

from fl_op.core import constants
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.provenance.namespace import content_hash
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

# Logical source datasets the feasibility query reads; located in whatever
# physical format the dataset uses (the ".csv" is only a locate hint).
_SOURCE_DATASETS = ("vehicles", "implements", "fields")


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
    schedule_file = sched_path / "schedule.json"
    source_paths = {
        name: locate_source(data_path, f"{name}.csv", codec)
        for name in _SOURCE_DATASETS
    }

    # Key the cache on each file input's canonical content, but digest it through
    # a stat-memo so a repeated request over an unchanged dataset reuses the
    # per-file digest without re-parsing. A file is only (re)parsed when its
    # (mtime, size) changed -- or, below, when the lookup misses and the rows are
    # needed for the evaluation. ``parsed`` is the rows/JSON when freshly read.
    source_digests: dict[str, str] = {}
    parsed_sources: dict[str, list[dict[str, Any]]] = {}
    for name, path in source_paths.items():
        digest, rows = _stat_memoized_digest(path, "feasibility-source", codec.read)
        source_digests[name] = digest
        if rows is not None:
            parsed_sources[name] = rows
    schedule_digest, schedule_content = _stat_memoized_digest(
        schedule_file, "feasibility-schedule", _parse_schedule
    )

    cache_key = feasibility_request_cache_key(source_digests, schedule_digest, order)
    cached = _read_feasibility_cache(cache_key)
    if cached is not None:
        logger.info("Feasibility request cache hit: %s", cache_key[:12])
        return cached

    registry = FileRegistry()
    # Cache miss: parse any inputs the stat-memo served from digest, then
    # translate raw physical rows (and the new order) into canonical-keyed rows so
    # the solver functions operate on the domain-neutral vocabulary.
    raw_sources = {
        name: parsed_sources[name]
        if name in parsed_sources
        else codec.read(source_paths[name])
        for name in _SOURCE_DATASETS
    }
    vehicles_raw = to_canonical_rows(raw_sources["vehicles"], "vehicles", registry)
    implements_raw = to_canonical_rows(raw_sources["implements"], "implements", registry)
    fields_raw = to_canonical_rows(raw_sources["fields"], "fields", registry)
    new_order = to_canonical_row(order, "orders", registry)

    if schedule_content is None and schedule_digest != _MISSING_DIGEST:
        schedule_content = _parse_schedule(schedule_file)
    dispatch_packages = (schedule_content or {}).get("schedule", [])

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
    source_digests: dict[str, str],
    schedule_digest: str,
    order: dict[str, Any],
) -> str:
    """Content-addressed hash for one /feasibility request.

    Each file input is represented by its canonical-content digest (computed by
    ``_stat_memoized_digest`` over the parsed rows / parsed ``schedule.json``),
    so two requests whose inputs differ only in JSON key ordering, CSV column
    order, whitespace, or physical format resolve to the same key and reuse a
    cached response. A changed dataset/schedule/order still misses. The inline
    order payload is canonicalized by ``content_hash`` as well.

    Routed through the shared provenance primitive so a namespace-version bump
    invalidates every cached feasibility response at once.
    """
    payload = {
        "kind": "feasibility-request",
        "sources": [[name, source_digests[name]] for name in sorted(source_digests)],
        "schedule": schedule_digest,
        "order": order,
    }
    return content_hash("feasibility-request", payload)


# Sentinel digest for an absent file input.
_MISSING_DIGEST = "missing"

# Per-file content-digest memo, invalidated by the file's (mtime, size) stat
# signature: str(path) -> (mtime_ns, size, digest).
_DIGEST_MEMO: dict[str, tuple[int, int, str]] = {}


def _stat_memoized_digest(
    path: pathlib.Path, namespace: str, parse: "Callable[[pathlib.Path], Any]"
) -> tuple[str, Any]:
    """Canonical-content digest of a file, memoized by its (mtime, size) stat.

    Returns ``(digest, parsed)`` where ``parsed`` is the freshly parsed content
    when the file had to be read, or ``None`` when the digest was served from the
    stat-keyed memo (so an unchanged input is not re-parsed before the cache
    lookup). An absent file digests to ``_MISSING_DIGEST``. Correctness rests on
    inputs being immutable run artifacts rather than rewritten in place within
    the filesystem's mtime resolution.
    """
    try:
        st = path.stat()
    except OSError:
        return (_MISSING_DIGEST, None)
    signature = (st.st_mtime_ns, st.st_size)
    key = str(path)
    cached = _DIGEST_MEMO.get(key)
    if cached is not None and (cached[0], cached[1]) == signature:
        return (cached[2], None)
    parsed = parse(path)
    digest = content_hash(namespace, parsed)
    _DIGEST_MEMO[key] = (signature[0], signature[1], digest)
    _evict_digest_memo()
    return (digest, parsed)


def _evict_digest_memo() -> None:
    """Bound the digest memo; drop oldest insertions past the configured cap."""
    overflow = len(_DIGEST_MEMO) - constants.FEASIBILITY_DIGEST_MEMO_MAX_ENTRIES
    for _ in range(max(0, overflow)):
        _DIGEST_MEMO.pop(next(iter(_DIGEST_MEMO)), None)


def _parse_schedule(path: pathlib.Path) -> dict[str, Any]:
    """Parse an existing ``schedule.json`` into its canonical structure."""
    with path.open() as fh:
        return json.load(fh)


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
