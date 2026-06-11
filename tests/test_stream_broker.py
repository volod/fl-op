"""Broker-backed event ingestion: polling, validation, source selection."""

import json
import sys

import pytest

from fl_op.core import constants
from fl_op.stream.broker import (
    EVENT_SOURCE_JSONL,
    EVENT_SOURCE_KAFKA,
    BrokerEventSource,
    open_event_source,
)
from fl_op.stream.source import JsonlEventSource


class FakeMessage:
    def __init__(self, value: str | None = None, error: str | None = None) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes | None:
        return self._value.encode("utf-8") if self._value is not None else None

    def error(self) -> str | None:
        return self._error


class FakeConsumer:
    """Scripted consumer: poll() pops entries; None entries are empty polls."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.closed = False

    def poll(self, timeout: float):
        if self.script:
            return self.script.pop(0)
        return None

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
    assert consumer.closed


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
    assert consumer.closed


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


def test_open_event_source_rejects_unknown_kind(monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", "carrier-pigeon")
    with pytest.raises(ValueError, match="EVENT_SOURCE_KIND"):
        open_event_source(None)
