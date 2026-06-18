"""Performance machinery: caches, feedback, and memory-aware pool sizing."""

import json

import numpy as np
import pytest

from fl_op.solver import cluster_pool, feasibility, preprocessing
from fl_op.solver.cluster_pool import compute_pool_sizing
from fl_op.solver.feasibility import (
    build_compat_matrix,
    cached_compat_matrix,
    compat_cache_key,
)
from fl_op.solver.preprocessing import (
    cached_cluster_specs,
    cached_feasible_vehicle_implement_pairs,
)
from fl_op.solver.types import DepotRow, PrimeMoverRow, RelatedRow, SiteRow, TaskRow


def _vehicle(vid: str, power: float = 150.0) -> PrimeMoverRow:
    return PrimeMoverRow.from_canonical_dict({"asset_id": vid, "rated_power": power})


def _implement(iid: str, power: float = 100.0) -> RelatedRow:
    return RelatedRow.from_canonical_dict(
        {
            "asset_id": iid,
            "required_power": power,
            "compatible_operations": ["SPRAYING"],
        }
    )


def _order(oid: str = "o0") -> TaskRow:
    return TaskRow.from_canonical_dict(
        {
            "task_id": oid,
            "operation_type": "SPRAYING",
            "location_ref": "f0",
            "penalty_per_day": 100.0,
        }
    )


def _field() -> SiteRow:
    return SiteRow.from_canonical_dict(
        {"location_id": "f0", "lat": 48.0, "lon": 32.0}
    )


def _depot() -> DepotRow:
    return DepotRow.from_canonical_dict(
        {"location_id": "d0", "lat": 48.0, "lon": 32.0}
    )


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


class TestPreprocessingCache:
    @pytest.fixture(autouse=True)
    def _isolated_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(preprocessing, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(
            preprocessing.constants, "PREPROCESSING_CACHE_ENABLED", True
        )
        self.cache_dir = tmp_path / preprocessing.constants.PREPROCESSING_CACHE_DIRNAME

    def test_candidate_filter_cache_hit_avoids_rebuild(self, monkeypatch):
        vehicles = [_vehicle("v0")]
        implements = [_implement("i0")]
        orders = [_order()]
        compat, _ = build_compat_matrix(vehicles, implements)
        result = cached_feasible_vehicle_implement_pairs(
            orders, vehicles, implements, compat, {"v0": 0}, {"i0": 0}
        )
        assert result == {"o0": [(0, 0)]}

        def fail_rebuild(*_args, **_kwargs):
            raise AssertionError("candidate filter rebuilt despite cache hit")

        monkeypatch.setattr(
            preprocessing, "filter_feasible_vehicle_implement_pairs", fail_rebuild
        )
        cached = cached_feasible_vehicle_implement_pairs(
            orders, vehicles, implements, compat, {"v0": 0}, {"i0": 0}
        )
        assert cached == result
        assert len(list((self.cache_dir / "candidate-filter").glob("*.json"))) == 1

    def test_cluster_specs_cache_hit_avoids_rebuild(self, monkeypatch):
        orders = [_order()]
        clusters = cached_cluster_specs(
            orders,
            [_field()],
            [_depot()],
            [_vehicle("v0")],
            [_implement("i0")],
            np.array([[True]]),
            {"v0": 0},
            {"i0": 0},
            order_index={"o0": orders[0]},
            target_size=10,
        )
        assert clusters[0]["task_ids"] == ["o0"]

        def fail_rebuild(*_args, **_kwargs):
            raise AssertionError("cluster specs rebuilt despite cache hit")

        monkeypatch.setattr(preprocessing, "build_cluster_specs", fail_rebuild)
        cached = cached_cluster_specs(
            orders,
            [_field()],
            [_depot()],
            [_vehicle("v0")],
            [_implement("i0")],
            np.array([[True]]),
            {"v0": 0},
            {"i0": 0},
            order_index={"o0": orders[0]},
            target_size=10,
        )
        assert cached == clusters
        assert len(list((self.cache_dir / "cluster-specs").glob("*.json"))) == 1


class TestPoolSizing:
    @pytest.fixture(autouse=True)
    def _disable_feedback(self, monkeypatch):
        monkeypatch.setattr(cluster_pool.constants, "SOLVER_FEEDBACK_ENABLED", False)

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

    def test_worker_rss_feedback_calibrates_estimate(self, tmp_path, monkeypatch):
        from fl_op.solver import performance_feedback

        monkeypatch.setattr(performance_feedback, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(cluster_pool, "SOLVER_WORKERS", 0)
        monkeypatch.setattr(cluster_pool.constants, "SOLVER_FEEDBACK_ENABLED", True)
        feedback_dir = tmp_path / cluster_pool.constants.SOLVER_FEEDBACK_DIRNAME
        feedback_dir.mkdir(parents=True)
        (feedback_dir / cluster_pool.constants.SOLVER_MEMORY_FEEDBACK_FILENAME).write_text(
            json.dumps({"max_worker_rss_mb": 777.0})
        )

        assert cluster_pool._estimate_worker_memory_mb([_cluster(5)]) >= 777.0

    def test_lns_budget_uses_objective_delta_feedback(self, tmp_path, monkeypatch):
        from fl_op.solver import performance_feedback

        monkeypatch.setattr(performance_feedback, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(cluster_pool.constants, "SOLVER_FEEDBACK_ENABLED", True)
        monkeypatch.setattr(cluster_pool.constants, "CLUSTER_LNS_ENABLED", True)
        monkeypatch.setattr(cluster_pool.constants, "CLUSTER_LNS_TIME_LIMIT_S", 10)
        monkeypatch.setattr(
            cluster_pool.constants, "CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY", 50.0
        )
        monkeypatch.setattr(
            cluster_pool.constants, "CLUSTER_LNS_FEEDBACK_REFERENCE_DELTA", 1000.0
        )
        performance_feedback.record_solver_feedback(
            [{"lns_attempted": True, "lns_objective_delta": -3000}]
        )

        clusters = [_cluster(3)]
        clusters[0]["total_penalty_per_day"] = 100.0
        max_budget = cluster_pool._assign_lns_budgets(clusters)
        assert max_budget == 30
        assert clusters[0]["lns_time_limit_s"] == 30


class TestWorkerMemoryFit:
    @pytest.fixture(autouse=True)
    def _isolated_feedback(self, tmp_path, monkeypatch):
        from fl_op.solver import performance_feedback as pf

        monkeypatch.setattr(pf, "DATA_ROOT", tmp_path)
        monkeypatch.setattr(pf.constants, "SOLVER_FEEDBACK_ENABLED", True)
        monkeypatch.setattr(pf.constants, "SOLVER_MEMORY_FIT_MIN_SAMPLES", 3)
        self.pf = pf

    @staticmethod
    def _record(n_tasks: int, n_veh: int, base: float, slope: float) -> dict:
        cells = (n_tasks + 1) ** 2 * (n_veh + 1)
        return {
            "n_tasks": n_tasks,
            "n_routing_vehicles": n_veh,
            "worker_max_rss_mb": base + slope * cells,
        }

    def test_fits_linear_memory_model_from_samples(self):
        records = [
            self._record(n, n // 5 + 1, base=120.0, slope=0.002)
            for n in (10, 40, 90, 160, 250)
        ]
        self.pf.record_solver_feedback(records)
        model = self.pf.calibrated_memory_model()
        assert model is not None
        base, slope = model
        assert base == pytest.approx(120.0, abs=2.0)
        assert slope == pytest.approx(0.002, rel=0.05)

    def test_too_few_samples_keeps_constant_model(self):
        self.pf.record_solver_feedback(
            [self._record(10, 2, base=100.0, slope=0.001)]
        )
        assert self.pf.calibrated_memory_model() is None

    def test_estimate_uses_fitted_model(self, monkeypatch):
        monkeypatch.setattr(self.pf, "calibrated_memory_model", lambda: (100.0, 0.01))
        monkeypatch.setattr(self.pf, "calibrated_worker_memory_mb", lambda mb: mb)
        # cells = (9+1)^2 * (1+1) = 200 -> 100 + 0.01 * 200 = 102.
        estimate = cluster_pool._estimate_worker_memory_mb([_cluster(9, n_vehicles=1)])
        assert estimate == pytest.approx(102.0)


def test_feasibility_request_cache_reuses_response(
    dataset_dir, small_entities, tmp_path, monkeypatch
) -> None:
    from fl_op.solver import query_pipeline

    schedule_dir = tmp_path / "solve"
    schedule_dir.mkdir()
    (schedule_dir / "schedule.json").write_text(json.dumps({"schedule": []}))
    monkeypatch.setattr(query_pipeline, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(query_pipeline.constants, "FEASIBILITY_CACHE_ENABLED", True)

    order = dict(small_entities["orders"][0])
    first = query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)
    assert list((tmp_path / query_pipeline.constants.FEASIBILITY_CACHE_DIRNAME).glob("*.json"))

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("query rebuilt despite feasibility cache hit")

    monkeypatch.setattr(query_pipeline, "cached_compat_matrix", fail_rebuild)
    second = query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)
    assert second == first


def test_request_key_frames_digests_and_order() -> None:
    """The request key is order-insensitive over the order payload and moves on
    any source/schedule/order change."""
    from fl_op.solver.query_pipeline import feasibility_request_cache_key

    digests = {"vehicles": "dv", "implements": "di", "fields": "df"}
    order = {"order_id": "o", "payload": {"a": 1, "b": 2}}
    base = feasibility_request_cache_key(digests, "ds", order)
    # Source-dict and order-key reordering do not change the key.
    same = feasibility_request_cache_key(
        {"fields": "df", "vehicles": "dv", "implements": "di"},
        "ds",
        {"payload": {"b": 2, "a": 1}, "order_id": "o"},
    )
    assert base == same
    changed_source = feasibility_request_cache_key({**digests, "vehicles": "dv2"}, "ds", order)
    changed_schedule = feasibility_request_cache_key(digests, "ds2", order)
    changed_order = feasibility_request_cache_key(digests, "ds", {"order_id": "o2"})
    assert len({base, changed_source, changed_schedule, changed_order}) == 4


def test_schedule_digest_is_content_addressed(tmp_path) -> None:
    """Two byte-different but logically equal JSON files digest identically."""
    from fl_op.solver import query_pipeline

    query_pipeline._DIGEST_MEMO.clear()
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"schedule": [], "meta": {"x": 1, "y": 2}}))
    b.write_text(json.dumps({"meta": {"y": 2, "x": 1}, "schedule": []}, indent=2))
    da, _ = query_pipeline._stat_memoized_digest(
        a, "feasibility-schedule", query_pipeline._parse_schedule
    )
    db, _ = query_pipeline._stat_memoized_digest(
        b, "feasibility-schedule", query_pipeline._parse_schedule
    )
    assert da == db


def test_stat_memo_skips_reparse_until_file_changes(tmp_path) -> None:
    """The stat-memo serves an unchanged file's digest without re-parsing."""
    from fl_op.solver import query_pipeline

    query_pipeline._DIGEST_MEMO.clear()
    path = tmp_path / "src.json"
    path.write_text(json.dumps({"a": 1, "b": 2}))
    calls = {"n": 0}

    def parse(p):
        calls["n"] += 1
        return json.loads(p.read_text())

    first_digest, parsed = query_pipeline._stat_memoized_digest(path, "test", parse)
    assert parsed is not None and calls["n"] == 1
    # Unchanged file: digest from memo, no re-parse, no parsed payload.
    again_digest, parsed_again = query_pipeline._stat_memoized_digest(path, "test", parse)
    assert again_digest == first_digest and parsed_again is None and calls["n"] == 1
    # Changed content (and size): re-parsed, new digest.
    path.write_text(json.dumps({"a": 1, "b": 2, "c": 3}))
    changed_digest, parsed_changed = query_pipeline._stat_memoized_digest(path, "test", parse)
    assert parsed_changed is not None and calls["n"] == 2 and changed_digest != first_digest
    # Absent file digests to the missing sentinel without parsing.
    missing_digest, missing_parsed = query_pipeline._stat_memoized_digest(
        tmp_path / "nope.json", "test", parse
    )
    assert missing_digest == query_pipeline._MISSING_DIGEST and missing_parsed is None
    assert calls["n"] == 2


def test_feasibility_cache_hit_skips_source_reparse(
    dataset_dir, small_entities, tmp_path, monkeypatch
) -> None:
    """A repeated (cache-hit) request over an unchanged dataset re-reads nothing."""
    from fl_op.solver import query_pipeline

    query_pipeline._DIGEST_MEMO.clear()
    schedule_dir = tmp_path / "solve"
    schedule_dir.mkdir()
    (schedule_dir / "schedule.json").write_text(json.dumps({"schedule": []}))
    monkeypatch.setattr(query_pipeline, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(query_pipeline.constants, "FEASIBILITY_CACHE_ENABLED", True)

    order = dict(small_entities["orders"][0])
    query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)

    # Warm memo: a second identical request hits the response cache and must not
    # re-parse any source (codec.read is never called again).
    def fail_read(*_args, **_kwargs):
        raise AssertionError("source re-parsed despite warm digest memo + cache hit")

    monkeypatch.setattr(query_pipeline, "_parse_schedule", fail_read)
    real_get_codec = query_pipeline.get_codec

    def codec_no_read(fmt):
        codec = real_get_codec(fmt)
        monkeypatch.setattr(codec, "read", fail_read, raising=False)
        return codec

    monkeypatch.setattr(query_pipeline, "get_codec", codec_no_read)
    second = query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)
    assert second["task_id"]


def test_feasibility_cache_hits_on_reordered_schedule(
    dataset_dir, small_entities, tmp_path, monkeypatch
) -> None:
    """A schedule.json rewritten with reordered keys/whitespace still hits cache."""
    from fl_op.solver import query_pipeline

    schedule_dir = tmp_path / "solve"
    schedule_dir.mkdir()
    (schedule_dir / "schedule.json").write_text(
        json.dumps({"schedule": [], "generated_at": "t0"})
    )
    monkeypatch.setattr(query_pipeline, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(query_pipeline.constants, "FEASIBILITY_CACHE_ENABLED", True)

    order = dict(small_entities["orders"][0])
    first = query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)

    # Same logical content, different byte ordering + indentation.
    (schedule_dir / "schedule.json").write_text(
        json.dumps({"generated_at": "t0", "schedule": []}, indent=4, sort_keys=False)
    )

    def fail_rebuild(*_args, **_kwargs):
        raise AssertionError("query rebuilt despite content-equal inputs")

    monkeypatch.setattr(query_pipeline, "cached_compat_matrix", fail_rebuild)
    second = query_pipeline.evaluate_query(str(dataset_dir), str(schedule_dir), order)
    assert second == first
