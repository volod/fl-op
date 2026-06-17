"""Spatial execution feedback: per-pass coverage geometry.

Covers the geometry primitives, the coverage payload/state helpers, and the
stream applier integration that refines remaining work from accumulated,
overlap-corrected covered area.
"""

import copy
import math
import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.core.constants import METERS_PER_DEGREE_LAT
from fl_op.core.geometry import polygon_rings_area_km2, swept_polygon
from fl_op.solver.restrictions import parse_polygon
from fl_op.stream.apply import EventApplicator
from fl_op.stream.coverage import (
    PAYLOAD_COVERED_PATH,
    PAYLOAD_COVERED_POLYGON,
    PAYLOAD_SWATH_WIDTH_M,
    coverage_state,
    has_coverage_payload,
    pass_ring_from_payload,
)
from fl_op.stream.source import EVENT_TASK_PROGRESS, ExecutionEvent


def _rect_latlon(area_ha: float, lat0: float = 48.0, lon0: float = 32.0):
    """Axis-aligned rectangle of ~area_ha, as canonical [lat, lon] vertices."""
    side_m = math.sqrt(area_ha * 10_000.0)
    dlat = side_m / METERS_PER_DEGREE_LAT
    dlon = side_m / (METERS_PER_DEGREE_LAT * math.cos(math.radians(lat0)))
    return [
        [lat0, lon0],
        [lat0 + dlat, lon0],
        [lat0 + dlat, lon0 + dlon],
        [lat0, lon0 + dlon],
    ]


def _covered_ha(latlon_ring) -> float:
    return polygon_rings_area_km2([parse_polygon(latlon_ring)]) * 100.0


_EVENT_SEQ = iter(range(100_000))


def _event(payload: dict, entity_ref: str = "order_1") -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"cov-{next(_EVENT_SEQ)}",
        event_type=EVENT_TASK_PROGRESS,
        observed_at="2026-06-05T08:00:00Z",
        entity_ref=entity_ref,
        payload=payload,
    )


def _orders(area_ha: float = 100.0):
    return {
        "orders": [
            {"order_id": "order_1", "status": "pending", "area_ha": str(area_ha)},
        ],
    }


# ---------------------------------------------------------------------------
# Geometry primitives
# ---------------------------------------------------------------------------


class TestCoverageGeometry:
    def test_swept_polygon_area_matches_width_times_length(self):
        # ~1000 m east-west pass at lat 48, 24 m implement -> ~2.4 ha.
        path = [(48.0, 32.0), (48.0, 32.0 + 1000.0 / (METERS_PER_DEGREE_LAT * math.cos(math.radians(48.0))))]
        ring = swept_polygon(path, width_m=24.0)
        area_ha = polygon_rings_area_km2([ring]) * 100.0
        assert area_ha == pytest.approx(2.4, rel=0.05)

    def test_overlapping_passes_not_double_counted(self):
        ring = swept_polygon([(48.0, 32.0), (48.0, 32.01)], width_m=24.0)
        single = polygon_rings_area_km2([ring])
        assert polygon_rings_area_km2([ring, ring]) == pytest.approx(single)

    def test_adjacent_passes_sum(self):
        ring_a = swept_polygon([(48.0, 32.0), (48.0, 32.01)], width_m=24.0)
        # ~50 m north: a clearly separate strip.
        north = 50.0 / METERS_PER_DEGREE_LAT
        ring_b = swept_polygon([(48.0 + north, 32.0), (48.0 + north, 32.01)], width_m=24.0)
        single = polygon_rings_area_km2([ring_a])
        assert polygon_rings_area_km2([ring_a, ring_b]) == pytest.approx(2 * single, rel=0.05)

    def test_empty_inputs(self):
        assert swept_polygon([], 24.0) == []
        assert swept_polygon([(48.0, 32.0)], 0.0) == []
        assert polygon_rings_area_km2([]) == 0.0


# ---------------------------------------------------------------------------
# Coverage payload + state helpers
# ---------------------------------------------------------------------------


class TestCoveragePayload:
    def test_has_coverage_payload(self):
        assert has_coverage_payload({PAYLOAD_COVERED_POLYGON: _rect_latlon(10.0)})
        assert has_coverage_payload({PAYLOAD_COVERED_PATH: [[48.0, 32.0]]})
        assert not has_coverage_payload({"completed_fraction": 0.5})

    def test_pass_ring_from_explicit_polygon(self):
        ring = pass_ring_from_payload({PAYLOAD_COVERED_POLYGON: _rect_latlon(10.0)})
        assert ring is not None and len(ring) >= 3

    def test_pass_ring_from_path_and_width(self):
        ring = pass_ring_from_payload(
            {
                PAYLOAD_COVERED_PATH: [[48.0, 32.0], [48.0, 32.01]],
                PAYLOAD_SWATH_WIDTH_M: 24.0,
            }
        )
        assert ring is not None and len(ring) >= 3

    def test_path_without_width_is_unusable(self):
        assert pass_ring_from_payload({PAYLOAD_COVERED_PATH: [[48.0, 32.0]]}) is None
        assert pass_ring_from_payload({}) is None

    def test_coverage_state_fraction_and_remaining(self):
        ring = parse_polygon(_rect_latlon(25.0))
        state = coverage_state([ring], original_area_ha=50.0)
        assert state["covered_fraction"] == pytest.approx(0.5, rel=0.05)
        assert state["remaining_area_ha"] == pytest.approx(25.0, rel=0.05)

    def test_coverage_state_clamps_overshoot(self):
        ring = parse_polygon(_rect_latlon(80.0))
        state = coverage_state([ring], original_area_ha=50.0)
        assert state["covered_fraction"] == 1.0

    def test_coverage_state_without_area_is_zero(self):
        ring = parse_polygon(_rect_latlon(10.0))
        assert coverage_state([ring], original_area_ha=0.0)["covered_fraction"] == 0.0


# ---------------------------------------------------------------------------
# Stream applier integration
# ---------------------------------------------------------------------------


class TestCoverageApplication:
    def test_pass_scales_remaining_work_spatially(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=100.0)
        ring = _rect_latlon(50.0)
        expected = _covered_ha(ring) / 100.0
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: ring}))
        assert float(sources["orders"][0]["area_ha"]) == pytest.approx(
            100.0 * (1 - expected), rel=0.02
        )
        assert sources["orders"][0]["status"] == "started"
        assert applicator.coverage_reports[-1]["covered_fraction"] == pytest.approx(
            expected, rel=0.02
        )
        assert applicator.coverage_reports[-1]["n_passes"] == 1

    def test_overlapping_pass_does_not_over_credit(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=100.0)
        ring = _rect_latlon(40.0)
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: ring}))
        after_first = float(sources["orders"][0]["area_ha"])
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: ring}))
        # The identical second pass covers no new ground: remaining is unchanged.
        assert float(sources["orders"][0]["area_ha"]) == pytest.approx(after_first)
        assert applicator.coverage_reports[-1]["n_passes"] == 2

    def test_non_overlapping_passes_accumulate(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=100.0)
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: _rect_latlon(25.0, lon0=32.0)}))
        first_fraction = applicator.coverage_reports[-1]["covered_fraction"]
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: _rect_latlon(25.0, lon0=32.05)}))
        second_fraction = applicator.coverage_reports[-1]["covered_fraction"]
        assert second_fraction > first_fraction
        assert second_fraction == pytest.approx(2 * first_fraction, rel=0.05)

    def test_full_coverage_completes_task(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=20.0)
        # Cover well beyond the area: fraction clamps to 1.0 -> task removed.
        applicator.apply(sources, _event({PAYLOAD_COVERED_POLYGON: _rect_latlon(40.0)}))
        assert sources["orders"] == []
        assert applicator.completions[-1]["task_id"] == "order_1"
        assert applicator.coverage_reports[-1]["covered_fraction"] == 1.0

    def test_path_and_width_pass_scales_work(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=10.0)
        payload = {
            PAYLOAD_COVERED_PATH: [
                [48.0, 32.0],
                [48.0, 32.0 + 1000.0 / (METERS_PER_DEGREE_LAT * math.cos(math.radians(48.0)))],
            ],
            PAYLOAD_SWATH_WIDTH_M: 24.0,
        }
        applicator.apply(sources, _event(payload))
        # ~2.4 ha covered out of 10 ha -> ~7.6 ha remaining.
        assert float(sources["orders"][0]["area_ha"]) == pytest.approx(7.6, rel=0.1)

    def test_geometryless_progress_still_uses_scalar(self):
        applicator = EventApplicator()
        sources = _orders(area_ha=100.0)
        applicator.apply(sources, _event({"completed_fraction": 0.4}))
        assert float(sources["orders"][0]["area_ha"]) == pytest.approx(60.0)
        assert applicator.coverage_reports == []


class TestCoverageTrail:
    def test_record_and_aggregate_trail(self, tmp_path):
        from fl_op.stream.coverage import coverage_stats, record_coverage

        trail = tmp_path / "coverage-passes.jsonl"
        record_coverage(
            [
                {"task_id": "t1", "n_passes": 1, "covered_fraction": 0.3,
                 "covered_area_ha": 30.0, "remaining_area_ha": 70.0},
                {"task_id": "t1", "n_passes": 2, "covered_fraction": 0.6,
                 "covered_area_ha": 60.0, "remaining_area_ha": 40.0},
                {"task_id": "t2", "n_passes": 1, "covered_fraction": 0.5,
                 "covered_area_ha": 10.0, "remaining_area_ha": 10.0},
            ],
            path=trail,
        )
        stats = coverage_stats(path=trail)
        assert stats["n_passes"] == 3
        assert stats["n_tasks_with_coverage"] == 2
        # Latest record per task: t1 -> 60 ha, t2 -> 10 ha.
        assert stats["total_covered_area_ha"] == pytest.approx(70.0)
        assert stats["mean_covered_fraction"] == pytest.approx((0.6 + 0.5) / 2)

    def test_empty_trail_is_empty_stats(self, tmp_path):
        from fl_op.stream.coverage import coverage_stats

        assert coverage_stats(path=tmp_path / "missing.jsonl") == {}


class TestCoverageDriverEndToEnd:
    def test_coverage_event_flows_through_driver(
        self, dataset_dir: pathlib.Path, monkeypatch
    ):
        from fl_op.contracts.registry import FileRegistry
        from fl_op.snapshot.builder import SnapshotBuilder
        from fl_op.stream import coverage as coverage_mod
        from fl_op.stream.driver import StreamDriver
        from fl_op.stream.source import ExecutionEvent

        now = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
        registry = FileRegistry()
        sources = SnapshotBuilder(registry).load_sources(dataset_dir)
        driver = StreamDriver(registry)
        baseline = driver.initial_revision(copy.deepcopy(sources), effective_at=now)
        assert baseline.plan.assignments, "baseline must assign at least one task"
        oid = baseline.plan.assignments[0].task_id
        area = _order_area_ha(sources, oid)
        assert area and area > 0

        captured: list[dict] = []
        monkeypatch.setattr(
            coverage_mod,
            "record_coverage",
            lambda reports, path=None: captured.extend(reports) or reports,
        )

        event = ExecutionEvent(
            "cov-1", "task.progress", now.isoformat(), oid,
            {PAYLOAD_COVERED_POLYGON: _rect_latlon(area * 0.5)},
        )
        result = driver.run(copy.deepcopy(sources), [event], effective_at=now)
        # Baseline plus the coverage-driven revision.
        assert len(result.revisions) >= 2
        records = [r for r in captured if r["task_id"] == oid]
        assert records, "coverage pass should be recorded for the started order"
        assert records[0]["covered_fraction"] == pytest.approx(0.5, rel=0.1)
        assert records[0]["n_passes"] == 1


def _order_area_ha(sources: dict, oid: str):
    for rows in sources.values():
        for row in rows:
            if str(row.get("order_id")) == oid and "area_ha" in row:
                return float(row["area_ha"])
    return None
