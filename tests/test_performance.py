"""Performance machinery: compat-matrix cache and memory-aware pool sizing."""

import numpy as np
import pytest

from fl_op.solver import cluster_pool, feasibility
from fl_op.solver.cluster_pool import compute_pool_sizing
from fl_op.solver.feasibility import (
    build_compat_matrix,
    cached_compat_matrix,
    compat_cache_key,
)
from fl_op.solver.types import PrimeMoverRow, RelatedRow


def _vehicle(vid: str, power: float = 150.0) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({"asset_id": vid, "rated_power": power})


def _implement(iid: str, power: float = 100.0) -> RelatedRow:
    return RelatedRow.from_canonical_dict({"asset_id": iid, "required_power": power})


def _cluster(n_tasks: int, n_vehicles: int = 1) -> dict:
    return {
        "cluster_id": "cl0",
        "depot_ref": "d0",
        "task_ids": [f"o{i}" for i in range(n_tasks)],
        "allocated_prime_related": {f"v{i}": [f"i{i}"] for i in range(n_vehicles)},
        "total_penalty_per_day": 0.0,
    }


class TestCompatCacheKey:
    def test_same_inputs_same_key(self):
        a = compat_cache_key([_vehicle("v0")], [_implement("i0")])
        b = compat_cache_key([_vehicle("v0")], [_implement("i0")])
        assert a == b

    def test_power_change_changes_key(self):
        base = compat_cache_key([_vehicle("v0", 150.0)], [_implement("i0")])
        changed = compat_cache_key([_vehicle("v0", 151.0)], [_implement("i0")])
        assert base != changed

    def test_fleet_membership_changes_key(self):
        base = compat_cache_key([_vehicle("v0")], [_implement("i0")])
        more = compat_cache_key([_vehicle("v0"), _vehicle("v1")], [_implement("i0")])
        assert base != more


class TestCachedCompatMatrix:
    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(feasibility, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(feasibility.constants, "COMPAT_MATRIX_CACHE_ENABLED", True)
        self.cache_dir = tmp_path / feasibility.constants.COMPAT_MATRIX_CACHE_DIRNAME

    def test_miss_then_hit_returns_identical_matrices(self):
        vehicles = [_vehicle("v0"), _vehicle("v1", 90.0)]
        implements = [_implement("i0"), _implement("i1", 160.0)]
        built_compat, built_margin = cached_compat_matrix(vehicles, implements)
        assert len(list(self.cache_dir.glob("*.npz"))) == 1

        cached_compat, cached_margin = cached_compat_matrix(vehicles, implements)
        reference_compat, reference_margin = build_compat_matrix(vehicles, implements)
        assert np.array_equal(cached_compat, reference_compat)
        assert np.array_equal(cached_margin, reference_margin)
        assert np.array_equal(built_compat, cached_compat)

    def test_disabled_cache_writes_nothing(self, monkeypatch):
        monkeypatch.setattr(feasibility.constants, "COMPAT_MATRIX_CACHE_ENABLED", False)
        cached_compat_matrix([_vehicle("v0")], [_implement("i0")])
        assert not self.cache_dir.exists()

    def test_cache_pruned_to_retention_bound(self, monkeypatch):
        monkeypatch.setattr(
            feasibility.constants, "COMPAT_MATRIX_CACHE_MAX_ENTRIES", 2
        )
        for n in range(4):
            cached_compat_matrix([_vehicle("v0", 100.0 + n)], [_implement("i0")])
        assert len(list(self.cache_dir.glob("*.npz"))) == 2


class TestPoolSizing:
    def test_explicit_solver_workers_wins(self, monkeypatch):
        monkeypatch.setattr(cluster_pool, "SOLVER_WORKERS", 3)
        sizing = compute_pool_sizing([_cluster(5) for _ in range(10)])
        assert sizing.explicit_override is True
        assert sizing.n_workers == 3

    def test_memory_cap_bounds_auto_workers(self, monkeypatch):
        monkeypatch.setattr(cluster_pool, "SOLVER_WORKERS", 0)
        # Room for exactly two estimated worker footprints after headroom.
        estimated = cluster_pool._estimate_worker_memory_mb([_cluster(5)])
        headroom = cluster_pool.constants.SOLVER_MEMORY_HEADROOM_PCT
        available = estimated * 2 / (1.0 - headroom / 100.0)
        monkeypatch.setattr(cluster_pool, "_available_memory_mb", lambda: available)
        sizing = compute_pool_sizing([_cluster(5) for _ in range(16)])
        assert sizing.memory_cap == 2
        assert sizing.n_workers == min(2, sizing.cpu_cap)

    def test_unmeasurable_memory_falls_back_to_cpu(self, monkeypatch):
        monkeypatch.setattr(cluster_pool, "SOLVER_WORKERS", 0)
        monkeypatch.setattr(cluster_pool, "_available_memory_mb", lambda: None)
        sizing = compute_pool_sizing([_cluster(5) for _ in range(64)])
        assert sizing.memory_cap is None
        assert sizing.n_workers == min(64, sizing.cpu_cap)

    def test_estimate_grows_with_largest_cluster(self):
        small = cluster_pool._estimate_worker_memory_mb([_cluster(5)])
        large = cluster_pool._estimate_worker_memory_mb([_cluster(500, n_vehicles=20)])
        assert large > small
        assert small >= cluster_pool.constants.SOLVER_WORKER_BASE_MEMORY_MB
