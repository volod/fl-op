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

# Canonical replanning-trigger event vocabulary. Operator unavailability needs
# no dedicated type: operators are assets, so `asset.unavailable` with an
# operator id removes them through the same binding-driven path.
EVENT_TASK_STARTED = "task.started"
EVENT_TASK_PROGRESS = "task.progress"
EVENT_TASK_COMPLETED = "task.completed"
EVENT_ORDER_CREATED = "order.created"
EVENT_ORDER_CANCELLED = "order.cancelled"
EVENT_ASSET_UNAVAILABLE = "asset.unavailable"
EVENT_FORECAST_UPDATED = "forecast.updated"
EVENT_OBSERVATION_RECORDED = "observation.recorded"
EVENT_ENTITY_CORRECTED = "entity.corrected"
EVENT_INVENTORY_ADJUSTED = "inventory.adjusted"

# Replanning-trigger event types the stream layer supports.
SUPPORTED_EVENT_TYPES = {
    EVENT_TASK_STARTED,
    EVENT_TASK_PROGRESS,
    EVENT_TASK_COMPLETED,
    EVENT_ORDER_CREATED,
    EVENT_ORDER_CANCELLED,
    EVENT_ASSET_UNAVAILABLE,
    EVENT_FORECAST_UPDATED,
    EVENT_OBSERVATION_RECORDED,
    EVENT_ENTITY_CORRECTED,
    EVENT_INVENTORY_ADJUSTED,
}


@dataclass
class ExecutionEvent:
    event_id: str
    event_type: str
    observed_at: str
    entity_ref: str
    payload: dict[str, Any]
    # When the platform saw the event, distinct from observed_at (when it
    # happened). Optional: producers that stamp it let event-derived
    # observations order by arrival; absent, the observed time is the proxy.
    ingested_at: str = ""


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
        ingested_at=record.get("ingested_at", ""),
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
