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
EventSourceFactory = Callable[[Optional[str]], Iterable[ExecutionEvent]]

_EVENT_SOURCE_FACTORIES: dict[str, EventSourceFactory] = {}
_DEDUP_EVENT_SOURCE_KINDS: set[str] = set()


def register_event_source(
    kind: str,
    factory: EventSourceFactory,
    *,
    uses_dedup_store: bool = False,
) -> None:
    """Register an EVENT_SOURCE_KIND implementation.

    The factory receives the optional CLI events path and returns an iterable
    of validated ExecutionEvents. Broker-like sources that can redeliver
    events should set uses_dedup_store so rolling planning attaches the
    durable event-id store after revision publication.
    """
    if not kind or kind.strip() != kind:
        raise ValueError("event source kind must be a non-empty trimmed string")
    _EVENT_SOURCE_FACTORIES[kind] = factory
    if uses_dedup_store:
        _DEDUP_EVENT_SOURCE_KINDS.add(kind)
    else:
        _DEDUP_EVENT_SOURCE_KINDS.discard(kind)


def registered_event_sources() -> tuple[str, ...]:
    """Return the configured EVENT_SOURCE_KIND values."""
    return tuple(sorted(_EVENT_SOURCE_FACTORIES))


class BrokerEventSource:
    """Yields validated ExecutionEvents from a broker topic.

    Malformed payloads and unsupported event types are logged and skipped
    rather than failing the stream: one bad producer must not stall rolling
    replanning for everyone else.

    Offsets are never auto-committed: the consumer stays open after the
    drain and the caller calls ``commit()`` once the resulting revisions are
    published (or ``close()`` to abandon them). A crash before the commit
    therefore redelivers the backlog instead of losing it - at-least-once
    delivery, made effectively-once by the durable event-id dedup store.
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
        self._consumer: Optional[Any] = None

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
                # Offsets commit only on commit(), after revision publication.
                "enable.auto.commit": False,
            }
        )
        consumer.subscribe([self.topic])
        return consumer

    def __iter__(self) -> Iterator[ExecutionEvent]:
        consumer = self._consumer = self._create_consumer()
        empty_polls = 0
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

    def commit(self) -> None:
        """Commit the consumed offsets and close; call after publication."""
        if self._consumer is None:
            return
        try:
            self._consumer.commit(asynchronous=False)
            logger.info("Committed broker offsets after revision publication")
        finally:
            self._consumer.close()
            self._consumer = None

    def close(self) -> None:
        """Close without committing: the drained backlog will be redelivered."""
        if self._consumer is not None:
            self._consumer.close()
            self._consumer = None


def open_dedup_store() -> Optional[Any]:
    """The durable event-id dedup store for the configured event source.

    Only registered broker-backed ingestion uses it (redelivery there is
    unintentional); the JSONL development source replays event files on
    purpose and must not be suppressed. EVENT_DEDUP_STORE_ENABLED=0 disables
    it entirely.
    """
    if constants.EVENT_SOURCE_KIND not in _DEDUP_EVENT_SOURCE_KINDS:
        return None
    if not constants.EVENT_DEDUP_STORE_ENABLED:
        return None
    from fl_op.stream.dedup import EventDedupStore

    return EventDedupStore()


def _jsonl_event_source(events_path: Optional[str]) -> Iterable[ExecutionEvent]:
    if events_path is None:
        return []
    return JsonlEventSource(events_path)


def _kafka_event_source(events_path: Optional[str]) -> Iterable[ExecutionEvent]:
    if events_path is not None:
        logger.warning(
            "EVENT_SOURCE_KIND=kafka: ignoring events file %s", events_path
        )
    return BrokerEventSource()


def open_event_source(events_path: Optional[str]) -> Iterable[ExecutionEvent]:
    """Resolve the configured execution-event source for a rolling run.

    EVENT_SOURCE_KIND selects a registered implementation: 'jsonl' (default)
    reads the given events file (no file means no events), 'kafka' consumes
    the configured broker topic and ignores the file path. Integrations can
    register additional sources without changing the stream driver.
    """
    kind = constants.EVENT_SOURCE_KIND
    factory = _EVENT_SOURCE_FACTORIES.get(kind)
    if factory is not None:
        return factory(events_path)
    raise ValueError(
        f"Unknown EVENT_SOURCE_KIND '{kind}'; expected "
        f"one of {', '.join(registered_event_sources())}"
    )


register_event_source(EVENT_SOURCE_JSONL, _jsonl_event_source)
register_event_source(
    EVENT_SOURCE_KAFKA,
    _kafka_event_source,
    uses_dedup_store=True,
)
