[Implementation guide](../current-implementation.md) > Rolling dispatch

# Rolling dispatch

Event application is binding-driven (`stream/apply.py`): the target source
collection and its key column are resolved from the selected domain mapping
documents (canonical entity + identity binding), so the driver knows no
domain-specific column names. Supported triggers:

- `task.started` / `task.progress` / `task.completed`: lifecycle and partial
  completion; progress carries either per-pass coverage geometry (see below), a
  `completed_fraction` (scales every work-quantity column down to the remaining
  share), or an absolute `remaining_quantity` in the task's work unit (exact
  overwrite of the generic work-quantity column, for domains without a
  meaningful fraction); a fully completed task leaves planning, so re-solves
  dispatch only the remaining effort;
- `order.created` / `order.cancelled`;
- `asset.unavailable`: removes any asset by id -- vehicles, implements,
  operators, and stationary equipment share one path;
- `inventory.adjusted`: partial merge into a location row (depot fuel, energy,
  and material balances) without touching its other fields;
- `forecast.updated`: with a payload, upserts the forecast window (weather
  invalidation by data); without one, a pure replan trigger;
- `observation.recorded`: streamed sensor readings upserted by reading id, so
  a re-sent corrected reading replaces the earlier one; readings normalized
  to the canonical `work-progress` metric drive task progress directly from
  telemetry (carrying coverage geometry or a percent value) and complete the
  task at 100 percent;
- `entity.corrected`: a corrected source row upserted by its key column, so
  quality-rejected or wrongly-valued entities re-enter planning.

Per-pass coverage geometry (`stream/coverage.py`, `core/geometry.py`) makes
progress spatially explicit. A `task.progress` event or `work-progress`
telemetry observation may carry the geometry covered in that pass instead of a
scalar: either an explicit `covered_polygon` ([lat, lon] vertices) or a
`covered_path` ([lat, lon] points) swept by a `swath_width_m` implement width
(buffered in a longitude space scaled by `cos(latitude)` so the swath is
metrically round). Passes accumulate per task and union geometrically, so two
passes over the same strip are not double-counted; the overlap-corrected covered
geodesic area over the task's original work area gives the completed share, which
shrinks every work column from its original value (cumulative, never re-shrinking
an already-reduced value). Reaching `COVERAGE_COMPLETE_FRACTION` (default 0.99)
finishes the task. Each pass appends one record (covered/remaining area, covered
fraction, pass count) to `$DATA_DIR/quality/coverage-passes.jsonl`, and
`coverage_stats` aggregates the rolling spatial-progress summary logged after
stream runs.

Event application is idempotent by `event-id`: at-least-once delivery may
replay an event, and a replay mutates nothing and produces no revision.
Broker-backed runs extend this across process restarts with a durable
event-id store (`stream/dedup.py`, an append-only id log under
`$DATA_DIR/stream`, compacted in place): each published revision's applied
event ids are recorded after publication and ids published by earlier runs
are suppressed on redelivery. The JSONL development source replays event
files intentionally and never uses the store.
Events whose observed times fall within the convergence window
(`STREAM_CONVERGENCE_WINDOW_S`, default off) coalesce into one rebuild and one
revision, so a partition flushing its backlog converges before replanning.

Reuses the solver chain on a filtered canonical payload:

- Started tasks and tasks inside the freeze window are frozen.
- Assignments whose task and assets still exist are carried forward unchanged.
- Tasks affected by new or unavailable assets are re-solved. Every asset held
  by a frozen/carried assignment stays available to the re-solve as a
  resource calendar of busy intervals, all blocked in-model: prime movers and
  implements get exact gap reuse (the routing model blocks the union of the
  pair's intervals as vehicle breaks, so either is reused only in a real
  non-overlapping gap), and a held operator's busy windows block that operator's
  own re-solved tasks the same way (each task interval must avoid the windows),
  so a held operator is reused only in a genuine gap rather than by hold-aware
  allocation scoring alone. Within a cluster, operators are also time-modelled
  inside routing: an operator shared by tasks on different routing vehicles gets
  vehicle-aware no-overlap constraints so the shared operator's parallel tasks
  serialize. (Hold-aware allocation scoring still biases which operator/vehicle a
  task draws; the in-model breaks make the held calendar a hard constraint.) Held
  assets are classified by solver-row section membership, not id prefixes, so the
  mechanism is domain-neutral.
- Each event yields an immutable plan revision with churn and plan-instability
  metrics.
- `fl-op plan diff-revisions` compares consecutive revisions of a rolling run
  and explains why every changed assignment moved (corrective action, trigger,
  freeze, feasibility change, or optimization tradeoff). For plain re-solves it
  prefers the per-task solver attribution carried in plan scores: cluster id,
  routing status/objective, first-solution objective, LNS delta, time-limit
  state, change penalty, and same-cluster conflicts. Reports are written as
  `revision_diff.json`/`.txt` under `.data/revision-diff/<ts>/`.

## Corrective rescheduling

Plans survive being wrong (`adapters/rolling/corrective.py`); every self-repair
is recorded as a `CorrectiveAction` on the revision and counted in its score:

- **Asset loss mid-plan**: a frozen (started) or carried assignment whose asset
  disappeared is released and its task re-solved
  (`reassigned-after-asset-loss`), instead of staying bound to a dead bundle.
- **False positive prognosis**: a derived service task no longer justified by
  newer readings is withdrawn (`service-withdrawn`), recording why it was
  derived (previous revision's monitoring reasons) and the contradicting
  current readings.
- **False negative prognosis**: critical battery or failed health derives an
  escalated service task (top priority, one-day deadline); a previously
  non-escalated assignment is forced out of carry-forward and re-solved
  (`service-escalated`).
- **Prognosis accuracy feedback** (`stream/prognosis.py`): every revision
  appends its service-task outcomes to
  `$DATA_DIR/quality/service-prognosis.jsonl`, with a per-asset-type breakdown
  (`by_asset_type`) so accuracy can be split by station class. Accumulated
  false-positive / false-negative rates above thresholds log monitoring-policy
  tuning recommendations, globally and per asset type. With
  `MONITORING_AUTO_TUNE_ENABLED=1` the loop closes: `snapshot/policy_tuning.py`
  adjusts `batteryForecastHorizonDays`, `compositeHealthThreshold`, and
  `batteryLowThresholdPct` in bounded steps (max relative step, absolute
  clamps). The global rates plus the service-completion lead-time distribution
  (a high share of service tasks finishing after their deadline loosens the
  policy, the same direction as escalations) drive the global step; per-type
  accuracy splits additionally tune each station class into the overlay's
  `assetTypeOverrides`. All steps are written to a tuned-policy overlay under
  `$DATA_DIR/quality` with a JSONL audit trail (one record per scope); the
  reviewed profile document is never modified and deleting the overlay reverts
  to it. Conflicting signals (a tighten and a loosen at once) skip that scope's
  adjustment but still audit.
- **Completion lead-time feedback** (`stream/lead_time.py`): `task.completed`
  events, fully complete `task.progress` events, and complete
  `work-progress` telemetry append one record per finished task to
  `$DATA_DIR/quality/completion-lead-times.jsonl`. Each record measures
  deadline lead and schedule error against the plan the task was executing
  under; distribution stats (including the service-task late share consumed by
  guarded monitoring tuning) are logged after stream runs.

Periodic plans get the same withdrawal/escalation record-keeping: each
periodic run reconciles against its predecessor
(`reconcile_previous_plan`), records the corrective actions on the plan,
persists a `service_reasons.json` artifact for the next run, and appends to
the same prognosis accuracy log.

## Watermark-driven replan triggering

Every published plan carries its
snapshot's `source_watermarks`. `fl-op plan freshness --data <dir> --plan
<dir|latest>` builds a snapshot from the data visible now and compares
(`stream/freshness.py`); with `--replan` a stale plan automatically triggers
a rolling replan. Each check writes a `freshness.json` artifact under
`$DATA_DIR/freshness/<ts>/`.
</content>
