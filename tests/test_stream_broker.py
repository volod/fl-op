"""Broker-backed event ingestion: polling, validation, source selection."""

import json
import sys
from datetime import datetime, timezone

import pytest

from fl_op.core import constants
from fl_op.stream.broker import (
    EVENT_SOURCE_JSONL,
    EVENT_SOURCE_KAFKA,
    BrokerEventSource,
    _message_epoch_ms,
    open_dedup_store,
    open_event_source,
    register_event_source,
    registered_event_sources,
)
from fl_op.stream.source import ExecutionEvent, JsonlEventSource, stamp_broker_ingested

# A Kafka record's create-time timestamp type (confluent_kafka.TIMESTAMP_CREATE_TIME).
_TS_CREATE_TIME = 1
_TS_NOT_AVAILABLE = 0


class FakeMessage:
    def __init__(self, value: str | None = None, error: str | None = None) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes | None:
        return self._value.encode("utf-8") if self._value is not None else None

    def error(self) -> str | None:
        return self._error


class FakeTimestampedMessage(FakeMessage):
    """A message that also exposes a Kafka-style (type, ms) record timestamp."""

    def __init__(
        self,
        value: str | None = None,
        error: str | None = None,
        timestamp: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(value=value, error=error)
        self._timestamp = timestamp

    def timestamp(self) -> tuple[int, int] | None:
        return self._timestamp


class FakeConsumer:
    """Scripted consumer: poll() pops entries; None entries are empty polls."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.closed = False
        self.committed = False

    def poll(self, timeout: float):
        if self.script:
            return self.script.pop(0)
        return None

    def commit(self, asynchronous: bool = True) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def _event_json(event_id: str, event_type: str = "observation.recorded") -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type,
            "observed_at": "2026-06-11T08:00:00Z",
            "entity_ref": "sensor-1",
            "payload_json": "{}",
        }
    )


def test_broker_source_yields_validated_events_and_stops_when_drained() -> None:
    consumer = FakeConsumer(
        [
            FakeMessage(value=_event_json("e-1")),
            None,
            FakeMessage(value=_event_json("e-2", "task.started")),
        ]
    )
    source = BrokerEventSource(
        poll_timeout_s=0.0, max_empty_polls=2, consumer_factory=lambda: consumer
    )

    events = list(source)
    assert [e.event_id for e in events] == ["e-1", "e-2"]
    assert events[0].event_type == "observation.recorded"
    # The consumer stays open after the drain: offsets commit only once the
    # caller has published the resulting revisions.
    assert not consumer.closed
    source.close()
    assert consumer.closed
    assert not consumer.committed


def test_broker_source_skips_malformed_and_unsupported_events() -> None:
    consumer = FakeConsumer(
        [
            FakeMessage(value="not-json"),
            FakeMessage(value=_event_json("e-bad", "not.an.event")),
            FakeMessage(error="broker hiccup"),
            FakeMessage(value=_event_json("e-good")),
        ]
    )
    source = BrokerEventSource(
        poll_timeout_s=0.0, max_empty_polls=1, consumer_factory=lambda: consumer
    )

    events = list(source)
    assert [e.event_id for e in events] == ["e-good"]
    source.close()
    assert consumer.closed


def test_commit_after_publication_commits_offsets_and_closes() -> None:
    consumer = FakeConsumer([FakeMessage(value=_event_json("e-1"))])
    source = BrokerEventSource(
        poll_timeout_s=0.0, max_empty_polls=1, consumer_factory=lambda: consumer
    )
    list(source)
    assert not consumer.committed
    source.commit()
    assert consumer.committed
    assert consumer.closed
    # Idempotent: a second commit on a closed source is a no-op.
    source.commit()


def test_open_dedup_store_only_for_broker_backed_runs(monkeypatch, tmp_path) -> None:
    from fl_op.stream import dedup as dedup_module

    monkeypatch.setattr(dedup_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_JSONL)
    assert open_dedup_store() is None
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_KAFKA)
    assert open_dedup_store() is not None
    monkeypatch.setattr(constants, "EVENT_DEDUP_STORE_ENABLED", False)
    assert open_dedup_store() is None


def test_broker_source_without_client_raises_actionable_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "confluent_kafka", None)
    source = BrokerEventSource(max_empty_polls=1)
    with pytest.raises(RuntimeError, match=r"fl-op\[broker\]"):
        list(source)


def test_open_event_source_jsonl_reads_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_JSONL)
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(_event_json("e-1") + "\n")

    source = open_event_source(str(events_file))
    assert isinstance(source, JsonlEventSource)
    assert [e.event_id for e in source] == ["e-1"]


def test_open_event_source_jsonl_without_path_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_JSONL)
    assert list(open_event_source(None)) == []


def test_open_event_source_kafka_returns_broker_source(monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_KAFKA)
    assert isinstance(open_event_source(None), BrokerEventSource)


def test_register_custom_event_source_uses_factory_and_dedup_flag(
    monkeypatch,
    tmp_path,
) -> None:
    from fl_op.stream import dedup as dedup_module

    expected = ExecutionEvent(
        event_id="custom-1",
        event_type="task.started",
        observed_at="2026-06-11T08:00:00Z",
        entity_ref="task-1",
        payload={},
    )
    seen: dict[str, str | None] = {}

    def factory(events_path: str | None):
        seen["events_path"] = events_path
        return [expected]

    register_event_source("custom-test", factory)
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", "custom-test")

    assert "custom-test" in registered_event_sources()
    assert list(open_event_source("feed-id")) == [expected]
    assert seen["events_path"] == "feed-id"
    assert open_dedup_store() is None

    register_event_source("custom-test-durable", factory, uses_dedup_store=True)
    monkeypatch.setattr(dedup_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", "custom-test-durable")
    assert open_dedup_store() is not None


def test_open_event_source_rejects_unknown_kind(monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", "unsupported-source")
    with pytest.raises(ValueError, match="EVENT_SOURCE_KIND"):
        open_event_source(None)


def _drain(consumer: FakeConsumer) -> list[ExecutionEvent]:
    source = BrokerEventSource(
        poll_timeout_s=0.0, max_empty_polls=1, consumer_factory=lambda: consumer
    )
    return list(source)


def test_broker_stamps_record_timestamp_when_producer_omits_ingested_at() -> None:
    ms = 1_700_000_000_000
    consumer = FakeConsumer(
        [FakeTimestampedMessage(value=_event_json("e-1"), timestamp=(_TS_CREATE_TIME, ms))]
    )
    [event] = _drain(consumer)
    # The record's broker arrival time becomes a true ingested_at.
    assert event.ingested_at == datetime.fromtimestamp(
        ms / 1000.0, tz=timezone.utc
    ).isoformat()


def test_broker_keeps_producer_ingested_at_over_record_timestamp() -> None:
    body = json.dumps(
        {
            "event_id": "e-1",
            "event_type": "observation.recorded",
            "observed_at": "2026-06-11T08:00:00Z",
            "entity_ref": "sensor-1",
            "payload_json": "{}",
            "ingested_at": "2026-06-11T08:00:30+00:00",
        }
    )
    consumer = FakeConsumer(
        [FakeTimestampedMessage(value=body, timestamp=(_TS_CREATE_TIME, 1_700_000_000_000))]
    )
    [event] = _drain(consumer)
    assert event.ingested_at == "2026-06-11T08:00:30+00:00"


def test_broker_unavailable_timestamp_leaves_ingested_at_to_proxy() -> None:
    consumer = FakeConsumer(
        [FakeTimestampedMessage(value=_event_json("e-1"), timestamp=(_TS_NOT_AVAILABLE, -1))]
    )
    [event] = _drain(consumer)
    assert event.ingested_at == ""


def test_broker_timestampless_message_leaves_ingested_at_to_proxy() -> None:
    # The plain FakeMessage has no timestamp(): the proxy applies downstream.
    [event] = _drain(FakeConsumer([FakeMessage(value=_event_json("e-1"))]))
    assert event.ingested_at == ""


def test_message_epoch_ms_reads_available_timestamps_only() -> None:
    assert _message_epoch_ms(FakeTimestampedMessage(timestamp=(_TS_CREATE_TIME, 1234))) == 1234.0
    assert _message_epoch_ms(FakeTimestampedMessage(timestamp=(_TS_NOT_AVAILABLE, -1))) is None
    assert _message_epoch_ms(FakeMessage()) is None  # no timestamp() at all


def test_stamp_broker_ingested_fills_blank_only() -> None:
    base = ExecutionEvent(
        event_id="e", event_type="task.started",
        observed_at="2026-06-11T08:00:00Z", entity_ref="t", payload={},
    )
    ms = 1_700_000_000_000
    expected = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()
    assert stamp_broker_ingested(base, ms).ingested_at == expected
    # A missing or non-positive broker arrival is a no-op (proxy still applies).
    assert stamp_broker_ingested(base, None).ingested_at == ""
    assert stamp_broker_ingested(base, 0).ingested_at == ""
    # A producer-supplied arrival always wins.
    producer = ExecutionEvent(
        event_id="e", event_type="task.started",
        observed_at="2026-06-11T08:00:00Z", entity_ref="t", payload={},
        ingested_at="2026-06-11T08:00:30+00:00",
    )
    assert stamp_broker_ingested(producer, ms).ingested_at == "2026-06-11T08:00:30+00:00"
