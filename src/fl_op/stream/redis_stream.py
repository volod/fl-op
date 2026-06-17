"""Redis Streams execution-event source (a production event-client adapter).

A worked example of the small adapter package the broker SPI invites, on a
Pythonic client (``redis``) instead of a vendor SDK: it registers an
``EVENT_SOURCE_KIND`` factory and opts into the durable dedup store because
Redis consumer groups are at-least-once and can redeliver. Like the Kafka client
it validates every body through the same ``parse_event`` and is bounded by
default (stop after ``EVENT_REDIS_MAX_EMPTY_POLLS`` empty reads), so a rolling
run drains the visible backlog and terminates exactly like its JSONL counterpart.

A read happens in two phases so the guarantee survives a restart: first this
consumer's *pending* (delivered-but-unacked) entries are re-read - the backlog a
crashed prior run left in the group's pending-entries list - then new entries
(``>``). Entries are acknowledged (``XACK``) only on :meth:`commit`, after the
run publishes the resulting revisions and records their event ids: a crash
before that leaves them pending (redelivered next run, nothing lost), and a
redelivery after publication is suppressed by the dedup store (nothing
duplicated) - effectively-once end to end. ``redis`` is an optional dependency
(``pip install 'fl-op[redis]'``), imported lazily with an actionable error; a
``client_factory`` can be injected for tests.
"""

import json
import logging
from typing import Any, Callable, Iterable, Iterator, Optional

from fl_op.core import constants
from fl_op.stream.broker import register_event_source
from fl_op.stream.source import ExecutionEvent, parse_event

logger = logging.getLogger(__name__)

EVENT_SOURCE_REDIS = "redis"

# XREADGROUP id sentinels: ">" delivers never-seen entries, "0" pages this
# consumer's already-delivered-but-unacked backlog.
_NEW_MESSAGES = ">"
_PENDING_START = "0"


class RedisStreamEventSource:
    """Yields validated ExecutionEvents from a Redis stream, acking on commit."""

    def __init__(
        self,
        url: str = constants.EVENT_REDIS_URL,
        host: str = constants.EVENT_REDIS_HOST,
        port: int = constants.EVENT_REDIS_PORT,
        db: int = constants.EVENT_REDIS_DB,
        stream: str = constants.EVENT_REDIS_STREAM,
        group: str = constants.EVENT_REDIS_GROUP,
        consumer: str = constants.EVENT_REDIS_CONSUMER,
        body_field: str = constants.EVENT_REDIS_BODY_FIELD,
        count: int = constants.EVENT_REDIS_COUNT,
        block_ms: int = constants.EVENT_REDIS_BLOCK_MS,
        max_empty_polls: Optional[int] = constants.EVENT_REDIS_MAX_EMPTY_POLLS,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.url = url
        self.host = host
        self.port = port
        self.db = db
        self.stream = stream
        self.group = group
        self.consumer = consumer
        self.body_field = body_field
        self.count = max(count, 1)
        self.block_ms = block_ms
        self.max_empty_polls = max_empty_polls
        self._client_factory = client_factory
        self._client: Optional[Any] = None
        self._ids: list[str] = []

    def _create_client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError(
                "EVENT_SOURCE_KIND=redis requires the redis extra: "
                "pip install 'fl-op[redis]'"
            ) from exc
        if self.url:
            return redis.Redis.from_url(self.url, decode_responses=True)
        return redis.Redis(
            host=self.host, port=self.port, db=self.db, decode_responses=True
        )

    def _ensure_group(self, client: Any) -> None:
        # Create the consumer group at the stream head, tolerating a re-create.
        # Caught broadly (not redis.ResponseError) so the lazy redis import stays
        # confined to _create_client and injected fakes need no exception types.
        try:
            client.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:  # noqa: BLE001 - only BUSYGROUP is benign
            if "BUSYGROUP" not in str(exc):
                raise

    def __iter__(self) -> Iterator[ExecutionEvent]:
        client = self._client = self._create_client()
        self._ensure_group(client)
        self._ids = []
        # Phase 1: re-deliver this consumer's unacked backlog (crash recovery).
        yield from self._read_pending(client)
        # Phase 2: new entries, bounded by consecutive empty reads.
        yield from self._read_new(client)

    def _read_pending(self, client: Any) -> Iterator[ExecutionEvent]:
        cursor = _PENDING_START
        while True:
            batch = self._xreadgroup(client, cursor, block=None)
            if not batch:
                return
            for entry_id, event in batch:
                cursor = entry_id
                if event is not None:
                    yield event

    def _read_new(self, client: Any) -> Iterator[ExecutionEvent]:
        empty_polls = 0
        while self.max_empty_polls is None or empty_polls < self.max_empty_polls:
            batch = self._xreadgroup(client, _NEW_MESSAGES, block=self.block_ms)
            if not batch:
                empty_polls += 1
                continue
            empty_polls = 0
            for _entry_id, event in batch:
                if event is not None:
                    yield event

    def _xreadgroup(
        self,
        client: Any,
        last_id: str,
        block: Optional[int],
    ) -> list[tuple[str, Optional[ExecutionEvent]]]:
        """One XREADGROUP, returning (entry_id, parsed-or-None) pairs.

        Malformed bodies parse to None but their ids are still collected, so a
        poison entry is acknowledged on commit instead of redelivering forever
        (the Kafka client advances its offset past bad messages the same way).
        """
        response = client.xreadgroup(
            self.group,
            self.consumer,
            {self.stream: last_id},
            count=self.count,
            block=block,
        )
        results: list[tuple[str, Optional[ExecutionEvent]]] = []
        for _stream_name, entries in response or []:
            for entry_id, fields in entries:
                self._ids.append(entry_id)
                results.append((entry_id, self._parse(fields)))
        return results

    def _parse(self, fields: dict) -> Optional[ExecutionEvent]:
        body = fields.get(self.body_field)
        try:
            return parse_event(json.loads(body))
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Skipping malformed Redis event: %s", exc)
            return None

    def commit(self) -> None:
        """Acknowledge the read entries; call after revisions are published."""
        if self._client is None or not self._ids:
            self._ids = []
            return
        self._client.xack(self.stream, self.group, *self._ids)
        logger.info("Acked %d Redis stream entries after publication", len(self._ids))
        self._ids = []
        self._client = None

    def close(self) -> None:
        """Abandon read entries without acking; they stay pending for next run."""
        self._ids = []
        self._client = None


def _redis_event_source(events_path: Optional[str]) -> Iterable[ExecutionEvent]:
    if events_path is not None:
        logger.warning("EVENT_SOURCE_KIND=redis: ignoring events file %s", events_path)
    return RedisStreamEventSource()


register_event_source(EVENT_SOURCE_REDIS, _redis_event_source, uses_dedup_store=True)
