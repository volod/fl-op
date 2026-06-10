"""Eventual-consistency mechanisms: watermarks, skew, idempotency, coalescing."""

from datetime import datetime, timedelta, timezone

from fl_op.canonical.observation import Observation
from fl_op.core.constants import METRIC_BATTERY_LEVEL
from fl_op.snapshot.assessment import assess_observations
from fl_op.stream.apply import EventApplicator
from fl_op.stream.driver import _coalesce
from fl_op.stream.source import EVENT_OBSERVATION_RECORDED, EVENT_TASK_STARTED, ExecutionEvent

_NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _obs(observation_id: str, value: float, at: datetime) -> Observation:
    return Observation(
        observation_id=observation_id,
        entity_ref="s1",
        metric=METRIC_BATTERY_LEVEL,
        value=value,
        observed_at=at,
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


def test_replayed_event_id_is_skipped_idempotently() -> None:
    applicator = EventApplicator()
    sources = {"orders": [{"order_id": "order_1", "status": "pending"}]}
    event = _event("evt-dup", "2026-06-05T08:00:00Z")
    assert applicator.apply(sources, event) is True
    assert sources["orders"][0]["status"] == "started"
    sources["orders"][0]["status"] = "pending"
    assert applicator.apply(sources, event) is False
    assert sources["orders"][0]["status"] == "pending"


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
    assert sources["sensor-readings"] == [corrected]


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
