"""Event visibility: producers stamp a true ingested_at, so purely event-fed
observation series order by arrival and flag arrival-order regressions instead
of approximating arrival by source row order."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np

from fl_op.data.ingestion import stamp_ingested
from fl_op.mapping import MappingEngine
from fl_op.planning.demo import generate_demo_events
from fl_op.snapshot.assessment import assess_observations
from fl_op.stream.apply import EventApplicator
from fl_op.stream.source import EVENT_OBSERVATION_RECORDED, ExecutionEvent

_NOW = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)


def _observation_event(
    event_id: str,
    reading_id: str,
    observed_at: str,
    value: float,
    ingested_at: str = "",
) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=event_id,
        event_type=EVENT_OBSERVATION_RECORDED,
        observed_at=observed_at,
        entity_ref="sensor_stream_only",
        payload={
            "reading_id": reading_id,
            "sensor_id": "sensor_stream_only",
            "metric": "battery-level",
            "value": value,
            "observed_at": observed_at,
        },
        ingested_at=ingested_at,
    )


def _apply_and_map(events: list[ExecutionEvent]) -> list:
    """Feed a purely event-fed observation series through the real agricultural
    sensor-readings mapping (which binds ingestedAt) and return the canonical
    observations."""
    applicator = EventApplicator()
    sources: dict[str, list] = {"sensor-readings": []}
    for event in events:
        applicator.apply(sources, event)
    return MappingEngine().map_dataset("sensor-readings", sources["sensor-readings"]).observations


def test_stamp_ingested_adds_bounded_delay() -> None:
    rng = np.random.default_rng(0)
    observed = _NOW
    arrival = datetime.fromisoformat(stamp_ingested(observed, rng))
    delay = (arrival - observed).total_seconds()
    assert 0.0 <= delay <= 120.0  # INGESTION_DELAY_MAX_S


def test_producer_stamped_arrival_flags_regression_for_event_fed_series() -> None:
    # The later-observed reading (08:00) arrives first; the earlier reading
    # (06:00) arrives ten minutes later -- an arrival-order regression a real
    # producer makes visible by stamping a true ingested_at on each event.
    events = [
        _observation_event(
            "evt-late-obs-early-arrival", "r-1",
            "2026-06-05T08:00:00+00:00", 80.0,
            ingested_at="2026-06-05T08:05:00+00:00",
        ),
        _observation_event(
            "evt-early-obs-late-arrival", "r-2",
            "2026-06-05T06:00:00+00:00", 79.0,
            ingested_at="2026-06-05T08:15:00+00:00",
        ),
    ]
    observations = _apply_and_map(events)
    # Every event-derived reading carries the producer-stamped arrival time.
    assert observations and all(o.ingested_at is not None for o in observations)

    result = assess_observations(observations, _NOW, as_of=_NOW)
    assert any(
        f.rule_id == "dq://observation/timestamp-regression" for f in result.findings
    )


def test_observed_time_proxy_hides_event_fed_regression() -> None:
    # Without a producer-stamped ingested_at the event path falls back to the
    # observed time as the arrival proxy, so the same readings look in order.
    events = [
        _observation_event(
            "evt-no-arrival-1", "r-1", "2026-06-05T08:00:00+00:00", 80.0,
        ),
        _observation_event(
            "evt-no-arrival-2", "r-2", "2026-06-05T06:00:00+00:00", 79.0,
        ),
    ]
    observations = _apply_and_map(events)
    result = assess_observations(observations, _NOW, as_of=_NOW)
    assert not any(
        f.rule_id == "dq://observation/timestamp-regression" for f in result.findings
    )


def test_demo_events_carry_a_true_ingested_at(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "assignments": [
                    {"task_id": "order_1", "asset_ids": ["vehicle_1"]},
                    {"task_id": "order_2", "asset_ids": ["vehicle_2"]},
                ]
            }
        )
    )

    events_path = generate_demo_events(str(tmp_path), plan_dir)
    events = [
        json.loads(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    assert events
    for event in events:
        assert event["ingested_at"]
        # Arrival never precedes the observed time.
        arrival = datetime.fromisoformat(event["ingested_at"])
        observed = datetime.fromisoformat(event["observed_at"])
        assert arrival >= observed
