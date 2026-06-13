"""Pre-filter, geographic clustering, and ClusterSpec construction.

Pipeline:
  1. Power + operation-type compatibility filter (vectorised over compat matrix).
  2. Haversine BallTree depot-affinity clustering: each order is assigned to the
     nearest depot; orders within a depot group are split into sub-clusters of
     CLUSTER_TARGET_SIZE.
  3. Returns a list of ClusterSpec TypedDicts ready for solver/allocation.
"""

import dataclasses
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np
from sklearn.neighbors import BallTree

from fl_op.core import constants
from fl_op.core.constants import CLUSTER_TARGET_SIZE
from fl_op.core.paths import DATA_ROOT
from fl_op.solver.travel_time import TravelLookup, travel_seconds
from fl_op.solver.types import ClusterSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compatibility filter
# ---------------------------------------------------------------------------


def filter_feasible_vehicle_implement_pairs(
    orders: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, list[tuple[int, int]]]:
    """Return {task_id: [(v_idx, i_idx), ...]} for all compatible V-I pairs.

    A pair is feasible when:
      - compat[v_idx, i_idx] is True (power margin within threshold)
      - The implement's compatible_operations includes the order's operation_type
    """
    # Build a lookup: implement_id -> set of OperationType values
    impl_ops: dict[str, set[str]] = {}
    for im in implements:
        ops_raw = im.compatible_operations
        if isinstance(ops_raw, str):
            # CSV stores lists as stringified Python lists; parse conservatively
            import ast

            try:
                ops_raw = ast.literal_eval(ops_raw)
            except Exception:
                ops_raw = [ops_raw]
        impl_ops[im.asset_id] = set(ops_raw)

    feasible: dict[str, list[tuple[int, int]]] = {}
    for order in orders:
        op = order.operation_type
        oid = order.task_id
        pairs: list[tuple[int, int]] = []
        for im in implements:
            if op not in impl_ops.get(im.asset_id, set()):
                continue
            i_idx = implement_index.get(im.asset_id)
            if i_idx is None:
                continue
            for v in vehicles:
                v_idx = vehicle_index.get(v.asset_id)
                if v_idx is None:
                    continue
                if compat[v_idx, i_idx]:
                    pairs.append((v_idx, i_idx))
        feasible[oid] = pairs

    n_feasible = sum(len(p) for p in feasible.values())
    logger.debug(
        "Feasibility filter: %d orders, %d total V-I pairs retained",
        len(orders),
        n_feasible,
    )
    return feasible


def candidate_filter_cache_key(
    orders: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    compat: np.ndarray,
) -> str:
    """Content hash over inputs used by the operation candidate filter."""
    return _hash_payload(
        {
            "kind": "candidate-filter",
            "orders": _rows_for_hash(orders),
            "vehicles": _rows_for_hash(vehicles),
            "implements": _rows_for_hash(implements),
            "compat": _array_digest(compat),
        }
    )


def cached_feasible_vehicle_implement_pairs(
    orders: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
) -> dict[str, list[tuple[int, int]]]:
    """Cached wrapper for the deterministic candidate filter.

    Cache misses and any cache I/O issue fall back to the plain filter. Values
    are returned as fresh Python tuples so downstream code sees the same shape
    on hits and misses.
    """
    if not constants.PREPROCESSING_CACHE_ENABLED:
        return filter_feasible_vehicle_implement_pairs(
            orders, vehicles, implements, compat, vehicle_index, implement_index
        )
    cache_dir = _preprocessing_cache_dir("candidate-filter")
    cache_path = cache_dir / (
        f"{candidate_filter_cache_key(orders, vehicles, implements, compat)}.json"
    )
    cached = _read_json_cache(cache_path, "candidate-filter")
    if isinstance(cached, dict):
        try:
            logger.info("Candidate-filter cache hit: %s", cache_path.name)
            return {
                str(task_id): [(int(v), int(i)) for v, i in pairs]
                for task_id, pairs in cached.items()
            }
        except (TypeError, ValueError) as exc:
            logger.warning("Candidate-filter cache decode failed (%s); rebuilding", exc)

    feasible = filter_feasible_vehicle_implement_pairs(
        orders, vehicles, implements, compat, vehicle_index, implement_index
    )
    _write_json_cache(
        cache_path,
        "candidate-filter",
        {task_id: [[v, i] for v, i in pairs] for task_id, pairs in feasible.items()},
    )
    return feasible


# ---------------------------------------------------------------------------
# Haversine BallTree depot-affinity clustering
# ---------------------------------------------------------------------------


def cluster_orders_by_depot(
    orders: list[Any],
    fields: list[Any],
    depots: list[Any],
    travel_lookup: Optional[TravelLookup] = None,
) -> dict[str, list[str]]:
    """Assign each order to the nearest depot; return {depot_id: [task_ids]}.

    With a travel network the depot-field time is the network shortest path
    where one exists (haversine estimate otherwise), so a field whose road
    access favors a farther depot clusters with that depot. Without a
    network, sklearn BallTree with haversine metric on field centroids.
    """
    field_map = {f.location_id: f for f in fields}
    depot_ids = [d.location_id for d in depots]

    if travel_lookup:
        assignment: dict[str, list[str]] = {did: [] for did in depot_ids}
        for order in orders:
            field = field_map.get(order.location_ref)
            if field is None:
                logger.warning(
                    "Order %s has no matching field; skipping", order.task_id
                )
                continue
            nearest_depot = min(
                depots,
                key=lambda d: travel_seconds(
                    d.location_id,
                    str(order.location_ref or ""),
                    float(d.lat), float(d.lon),
                    float(field.lat), float(field.lon),
                    travel_lookup,
                ),
            ).location_id
            assignment[nearest_depot].append(order.task_id)
        return assignment

    depot_coords = np.radians(
        np.array([[float(d.lat), float(d.lon)] for d in depots])
    )
    tree = BallTree(depot_coords, metric="haversine")

    assignment = {did: [] for did in depot_ids}
    for order in orders:
        field = field_map.get(order.location_ref)
        if field is None:
            logger.warning("Order %s has no matching field; skipping", order.task_id)
            continue
        lat = float(field.lat)
        lon = float(field.lon)
        coords = np.radians([[lat, lon]])
        _, indices = tree.query(coords, k=1)
        nearest_depot = depot_ids[indices[0][0]]
        assignment[nearest_depot].append(order.task_id)

    return assignment


def _split_into_subclusters(
    task_ids: list[str],
    target_size: int,
) -> list[list[str]]:
    """Split a flat list of task_ids into sub-lists of approximately target_size."""
    if not task_ids:
        return []
    n = len(task_ids)
    n_clusters = max(1, round(n / target_size))
    chunk = max(1, n // n_clusters)
    chunks = [task_ids[i : i + chunk] for i in range(0, n, chunk)]
    return chunks


# ---------------------------------------------------------------------------
# Task precedence units
# ---------------------------------------------------------------------------


def _dependency_units(orders: list[Any]) -> dict[str, list[str]]:
    """Group task ids into precedence units keyed by their root task id.

    A unit is a root task plus all its (transitive) dependents present in the
    input. References to absent tasks are treated as satisfied; cyclic
    references are broken (logged) so bad data cannot hang clustering.
    """
    present = {o.task_id for o in orders}
    parent: dict[str, str] = {}
    for order in orders:
        dep = str(order.depends_on_task_ref or "")
        parent[order.task_id] = dep if dep in present and dep != order.task_id else ""

    def root_of(task_id: str) -> str:
        seen: set[str] = set()
        current = task_id
        while parent.get(current):
            if current in seen:
                logger.warning(
                    "Cyclic task dependency at %s; treating as independent", current
                )
                return task_id
            seen.add(current)
            current = parent[current]
        return current

    units: dict[str, list[str]] = {}
    for order in orders:
        units.setdefault(root_of(order.task_id), []).append(order.task_id)
    return units


def _regroup_units_by_root_depot(
    depot_assignment: dict[str, list[str]],
    units: dict[str, list[str]],
) -> dict[str, list[list[str]]]:
    """Return {depot_id: [unit, ...]} with every chain kept whole.

    Each multi-task unit moves to the depot of its root task, so the whole
    chain is solved by one cluster and the routing model can order it.
    """
    depot_of = {
        tid: depot for depot, tids in depot_assignment.items() for tid in tids
    }
    unit_of_task = {tid: root for root, tids in units.items() for tid in tids}

    grouped: dict[str, list[list[str]]] = {depot: [] for depot in depot_assignment}
    emitted_roots: set[str] = set()
    for depot, task_ids in depot_assignment.items():
        for tid in task_ids:
            root = unit_of_task.get(tid, tid)
            if root in emitted_roots:
                continue
            members = units.get(root, [tid])
            if len(members) == 1:
                grouped[depot].append([tid])
                continue
            emitted_roots.add(root)
            home = depot_of.get(root, depot)
            grouped.setdefault(home, []).append(
                [m for m in members if m in depot_of]
            )
    return grouped


def _split_units_into_subclusters(
    unit_list: list[list[str]],
    target_size: int,
) -> list[list[str]]:
    """Chunk units to approximately target_size without splitting any unit."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for unit in unit_list:
        if current and len(current) + len(unit) > target_size:
            chunks.append(current)
            current = []
        current.extend(unit)
    if current:
        chunks.append(current)
    return chunks


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_cluster_specs(
    orders: list[Any],
    fields: list[Any],
    depots: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    order_index: dict[str, Any] | None = None,
    target_size: int | None = None,
    travel_lookup: Optional[TravelLookup] = None,
) -> list[ClusterSpec]:
    """Produce ClusterSpec list from raw entity dicts and compat matrix.

    Steps:
      1. Depot-affinity clustering: network travel times where links exist,
         haversine BallTree otherwise.
      2. Sub-cluster each depot group to target_size (default
         CLUSTER_TARGET_SIZE; tunable via SolverParameters).
      3. Compute total_penalty_per_day for priority sorting.
      4. Initialise allocated_prime_related to empty (filled by allocation).
    """
    if order_index is None:
        order_index = {o.task_id: o for o in orders}
    if target_size is None:
        target_size = CLUSTER_TARGET_SIZE

    depot_assignment = cluster_orders_by_depot(orders, fields, depots, travel_lookup)

    units = _dependency_units(orders)
    has_chains = any(len(members) > 1 for members in units.values())
    depot_units = (
        _regroup_units_by_root_depot(depot_assignment, units) if has_chains else None
    )

    clusters: list[ClusterSpec] = []
    cluster_seq = 0
    for depot_id, oid_list in depot_assignment.items():
        if depot_units is not None:
            subclusters = _split_units_into_subclusters(
                depot_units.get(depot_id, []), target_size
            )
        elif oid_list:
            subclusters = _split_into_subclusters(oid_list, target_size)
        else:
            continue
        for sub in subclusters:
            total_penalty = sum(
                float(order_index[oid].penalty_per_day) for oid in sub
            )
            spec: ClusterSpec = {
                "cluster_id": f"cluster_{cluster_seq:06d}",
                "depot_ref": depot_id,
                "task_ids": sub,
                "allocated_prime_related": {},
                "total_penalty_per_day": total_penalty,
            }
            clusters.append(spec)
            cluster_seq += 1

    logger.info(
        "Clustering: %d orders -> %d clusters across %d depots",
        len(orders),
        len(clusters),
        len([d for d in depot_assignment if depot_assignment[d]]),
    )
    return clusters


def cluster_specs_cache_key(
    orders: list[Any],
    fields: list[Any],
    depots: list[Any],
    target_size: int,
    travel_lookup: Optional[TravelLookup],
) -> str:
    """Content hash over inputs that determine pre-allocation clusters."""
    return _hash_payload(
        {
            "kind": "cluster-specs",
            "orders": _rows_for_hash(orders),
            "fields": _rows_for_hash(fields),
            "depots": _rows_for_hash(depots),
            "target_size": target_size,
            "travel_lookup": _travel_lookup_for_hash(travel_lookup),
        }
    )


def cached_cluster_specs(
    orders: list[Any],
    fields: list[Any],
    depots: list[Any],
    vehicles: list[Any],
    implements: list[Any],
    compat: np.ndarray,
    vehicle_index: dict[str, int],
    implement_index: dict[str, int],
    order_index: dict[str, Any] | None = None,
    target_size: int | None = None,
    travel_lookup: Optional[TravelLookup] = None,
) -> list[ClusterSpec]:
    """Cached wrapper for deterministic cluster-spec construction."""
    if target_size is None:
        target_size = CLUSTER_TARGET_SIZE
    if not constants.PREPROCESSING_CACHE_ENABLED:
        return build_cluster_specs(
            orders,
            fields,
            depots,
            vehicles,
            implements,
            compat,
            vehicle_index,
            implement_index,
            order_index=order_index,
            target_size=target_size,
            travel_lookup=travel_lookup,
        )
    cache_dir = _preprocessing_cache_dir("cluster-specs")
    cache_path = cache_dir / (
        f"{cluster_specs_cache_key(orders, fields, depots, target_size, travel_lookup)}.json"
    )
    cached = _read_json_cache(cache_path, "cluster-specs")
    if isinstance(cached, list):
        logger.info("Cluster-spec cache hit: %s", cache_path.name)
        return [dict(cluster) for cluster in cached]

    clusters = build_cluster_specs(
        orders,
        fields,
        depots,
        vehicles,
        implements,
        compat,
        vehicle_index,
        implement_index,
        order_index=order_index,
        target_size=target_size,
        travel_lookup=travel_lookup,
    )
    _write_json_cache(cache_path, "cluster-specs", clusters)
    return clusters


def _preprocessing_cache_dir(kind: str) -> Path:
    return DATA_ROOT / constants.PREPROCESSING_CACHE_DIRNAME / kind


def _read_json_cache(path: Path, kind: str) -> Any:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("%s cache read failed (%s); rebuilding", kind, exc)
        return None
    if payload.get("kind") != kind:
        logger.warning("%s cache kind mismatch in %s", kind, path)
        return None
    return payload.get("value")


def _write_json_cache(path: Path, kind: str, value: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"kind": kind, "schema_version": 1, "value": value},
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            )
        )
        _prune_cache(path.parent)
        logger.debug("%s cache stored: %s", kind, path.name)
    except OSError as exc:
        logger.warning("%s cache write failed (%s); continuing uncached", kind, exc)


def _prune_cache(cache_dir: Path) -> None:
    try:
        entries = sorted(cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    stale_count = max(0, len(entries) - constants.PREPROCESSING_CACHE_MAX_ENTRIES)
    for stale in entries[:stale_count]:
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rows_for_hash(rows: list[Any]) -> list[Any]:
    return [_normalise_for_hash(row) for row in rows]


def _normalise_for_hash(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _normalise_for_hash(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): _normalise_for_hash(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalise_for_hash(v) for v in value]
    if isinstance(value, set):
        return sorted(_normalise_for_hash(v) for v in value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def _array_digest(array: np.ndarray) -> dict[str, Any]:
    contiguous = np.ascontiguousarray(array)
    return {
        "shape": list(contiguous.shape),
        "dtype": str(contiguous.dtype),
        "sha256": hashlib.sha256(contiguous.tobytes()).hexdigest(),
    }


def _travel_lookup_for_hash(travel_lookup: Optional[TravelLookup]) -> list[list[Any]]:
    if not travel_lookup:
        return []
    return [
        [from_ref, to_ref, int(seconds)]
        for (from_ref, to_ref), seconds in sorted(travel_lookup.items())
    ]
