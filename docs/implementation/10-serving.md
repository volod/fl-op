[Implementation guide](../current-implementation.md) > Serving

# Serving

- `fl-op serve` (`serving/api.py`, FastAPI + uvicorn, loopback by default)
  exposes published plan retrieval (`/plans/{periodic|rolling}` listing,
  per-run and `latest` plan documents, rolling revision summaries and
  per-revision plans) and `POST /feasibility`, the query-contract evaluation
  for a new order; the evaluation core (`solver/query_pipeline.py:
  evaluate_query`) is shared with the CLI pipeline. `/health` is public; plan
  and feasibility routes are guarded by the security gateway (below). The API
  reads artifacts through `serving/artifacts.py`: by default this is
  `$DATA_DIR`, or `SERVE_ARTIFACT_ROOT` for a shared mounted artifact tree, or
  an object store (below). It never mutates datasets or plans. Exact feasibility
  responses are cached under `$DATA_DIR/cache/feasibility`, keyed by the
  *canonical content* of the source rows the query reads, the parsed
  `schedule.json`, and the order payload -- so inputs that differ only in JSON
  key/byte ordering, CSV column order, or format reuse a cached response. Per-file
  content digests are memoized by `(mtime, size)`, so a repeated request over an
  unchanged dataset skips re-parsing the sources before the lookup; uncached
  requests also reuse the compat and candidate-filter caches.
- Serving security (`serving/security/`) hardens the previous single-token
  check into a composable gateway run on every protected route as
  authenticate -> authorize -> rate-limit -> audit, with each refusal audited
  before its HTTP status. `SERVE_AUTH_MODE` selects the authenticator (auto when
  unset: `oidc` if an issuer is configured, `static` if tokens are, else
  `none`/open for loopback dev):
  - `static` accepts a set of bearer tokens (`SERVE_AUTH_TOKENS`, comma list;
    `SERVE_AUTH_TOKEN` folded in). Holding several at once makes token rotation
    zero-downtime: add the new token, roll clients over, then drop the old one.
  - `oidc` validates RFC 7519 JWTs (`OidcJwtAuthenticator`, PyJWT via the
    `[auth]` extra, lazily imported with an actionable error): signature
    (RS256 via the issuer JWKS at `SERVE_OIDC_JWKS_URL`, or HS256 via
    `SERVE_OIDC_HS256_SECRET`), issuer, audience, and expiry, with scopes read
    from the `scope`/`scp`/`roles` claims.
  Authorization is per-route by scope: plan routes require `plans:read`,
  feasibility requires `feasibility:evaluate`, so a read-only client cannot
  drive solves; a known principal lacking the scope gets 403, an unauthenticated
  one 401. An opt-in in-process fixed-window rate limiter
  (`SERVE_RATE_LIMIT_REQUESTS`/`_WINDOW_S`, 0 = off) throttles per principal (or
  per client host when anonymous) and returns 429 with `Retry-After`; durable
  cross-instance limits still belong at an ingress/proxy. Every protected
  request emits one structured audit record (principal, route, decision, status)
  to the `fl_op.serving.audit` logger, and to JSONL under
  `$DATA_DIR/serving/` when `SERVE_AUDIT_LOG_FILENAME` is set; only a short
  non-reversible token fingerprint is logged, never the token. A non-loopback
  bind is rejected unless an authenticator (static or OIDC) is configured.
- Object-store artifact backend (`serving/objectstore.py`,
  `SERVE_ARTIFACT_BACKEND=object-store`). Object stores have no atomic directory
  rename, so publication is made explicit: a run is visible only once a commit
  marker object (`_COMMITTED`) appears under its prefix, and `publish_run`
  writes that marker *last*. `ObjectStoreArtifactStore.list_run_ids` enumerates
  only commit-marked runs, so a reader never observes a run another writer is
  still publishing - cross-writer read-after-write consistency without locks.
  Reads go through an injectable `ObjectStoreClient`; the built-in
  `LocalObjectStoreClient` is a filesystem-backed reference (no dependency, and
  no vendor SDK bundled), and the protocol is the seam where a future networked
  backend plugs in as its own client. The feasibility path calls `local_path`,
  which materializes a committed run's objects into `$DATA_DIR/cache/objstore-
  materialized/` once (committed runs are immutable) and returns the local
  directory the query pipeline reads.
- Rolling planning ingests execution events from the source selected by
  EVENT_SOURCE_KIND (`stream/broker.py:open_event_source`): JSONL, Kafka, and
  Redis Streams (`stream/redis_stream.py`, the `redis` package) are registered
  built-ins, and integrations register additional source factories with
  `register_event_source`. Each adapter is a small package that self-registers
  its factory on import and opts into the durable dedup store when its source
  can redeliver (Kafka and Redis do; JSONL replays files intentionally and does
  not). Kafka and Redis validate messages through the same `parse_event` and
  drain the visible backlog before the run publishes revisions. When a producer
  leaves `ingested_at` blank, the live adapter stamps it from the broker's own
  arrival time -- Kafka's record timestamp and the Redis stream entry id's
  `<millisecondsTime>` (`stream/source.py:stamp_broker_ingested`) -- so a
  broker-fed observation series orders by a true platform-ingestion time rather
  than the observed-time proxy; a producer-supplied `ingested_at` always wins,
  and an unavailable broker timestamp leaves the proxy in place. The broker
  assigns the timestamp once, so a redelivered or restart-recovered entry keeps
  the same arrival time. Acknowledgement
  is never automatic: Kafka offsets stay uncommitted and Redis stream entries
  stay unacked (`XACK`) until the run's revisions are written, right after the
  dedup store records the published event ids. A crash before publication
  replays the backlog; a crash between record and commit redelivers events the
  store suppresses - effectively exactly-once from source to published revision.
  The Redis source reads its consumer group's pending (delivered-but-unacked)
  entries before new ones, so a crashed run's in-flight backlog is recovered on
  restart; it acks every read entry including malformed bodies, so a poison
  entry advances past instead of redelivering forever, matching how Kafka
  advances its offset.
- The serving-side watcher (`fl-op plan watch --data <dir>`,
  `planning/plans.py:run_plan_watch`) keeps a single `StreamSession` alive and
  drains bounded event cycles forever instead of draining once and exiting like
  `plan rolling`. The session's `start()` publishes the baseline revision, then
  each cycle opens a fresh bounded event source, applies its backlog, and
  extends the same continuity chain. Offset commits are bounded per cycle:
  after a cycle's revisions are written and its event ids recorded in the
  dedup store, the cycle records-then-commits its source offsets
  (`event_source.commit()`, which commits and closes) so a crash redelivers
  just the in-flight cycle rather than the whole session. An empty cycle idles
  `PLAN_WATCH_POLL_INTERVAL_S` before re-polling so a quiet topic does not
  spin; `--max-cycles` (`PLAN_WATCH_MAX_CYCLES`) bounds the loop for tests and
  graceful shutdown, and `0`/`None` runs unbounded. Revisions
  land under `plan-watch/<timestamp>/` with a rolling `revisions_summary.json`,
  and each cycle that produces revisions logs one MLflow tracking run tagged
  with its `watch_cycle`. The watcher pairs with `plan freshness`
  (see "Watermark-driven replan triggering") for a poll-and-replan loop.
</content>
