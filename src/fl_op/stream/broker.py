"""Broker-backed execution-event ingestion (event bus).

Consumes ``observation.recorded`` and the other replanning-trigger events
from a Kafka topic, validating every message through the same ``parse_event``
the JSONL source uses, so the two sources are interchangeable upstream of the
stream driver. The Kafka client is an optional dependency
(``pip install 'fl-op[broker]'``); a ``consumer_factory`` can be injected for
tests or alternative broker clients.

Consumption is bounded by default: after ``EVENT_BROKER_MAX_EMPTY_POLLS``
consecutive empty polls the source stops, so a rolling run drains the visible
backlog and terminates exactly like its JSONL counterpart. Pass
``max_empty_polls=None`` for an unbounded daemon-style consumer.
"""

import json
import logging
from typing import Any, Callable, Iterable, Iterator, Optional

from fl_op.core import constants
from fl_op.stream.source import ExecutionEvent, JsonlEventSource, parse_event

logger = logging.getLogger(__name__)

# Recognized EVENT_SOURCE_KIND values.
EVENT_SOURCE_JSONL = "jsonl"
EVENT_SOURCE_KAFKA = "kafka"


class BrokerEventSource:
    """Yields validated ExecutionEvents from a broker topic.

    Malformed payloads and unsupported event types are logged and skipped
    rather than failing the stream: one bad producer must not stall rolling
    replanning for everyone else.
    """

    def __init__(
        self,
        topic: str = constants.EVENT_BROKER_TOPIC,
        bootstrap_servers: str = constants.EVENT_BROKER_BOOTSTRAP_SERVERS,
        group_id: str = constants.EVENT_BROKER_GROUP_ID,
        poll_timeout_s: float = constants.EVENT_BROKER_POLL_TIMEOUT_S,
        max_empty_polls: Optional[int] = constants.EVENT_BROKER_MAX_EMPTY_POLLS,
        consumer_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.topic = topic
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.poll_timeout_s = poll_timeout_s
        self.max_empty_polls = max_empty_polls
        self._consumer_factory = consumer_factory

    def _create_consumer(self) -> Any:
        if self._consumer_factory is not None:
            return self._consumer_factory()
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:
            raise RuntimeError(
                "EVENT_SOURCE_KIND=kafka requires the broker extra: "
                "pip install 'fl-op[broker]'"
            ) from exc
        consumer = Consumer(
            {
                "bootstrap.servers": self.bootstrap_servers,
                "group.id": self.group_id,
                "auto.offset.reset": "earliest",
            }
        )
        consumer.subscribe([self.topic])
        return consumer

    def __iter__(self) -> Iterator[ExecutionEvent]:
        consumer = self._create_consumer()
        empty_polls = 0
        try:
            while self.max_empty_polls is None or empty_polls < self.max_empty_polls:
                message = consumer.poll(self.poll_timeout_s)
                if message is None:
                    empty_polls += 1
                    continue
                if message.error():
                    logger.warning("Broker message error: %s", message.error())
                    continue
                empty_polls = 0
                raw = message.value()
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    yield parse_event(json.loads(raw))
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning("Skipping malformed broker event: %s", exc)
        finally:
            consumer.close()


def open_event_source(events_path: Optional[str]) -> Iterable[ExecutionEvent]:
    """Resolve the configured execution-event source for a rolling run.

    EVENT_SOURCE_KIND selects the implementation: 'jsonl' (default) reads the
    given events file (no file means no events), 'kafka' consumes the
    configured broker topic and ignores the file path.
    """
    kind = constants.EVENT_SOURCE_KIND
    if kind == EVENT_SOURCE_JSONL:
        if events_path is None:
            return []
        return JsonlEventSource(events_path)
    if kind == EVENT_SOURCE_KAFKA:
        if events_path is not None:
            logger.warning(
                "EVENT_SOURCE_KIND=kafka: ignoring events file %s", events_path
            )
        return BrokerEventSource()
    raise ValueError(
        f"Unknown EVENT_SOURCE_KIND '{kind}'; expected "
        f"'{EVENT_SOURCE_JSONL}' or '{EVENT_SOURCE_KAFKA}'"
    )
