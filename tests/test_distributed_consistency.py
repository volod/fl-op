"""Eventual-consistency mechanisms: watermarks, skew, idempotency, coalescing."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.observation import Observation
from fl_op.core.constants import METRIC_BATTERY_LEVEL
from fl_op.snapshot.assessment import assess_observations
from fl_op.stream.apply import EventApplicator
from fl_op.stream.driver import _coalesce
from fl_op.stream.source import EVENT_OBSERVATION_RECORDED, EVENT_TASK_STARTED, ExecutionEvent

_NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _obs(
    observation_id: str,
    value: float,
    at: datetime,
    ingested_at: datetime | None = None,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        entity_ref="s1",
        metric=METRIC_BATTERY_LEVEL,
        value=value,
        observed_at=at,
        ingested_at=ingested_at,
        source_ref=f"sensor-readings:{observation_id}",
    )


def _event(event_id: str, observed_at: str, event_type: str = EVENT_TASK_STARTED) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=event_id,
        event_type=event_type,
        observed_at=observed_at,
        entity_ref="order_1",
        payload={},
    )


def test_source_watermarks_record_newest_trusted_reading() -> None:
    obs = [
        _obs("o-1", 80.0, _NOW - timedelta(hours=2)),
        _obs("o-2", 79.0, _NOW - timedelta(hours=1)),
    ]
    result = assess_observations(obs, _NOW, as_of=_NOW)
    assert result.source_watermarks == {"sensor-readings": _NOW - timedelta(hours=1)}


def test_future_timestamp_beyond_skew_tolerance_is_excluded() -> None:
    obs = [
        _obs("o-1", 80.0, _NOW - timedelta(hours=1)),
        _obs("o-future", 10.0, _NOW + timedelta(hours=2)),
    ]
    result = assess_observations(obs, _NOW, as_of=_NOW)
    ids = {o.observation_id for o in result.observations}
    assert "o-future" not in ids
    assert any(
        f.rule_id == "dq://observation/future-timestamp" for f in result.findings
    )
    # The untrusted future reading must not advance the watermark either.
    assert result.source_watermarks["sensor-readings"] == _NOW - timedelta(hours=1)


def test_arrival_order_timestamp_regression_is_flagged() -> None:
    # Second arrival claims a time two hours before the first: beyond tolerance.
    obs = [
        _obs("o-1", 80.0, _NOW),
        _obs("o-2", 79.0, _NOW - timedelta(hours=2)),
    ]
    result = assess_observations(obs, _NOW, as_of=_NOW)
    assert any(
        f.rule_id == "dq://observation/timestamp-regression" for f in result.findings
    )


def test_ingested_at_makes_arrival_order_exact_across_restarts() -> None:
    """Row order hides the regression (rows sorted by observed time, as a
    restart-reloaded store would return them); explicit ingestion timestamps
    recover the true arrival order and flag it."""
    obs = [
        _obs(
            "o-early", 79.0, _NOW - timedelta(hours=2),
            ingested_at=_NOW + timedelta(minutes=5),
        ),
        _obs("o-late", 80.0, _NOW, ingested_at=_NOW + timedelta(minutes=1)),
    ]
    result = assess_observations(obs, _NOW, as_of=_NOW)
    assert any(
        f.rule_id == "dq://observation/timestamp-regression" for f in result.findings
    )
    # Without ingestion timestamps the same rows look clean in row order.
    legacy = [
        _obs("o-early", 79.0, _NOW - timedelta(hours=2)),
        _obs("o-late", 80.0, _NOW),
    ]
    result = assess_observations(legacy, _NOW, as_of=_NOW)
    assert not any(
        f.rule_id == "dq://observation/timestamp-regression" for f in result.findings
    )


def test_event_watermarks_track_mutated_source_contracts() -> None:
    applicator = EventApplicator()
    sources = {"orders": [{"order_id": "order_1", "status": "pending"}]}
    applicator.apply(sources, _event("evt-w1", "2026-06-05T08:00:00Z"))
    applicator.apply(sources, _event("evt-w2", "2026-06-05T09:00:00Z"))
    # Older event arriving later must not move the horizon backwards.
    applicator.apply(sources, _event("evt-w3", "2026-06-05T07:00:00Z"))
    assert applicator.watermarks == {
        "orders": datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
    }


def test_builder_merges_event_watermarks_with_observation_watermarks() -> None:
    from fl_op.snapshot.builder import SnapshotBuilder

    builder = SnapshotBuilder()
    snapshot = builder.build_from_sources(
        {"orders": [], "sensor-readings": []},
        effective_at=_NOW,
        source_watermarks={"orders": _NOW - timedelta(minutes=10)},
    )
    assert snapshot.source_watermarks["orders"] == _NOW - timedelta(minutes=10)


class TestWatermarkFreshness:
    """A published plan's watermarks against the data visible now."""

    @staticmethod
    def _plan(watermarks: dict) -> "object":
        from fl_op.canonical.common import VersionDimensions
        from fl_op.canonical.enums import PlanningMode
        from fl_op.canonical.plan import Plan

        return Plan(
            plan_id="plan-1",
            revision_id="rev-1",
            origin_plan_id="plan-1",
            planning_mode=PlanningMode.ROLLING,
            snapshot_id="snap-0",
            version_dimensions=VersionDimensions(),
            adapter_id="ortools-rolling",
            adapter_version="0.1.0",
            generated_at=_NOW,
            effective_from=_NOW,
            source_watermarks=watermarks,
        )

    @staticmethod
    def _snapshot(watermarks: dict) -> "object":
        from fl_op.canonical.common import TimeInterval, VersionDimensions
        from fl_op.canonical.enums import PlanningMode
        from fl_op.canonical.snapshot import PlanningSnapshot

        return PlanningSnapshot(
            snapshot_id="snap-1",
            effective_at=_NOW,
            generated_at=_NOW,
            planning_mode=PlanningMode.ROLLING,
            planning_horizon=TimeInterval(**{"from": _NOW}),
            version_dimensions=VersionDimensions(),
            source_watermarks=watermarks,
        )

    def test_newer_visible_data_marks_the_plan_stale(self):
        from fl_op.stream.freshness import newly_visible_sources, should_replan

        plan = self._plan({"orders": _NOW - timedelta(hours=1)})
        snapshot = self._snapshot({"orders": _NOW})
        newly = newly_visible_sources(plan, snapshot)
        assert "orders" in newly
        assert should_replan(plan, snapshot)

    def test_plan_covering_visible_data_is_fresh(self):
        from fl_op.stream.freshness import newly_visible_sources, should_replan

        watermarks = {"orders": _NOW, "sensor-readings": _NOW - timedelta(hours=2)}
        plan = self._plan(watermarks)
        snapshot = self._snapshot(dict(watermarks))
        assert newly_visible_sources(plan, snapshot) == {}
        assert not should_replan(plan, snapshot)

    def test_source_the_plan_never_saw_is_newly_visible(self):
        from fl_op.stream.freshness import newly_visible_sources

        plan = self._plan({})
        snapshot = self._snapshot({"sensor-readings": _NOW})
        newly = newly_visible_sources(plan, snapshot)
        assert newly["sensor-readings"]["plan"] is None


def test_replayed_event_id_is_skipped_idempotently() -> None:
    applicator = EventApplicator()
    sources = {"orders": [{"order_id": "order_1", "status": "pending"}]}
    event = _event("evt-dup", "2026-06-05T08:00:00Z")
    assert applicator.apply(sources, event) is True
    assert sources["orders"][0]["status"] == "started"
    sources["orders"][0]["status"] = "pending"
    assert applicator.apply(sources, event) is False
    assert sources["orders"][0]["status"] == "pending"


class TestDurableEventDedup:
    """The dedup store makes replay suppression survive process restarts."""

    def test_published_ids_persist_across_store_instances(self, tmp_path):
        from fl_op.stream.dedup import EventDedupStore

        path = tmp_path / "event-dedup.ids"
        store = EventDedupStore(path)
        assert "evt-1" not in store
        store.record_published(["evt-1", "evt-2", "evt-2", ""])
        assert len(store) == 2

        restarted = EventDedupStore(path)
        assert "evt-1" in restarted
        assert "evt-2" in restarted
        assert "evt-3" not in restarted

    def test_store_compacts_past_retention_bound(self, tmp_path, monkeypatch):
        from fl_op.stream import dedup as dedup_module
        from fl_op.stream.dedup import EventDedupStore

        monkeypatch.setattr(dedup_module, "EVENT_DEDUP_MAX_IDS", 3)
        store = EventDedupStore(tmp_path / "event-dedup.ids")
        store.record_published([f"evt-{n}" for n in range(5)])
        assert len(store) == 3
        assert "evt-0" not in store
        assert "evt-4" in store

    def test_applicator_suppresses_ids_published_by_earlier_runs(self, tmp_path):
        from fl_op.stream.dedup import EventDedupStore

        path = tmp_path / "event-dedup.ids"
        EventDedupStore(path).record_published(["evt-published"])

        applicator = EventApplicator(dedup_store=EventDedupStore(path))
        sources = {"orders": [{"order_id": "order_1", "status": "pending"}]}
        event = _event("evt-published", "2026-06-05T08:00:00Z")
        assert applicator.apply(sources, event) is False
        assert sources["orders"][0]["status"] == "pending"
        # A fresh id still applies normally.
        assert applicator.apply(sources, _event("evt-new", "2026-06-05T08:01:00Z"))
        assert sources["orders"][0]["status"] == "started"


def test_observation_correction_upserts_by_reading_id() -> None:
    applicator = EventApplicator()
    sources = {"sensor-readings": [{"reading_id": "r-1", "value": 80.0}]}
    corrected = {"reading_id": "r-1", "value": 30.0}
    event = ExecutionEvent(
        event_id="evt-corr",
        event_type=EVENT_OBSERVATION_RECORDED,
        observed_at="2026-06-05T08:00:00Z",
        entity_ref="sensor_1",
        payload=corrected,
    )
    applicator.apply(sources, event)
    # Upserted by reading_id (one row, corrected value) and stamped with an
    # arrival time so the series orders by ingestion, not source row order.
    assert len(sources["sensor-readings"]) == 1
    row = sources["sensor-readings"][0]
    assert row["reading_id"] == "r-1" and row["value"] == 30.0
    assert row["ingested_at"] == "2026-06-05T08:00:00Z"


def test_events_within_convergence_window_coalesce() -> None:
    events = [
        _event("e-1", "2026-06-05T08:00:00Z"),
        _event("e-2", "2026-06-05T08:00:30Z"),
        _event("e-3", "2026-06-05T08:05:00Z"),
    ]
    batches = _coalesce(events, window_s=60.0, fallback=_NOW)
    assert [len(b) for b in batches] == [2, 1]
    assert [b[-1].event_id for b in batches] == ["e-2", "e-3"]


def test_zero_window_keeps_one_revision_per_event() -> None:
    events = [_event("e-1", "2026-06-05T08:00:00Z"), _event("e-2", "2026-06-05T08:00:01Z")]
    assert [len(b) for b in _coalesce(events, window_s=0.0, fallback=_NOW)] == [1, 1]
