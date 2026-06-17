[Implementation guide](../current-implementation.md) > Serving

# Serving

- `fl-op serve` (`serving/api.py`, FastAPI + uvicorn, loopback by default)
  exposes published plan retrieval (`/plans/{periodic|rolling}` listing,
  per-run and `latest` plan documents, rolling revision summaries and
  per-revision plans) and `POST /feasibility`, the query-contract evaluation
  for a new order; the evaluation core (`solver/query_pipeline.py:
  evaluate_query`) is shared with the CLI pipeline. `/health` is public; all
  plan and feasibility routes require `Authorization: Bearer <token>` when
  `SERVE_AUTH_TOKEN` is set, and a non-loopback bind is rejected unless that
  token is configured. The API reads artifacts through
  `serving/artifacts.py`: by default this is `$DATA_DIR`, or
  `SERVE_ARTIFACT_ROOT` for a shared mounted artifact tree. It never mutates
  datasets or plans. Exact feasibility responses are cached under
  `$DATA_DIR/cache/feasibility`, keyed by the source bytes the query reads,
  schedule.json, and the order payload; uncached requests also reuse the
  compat and candidate-filter caches.
- Rolling planning ingests execution events from the source selected by
  EVENT_SOURCE_KIND (`stream/broker.py:open_event_source`): JSONL and Kafka
  are registered built-ins, and integrations can register additional source
  factories with `register_event_source`. Kafka validates messages through the
  same `parse_event` and drains the visible backlog before the run publishes
  revisions. Broker offsets are never auto-committed: the consumer stays open
  after the drain and commits only once the run's revisions are written,
  right after the durable dedup store records the published event ids. Any
  registered source kind can opt into that dedup store. A crash before
  publication replays the backlog; a crash between record and commit
  redelivers events the store suppresses - effectively exactly-once from
  broker to published revision.
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
