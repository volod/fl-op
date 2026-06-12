"""Binding-driven event application: no hardcoded domain column names."""

import pytest

from fl_op.stream.apply import EventApplicator
from fl_op.stream.source import (
    EVENT_ASSET_UNAVAILABLE,
    EVENT_ENTITY_CORRECTED,
    EVENT_FORECAST_UPDATED,
    EVENT_INVENTORY_ADJUSTED,
    EVENT_OBSERVATION_RECORDED,
    EVENT_ORDER_CANCELLED,
    EVENT_ORDER_CREATED,
    EVENT_TASK_PROGRESS,
    EVENT_TASK_STARTED,
    ExecutionEvent,
)


@pytest.fixture(scope="module")
def applicator() -> EventApplicator:
    return EventApplicator()


_EVENT_SEQ = iter(range(10_000))


def _event(event_type: str, entity_ref: str = "", payload: dict | None = None) -> ExecutionEvent:
    return ExecutionEvent(
        event_id=f"evt-{next(_EVENT_SEQ)}",
        event_type=event_type,
        observed_at="2026-06-05T08:00:00Z",
        entity_ref=entity_ref,
        payload=payload or {},
    )


def _sources() -> dict:
    return {
        "orders": [
            {"order_id": "order_1", "status": "pending", "area_ha": "100.0"},
            {"order_id": "order_2", "status": "pending", "area_ha": "50.0"},
        ],
        "vehicles": [
            {"vehicle_id": "vehicle_1"},
            {"vehicle_id": "vehicle_2"},
        ],
        "operators": [
            {"operator_id": "operator_1"},
            {"operator_id": "operator_2"},
        ],
        "depots": [
            {"depot_id": "depot_1", "lat": 50.0, "lon": 28.0, "fuel_available_l": 1000.0},
        ],
        "weather": [
            {"window_id": "w-1", "wind_ms": 3.0},
        ],
        "sensor-readings": [],
    }


def test_task_started_sets_status_via_mapping(applicator: EventApplicator) -> None:
    sources = _sources()
    applicator.apply(sources, _event(EVENT_TASK_STARTED, entity_ref="order_1"))
    assert sources["orders"][0]["status"] == "started"
    assert sources["orders"][1]["status"] == "pending"


def test_order_cancelled_removes_task_row(applicator: EventApplicator) -> None:
    sources = _sources()
    applicator.apply(sources, _event(EVENT_ORDER_CANCELLED, entity_ref="order_2"))
    assert [o["order_id"] for o in sources["orders"]] == ["order_1"]


def test_order_created_appends_payload(applicator: EventApplicator) -> None:
    sources = _sources()
    payload = {"order_id": "order_3", "status": "pending"}
    applicator.apply(sources, _event(EVENT_ORDER_CREATED, payload=payload))
    assert sources["orders"][-1]["order_id"] == "order_3"


def test_asset_unavailable_removes_asset_row(applicator: EventApplicator) -> None:
    sources = _sources()
    applicator.apply(sources, _event(EVENT_ASSET_UNAVAILABLE, entity_ref="vehicle_1"))
    assert [v["vehicle_id"] for v in sources["vehicles"]] == ["vehicle_2"]


def test_observation_recorded_appends_reading(applicator: EventApplicator) -> None:
    sources = _sources()
    reading = {
        "reading_id": "reading_9",
        "sensor_id": "sensor_1",
        "metric": "battery-level",
        "value": 12.0,
        "observed_at": "2026-06-05T08:00:00+00:00",
    }
    applicator.apply(sources, _event(EVENT_OBSERVATION_RECORDED, entity_ref="sensor_1", payload=reading))
    assert sources["sensor-readings"] == [reading]


def test_entity_corrected_replaces_row_by_key(applicator: EventApplicator) -> None:
    sources = _sources()
    corrected = {"vehicle_id": "vehicle_1", "rated_power_kw": "210.0"}
    applicator.apply(sources, _event(EVENT_ENTITY_CORRECTED, entity_ref="vehicle_1", payload=corrected))
    assert sources["vehicles"][0] == corrected
    assert len(sources["vehicles"]) == 2


def test_entity_corrected_appends_when_row_absent(applicator: EventApplicator) -> None:
    sources = _sources()
    corrected = {"order_id": "order_99", "status": "pending"}
    applicator.apply(sources, _event(EVENT_ENTITY_CORRECTED, entity_ref="order_99", payload=corrected))
    assert sources["orders"][-1] == corrected


def test_task_progress_reduces_remaining_work_and_marks_started(
    applicator: EventApplicator,
) -> None:
    sources = _sources()
    event = _event(EVENT_TASK_PROGRESS, entity_ref="order_1", payload={"completed_fraction": 0.4})
    applicator.apply(sources, event)
    assert sources["orders"][0]["area_ha"] == 60.0
    assert sources["orders"][0]["status"] == "started"


def test_task_progress_completion_removes_task(applicator: EventApplicator) -> None:
    sources = _sources()
    event = _event(EVENT_TASK_PROGRESS, entity_ref="order_2", payload={"completed_fraction": 1.0})
    applicator.apply(sources, event)
    assert [o["order_id"] for o in sources["orders"]] == ["order_1"]


def test_task_progress_absolute_remaining_overwrites_work(
    applicator: EventApplicator,
) -> None:
    sources = _sources()
    event = _event(
        EVENT_TASK_PROGRESS, entity_ref="order_1", payload={"remaining_quantity": 37.5}
    )
    applicator.apply(sources, event)
    assert sources["orders"][0]["area_ha"] == 37.5
    assert sources["orders"][0]["status"] == "started"


def test_task_progress_zero_remaining_removes_task(
    applicator: EventApplicator,
) -> None:
    sources = _sources()
    event = _event(
        EVENT_TASK_PROGRESS, entity_ref="order_2", payload={"remaining_quantity": 0.0}
    )
    applicator.apply(sources, event)
    assert [o["order_id"] for o in sources["orders"]] == ["order_1"]


def test_task_progress_absolute_remaining_wins_over_fraction(
    applicator: EventApplicator,
) -> None:
    sources = _sources()
    event = _event(
        EVENT_TASK_PROGRESS,
        entity_ref="order_1",
        payload={"remaining_quantity": 80.0, "completed_fraction": 0.5},
    )
    applicator.apply(sources, event)
    assert sources["orders"][0]["area_ha"] == 80.0


def test_operator_unavailable_via_asset_event(applicator: EventApplicator) -> None:
    sources = _sources()
    applicator.apply(sources, _event(EVENT_ASSET_UNAVAILABLE, entity_ref="operator_1"))
    assert [o["operator_id"] for o in sources["operators"]] == ["operator_2"]
    assert len(sources["vehicles"]) == 2


def test_inventory_adjusted_merges_partial_fields(applicator: EventApplicator) -> None:
    sources = _sources()
    event = _event(
        EVENT_INVENTORY_ADJUSTED,
        entity_ref="depot_1",
        payload={"depot_id": "depot_1", "fuel_available_l": 250.0},
    )
    applicator.apply(sources, event)
    depot = sources["depots"][0]
    assert depot["fuel_available_l"] == 250.0
    assert depot["lat"] == 50.0  # untouched fields survive the merge


def test_forecast_updated_with_payload_upserts_window(applicator: EventApplicator) -> None:
    sources = _sources()
    invalidated = {"window_id": "w-1", "wind_ms": 18.0}
    event = _event(EVENT_FORECAST_UPDATED, entity_ref="w-1", payload=invalidated)
    applicator.apply(sources, event)
    assert sources["weather"] == [invalidated]


def test_unknown_event_type_is_noop(applicator: EventApplicator) -> None:
    sources = _sources()
    applicator.apply(sources, _event("custom.unknown", entity_ref="x"))
    assert sources == _sources()


def test_task_completed_removes_task_and_records_lead_time_evidence() -> None:
    applicator = EventApplicator()
    sources = {
        "orders": [
            {"order_id": "order_1", "status": "started", "area_ha": "100.0",
             "deadline": "2026-06-07T00:00:00+00:00"},
        ]
    }
    from fl_op.stream.source import EVENT_TASK_COMPLETED

    applicator.apply(sources, _event(EVENT_TASK_COMPLETED, entity_ref="order_1"))
    assert sources["orders"] == []
    assert len(applicator.completions) == 1
    completion = applicator.completions[0]
    assert completion["task_id"] == "order_1"
    assert completion["deadline"] == "2026-06-07T00:00:00+00:00"
    assert completion["via"] == "event"


def test_full_progress_also_records_a_completion() -> None:
    applicator = EventApplicator()
    sources = {"orders": [{"order_id": "order_1", "status": "started",
                           "area_ha": "10.0", "deadline": "2026-06-07T00:00:00+00:00"}]}
    event = _event(EVENT_TASK_PROGRESS, entity_ref="order_1",
                   payload={"completed_fraction": 1.0})
    applicator.apply(sources, event)
    assert sources["orders"] == []
    assert applicator.completions[0]["via"] == "progress"


def test_work_progress_telemetry_scales_remaining_work() -> None:
    """A work-progress observation drives task progress without an explicit
    task.progress event (raw metric normalized via the mapping's codes)."""
    applicator = EventApplicator()
    sources = _sources()
    reading = {
        "reading_id": "r-progress",
        "sensor_id": "order_1",
        "metric": "work_progress_pct",
        "value": 40.0,
        "observed_at": "2026-06-05T08:00:00+00:00",
    }
    applicator.apply(
        sources,
        _event(EVENT_OBSERVATION_RECORDED, entity_ref="order_1", payload=reading),
    )
    assert sources["orders"][0]["area_ha"] == 60.0
    assert sources["orders"][0]["status"] == "started"


def test_work_progress_telemetry_at_completion_finishes_the_task() -> None:
    applicator = EventApplicator()
    sources = _sources()
    reading = {
        "reading_id": "r-done",
        "sensor_id": "order_2",
        "metric": "work_progress_pct",
        "value": 100.0,
        "observed_at": "2026-06-05T09:00:00+00:00",
    }
    applicator.apply(
        sources,
        _event(EVENT_OBSERVATION_RECORDED, entity_ref="order_2", payload=reading),
    )
    assert [o["order_id"] for o in sources["orders"]] == ["order_1"]
    assert applicator.completions[0]["via"] == "telemetry"


def test_other_metrics_do_not_touch_tasks() -> None:
    applicator = EventApplicator()
    sources = _sources()
    reading = {
        "reading_id": "r-batt",
        "sensor_id": "order_1",
        "metric": "battery_pct",
        "value": 50.0,
        "observed_at": "2026-06-05T08:00:00+00:00",
    }
    applicator.apply(
        sources,
        _event(EVENT_OBSERVATION_RECORDED, entity_ref="order_1", payload=reading),
    )
    assert sources["orders"][0]["area_ha"] == "100.0"
    assert applicator.completions == []
