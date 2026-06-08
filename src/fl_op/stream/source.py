"""Python-native event stream source.

Reads a JSONL file of execution events and validates each against the
execution-events Avro schema's field set. No broker or JVM is involved; this is
the stream analogue of the batch CSV importer.
"""

import json
import logging
import pathlib
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Replanning-trigger event types supported by the rolling demo.
SUPPORTED_EVENT_TYPES = {
    "task.started",
    "order.created",
    "order.cancelled",
    "asset.unavailable",
    "forecast.updated",
}


@dataclass
class ExecutionEvent:
    event_id: str
    event_type: str
    observed_at: str
    entity_ref: str
    payload: dict[str, Any]


def parse_event(record: dict[str, Any]) -> ExecutionEvent:
    """Normalize a raw event dict into an ExecutionEvent, parsing payload_json."""
    payload = record.get("payload")
    if payload is None:
        raw = record.get("payload_json", "{}")
        payload = json.loads(raw) if isinstance(raw, str) else (raw or {})
    event_type = record.get("event_type", "")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(
            f"Unsupported event type '{event_type}'. Supported: {sorted(SUPPORTED_EVENT_TYPES)}"
        )
    return ExecutionEvent(
        event_id=record.get("event_id", ""),
        event_type=event_type,
        observed_at=record.get("observed_at", ""),
        entity_ref=record.get("entity_ref", ""),
        payload=payload,
    )


class JsonlEventSource:
    """Yields validated ExecutionEvents from a JSONL file."""

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)

    def __iter__(self) -> Iterator[ExecutionEvent]:
        with self.path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield parse_event(json.loads(line))
