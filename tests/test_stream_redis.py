"""Redis Streams execution-event source.

Driven against an in-memory ``fakeredis`` server through the real redis-py API
(``xadd``/``xgroup_create``/``xreadgroup``/``xack``), so the consumer-group
reads, crash recovery, validation, and ack-on-commit are exercised against a
faithful Redis, not a hand-rolled stub. Set ``FL_OP_TEST_REDIS_URL`` (for
example ``redis://localhost:6379/0`` from the bundled docker-compose service) to
run the same suite against a real Redis endpoint instead.
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from fl_op.core import constants
from fl_op.stream.broker import (
    open_dedup_store,
    open_event_source,
    registered_event_sources,
)
from fl_op.stream.redis_stream import (
    EVENT_SOURCE_REDIS,
    RedisStreamEventSource,
    _entry_id_epoch_ms,
)


def _make_client():
    """An in-memory fakeredis, or a real endpoint when FL_OP_TEST_REDIS_URL set."""
    url = os.environ.get("FL_OP_TEST_REDIS_URL")
    if url:
        import redis

        return redis.Redis.from_url(url, decode_responses=True)
    import fakeredis

    return fakeredis.FakeRedis(decode_responses=True)


def _body(event_id: str, event_type: str = "task.started") -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type,
            "observed_at": "2026-06-11T08:00:00Z",
            "entity_ref": "task-1",
            "payload_json": "{}",
        }
    )


@pytest.fixture
def redis_env():
    """A client plus a stream/group unique per test (isolates real endpoints)."""
    client = _make_client()
    env = SimpleNamespace(
        client=client,
        stream=f"test.stream.{uuid.uuid4().hex}",
        group=f"test-group-{uuid.uuid4().hex[:8]}",
    )
    yield env
    # Best-effort cleanup so a shared real endpoint does not accumulate streams.
    try:
        client.delete(env.stream)
    except Exception:  # noqa: BLE001 - cleanup is best effort
        pass


def _add(env, *bodies: str) -> None:
    for body in bodies:
        env.client.xadd(env.stream, {"data": body})


def _pending(env) -> int:
    return env.client.xpending(env.stream, env.group)["pending"]


def _ensure_group(env) -> None:
    try:
        env.client.xgroup_create(env.stream, env.group, id="0", mkstream=True)
    except Exception as exc:  # noqa: BLE001
        if "BUSYGROUP" not in str(exc):
            raise


def _source(env, **kwargs) -> RedisStreamEventSource:
    return RedisStreamEventSource(
        stream=env.stream,
        group=env.group,
        consumer=kwargs.pop("consumer", "c-1"),
        max_empty_polls=kwargs.pop("max_empty_polls", 1),
        client_factory=lambda: env.client,
        **kwargs,
    )


def test_yields_new_events_and_stops_when_drained(redis_env) -> None:
    _add(redis_env, _body("e-1"), _body("e-2"))
    events = list(_source(redis_env))
    assert [e.event_id for e in events] == ["e-1", "e-2"]
    # Nothing acked until commit, so a crash redelivers the backlog.
    assert _pending(redis_env) == 2


def test_pending_backlog_is_redelivered_before_new(redis_env) -> None:
    # A prior run delivered e-1/e-2 to this consumer but crashed before acking.
    _ensure_group(redis_env)
    _add(redis_env, _body("e-1"), _body("e-2"))
    redis_env.client.xreadgroup(
        redis_env.group, "c-1", {redis_env.stream: ">"}, count=10
    )
    _add(redis_env, _body("e-3"))  # e-3 is new

    events = list(_source(redis_env, consumer="c-1"))
    assert [e.event_id for e in events] == ["e-1", "e-2", "e-3"]


def test_malformed_bodies_are_skipped_but_still_acked(redis_env) -> None:
    _add(redis_env, _body("e-good"), "not-json")
    source = _source(redis_env)
    events = list(source)
    assert [e.event_id for e in events] == ["e-good"]
    source.commit()
    # Both entries are acked: a poison body advances past instead of looping.
    assert _pending(redis_env) == 0


def test_commit_acks_and_close_does_not(redis_env) -> None:
    _add(redis_env, _body("e-1"))
    source = _source(redis_env)
    list(source)
    source.close()
    assert _pending(redis_env) == 1  # close leaves the entry pending

    source2 = _source(redis_env)
    list(source2)
    source2.commit()
    assert _pending(redis_env) == 0
    source2.commit()  # idempotent no-op
    assert _pending(redis_env) == 0


def test_ensure_group_tolerates_busygroup(redis_env) -> None:
    # Pre-create the group; the source's own xgroup_create then hits BUSYGROUP.
    _ensure_group(redis_env)
    _add(redis_env, _body("e-1"))
    assert [e.event_id for e in _source(redis_env)] == ["e-1"]


def test_ensure_group_reraises_non_busygroup_errors() -> None:
    class _Raising:
        def xgroup_create(self, *a, **k):
            raise RuntimeError("ERR connection refused")

    source = RedisStreamEventSource(client_factory=lambda: _Raising())
    with pytest.raises(RuntimeError, match="connection refused"):
        list(source)


def test_missing_redis_raises_actionable_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)
    source = RedisStreamEventSource(stream="s", max_empty_polls=1)
    with pytest.raises(RuntimeError, match=r"fl-op\[redis\]"):
        list(source)


def test_redis_registered_and_opts_into_dedup(monkeypatch, tmp_path) -> None:
    from fl_op.stream import dedup as dedup_module

    assert EVENT_SOURCE_REDIS in registered_event_sources()
    monkeypatch.setattr(dedup_module, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_REDIS)
    assert open_dedup_store() is not None


def test_open_event_source_redis_returns_redis_source(monkeypatch) -> None:
    monkeypatch.setattr(constants, "EVENT_SOURCE_KIND", EVENT_SOURCE_REDIS)
    assert isinstance(open_event_source(None), RedisStreamEventSource)


def test_entry_id_epoch_ms_parses_broker_time_and_tolerates_garbage() -> None:
    # Redis ids are "<millisecondsTime>-<sequenceNumber>".
    assert _entry_id_epoch_ms("1700000000000-0") == 1700000000000.0
    assert _entry_id_epoch_ms("1700000000000-5") == 1700000000000.0
    assert _entry_id_epoch_ms("not-an-id") is None
    assert _entry_id_epoch_ms(None) is None


def test_redis_entry_id_stamps_ingested_at(redis_env) -> None:
    _add(redis_env, _body("e-1"))
    [event] = list(_source(redis_env))
    # The entry id Redis just assigned gives a true arrival time, distinct from
    # the producer-omitted ingested_at; it is recent (the broker added it now).
    assert event.ingested_at
    arrival = datetime.fromisoformat(event.ingested_at)
    assert abs((datetime.now(timezone.utc) - arrival).total_seconds()) < 300


def test_redis_keeps_producer_ingested_at(redis_env) -> None:
    body = json.dumps(
        {
            "event_id": "e-1",
            "event_type": "task.started",
            "observed_at": "2026-06-11T08:00:00Z",
            "entity_ref": "task-1",
            "payload_json": "{}",
            "ingested_at": "2026-06-11T08:00:30+00:00",
        }
    )
    _add(redis_env, body)
    [event] = list(_source(redis_env))
    assert event.ingested_at == "2026-06-11T08:00:30+00:00"
