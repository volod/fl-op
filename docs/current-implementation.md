# Current Implementation

How the system works today. For the contract layer see
[canonical-model.md](reference/canonical-model.md) and
[domain-mapping.md](reference/domain-mapping.md); for the entity ontology, use
cases, and algorithm overview see
[optimization-ontology.md](reference/optimization-ontology.md); for why and how
the system survives the gap between its entity model and the physical world see
[model-world-divergence.md](reference/model-world-divergence.md).

## Three layers

1. **Canonical optimization model** (`contracts/canonical/`) - the domain-neutral
   entity / capability / semantic-term contract the engine consumes.
2. **Domain mapping packs** (`contracts/domains/<domain>/`) - a pure physical ODCS
   schema, separate `*.mapping.yaml` projections onto the canonical model, and an
   optimization profile. Physical schemas may carry extra real-data fields beyond
   what the optimizer needs; those are retained for analysis and ignored by the
   engine.
3. **Engine** (`src/fl_op/{snapshot,solver,adapters}`) - consumes canonical
   entities only; no dependency on any domain model layer.

Three domain packs exist today: agricultural custom services and construction
earthworks are runnable end to end (registered contracts, data generators,
profiles), roadside infrastructure is a validation-level example pack
(stationary signage along road segments, inspection rounds as observations).
The construction pack is earthworks-native: volume-shaped jobs (excavation,
trenching, hauling) carry m3 quantities and volume-moving attachments declare
m3-per-hour work rates, so durations come from the rate, not an area proxy.
One domain is active per run: registry.yaml `activeDomain`, overridable with
`ACTIVE_DOMAIN=construction`. Solver inputs resolve their binding tables by
canonical entity and asset role, never by contract id, so switching domains
needs no engine change (`fl-op generate-data --domain construction`, then
`ACTIVE_DOMAIN=construction fl-op plan periodic --data latest`).

## Data and contracts

`fl-op generate-data` writes one timestamped dataset under
`$DATA_DIR/generate-data/<timestamp>/` (Avro by default; CSV/Parquet via
`--format`). `metadata.json` records the chosen format so downstream commands use
the right codec.

Physical schemas (Avro/Protobuf/Elasticsearch/Parquet) are generated from the
physical ODCS contracts into `contracts/generated/` (gitignored). Generated
schemas are structural only - they carry no optimization metadata. The
canonical plan OUTPUT contract generates physical schemas too
(`contracts/plan_schema_gen.py`, Avro and Parquet): nested records named
after the plan.json payload fields, joined from the same binding table the
publication validator uses, so downstream consumers can validate received
plan artifacts without this codebase.

`fl-op contracts validate` checks: generated-schema structural fingerprints, the
canonical model, and per-domain **mapping completeness** (every mapping binds only
to declared canonical fields + known terms, and covers every required canonical
binding). `fl-op contracts validate-domain --domain <d>` additionally reports each
contract's optimization-mapped vs extra (analytical) physical fields.

## Planning pipeline

1. Validate contracts (`fl-op contracts validate`).
2. Map source rows into canonical assets, locations, tasks, forecasts,
   observations, commitments, travel links, cost rates, and operational
   bundles. Which datasets are mapped is derived from the registry (active
   domain + mapping entity), and entity dispatch is a registered emitter table
   (`mapping/builders.py:ENTITY_EMITTERS`), so new datasets and entities plug
   in without engine changes.
3. Statistically assess observation series (`snapshot/assessment.py`):
   order each series by observed time (never arrival order), flag
   arrival-order timestamp regressions (arrival order is the explicit
   `ingested-at` timestamps when the whole series carries them -- exact
   across restarts -- with source row order as the legacy fallback),
   exclude readings claiming times beyond
   the clock-skew tolerance ahead of planning time, bound the series by the
   retention window and aggregate over-long histories into time windows
   (endpoints preserved; each window representative carries min/mean/max and
   reading-count aggregates so spikes survive downsampling), exclude
   readings flagged bad by their source and
   outliers (MAD-based modified z-score), floor the confidence of
   fault-suspected series (battery rising without service, frozen non-zero
   values), detect metric drift on non-trending metrics, and aggregate
   per-source error rates into the quality summary. Source quality flags fold
   into per-reading confidence. Per-source watermarks (the newest trusted
   observed time per contract) are stamped onto the snapshot
   (`source_watermarks`). Degraded sources are reported per build and trended
   across runs (`snapshot/quality_trend.py`).
4. Apply the stationary-equipment monitoring policy
   (`snapshot/monitoring.py`): stationary assets (sensor stations, fixed
   road/field equipment) with low battery, a battery drain trend projected
   below threshold within the forecast horizon, degraded health, an overdue
   service interval, a drifting metric (calibration), or a low composite
   health score (weighted battery/health/service-due/drift signals; the
   weights and headrooms are profile-tunable next to the thresholds) yield
   canonical service tasks anchored at their home location. Readings below
   the policy's minimum confidence are ignored. Thresholds and task
   attributes come from the profile's `monitoring` section, with
   constant-backed defaults, per-asset-type overrides
   (`assetTypeOverrides`), and instance-level overrides by asset id
   (`assetOverrides`, a single critical station) layered on top; the
   guarded auto-tuning overlay (see corrective rescheduling) layers above
   the reviewed profile.
   Observation metric codes are normalized from raw
   source vocabularies via the mapping document's `metricCodes` table.
5. Build an immutable, reproducibly-hashed `PlanningSnapshot` (purely canonical).
6. An adapter projects the snapshot into canonical solver rows
   (`solver/inputs.py`) and runs the OR-Tools solver chain; derived service
   tasks are dispatched alongside ordered work.
7. Validate every published plan against the canonical plan output contract
   (`contracts/canonical/odcs/plan.odcs.yaml`, enforced by
   `contracts/plan_contract.py`): a plan whose required bindings do not
   resolve fails publication instead of writing a non-conforming artifact.
8. Synthesize execution events and run rolling-dispatch revisions.

## Solver chain

Shared by batch `solve` and the canonical adapters; it operates on canonical
solver rows (keyed by `asset_id`, `rated_power`, `task_id`, ...):

1. Enforce the profile's weather-window constraint (`solver/enforcement.py`):
   a weather-sensitive task with no compliant forecast window at its nearest
   forecast location is excluded with `NO_VALID_WEATHER_WINDOW`. Sensitivity
   per operation type and limits come from the profile's `weatherPolicy`.
   For the kept sensitive tasks the filter also returns their non-compliant
   forecast windows as blocked intervals, which the routing model keeps
   execution out of (step 8), so weather-sensitive work is scheduled *into*
   its compliant windows, not merely admitted because one exists.
   Structural data semantics are filtered alongside: tasks none of whose
   workable windows can still be met (`CONTRACT_WINDOW_INFEASIBLE`,
   `solver/task_relations.py`), tasks blocked by their location's declared
   restrictions -- prohibited operation types or restriction windows covering
   every admissible start (`RESTRICTED_ZONE`, `solver/restrictions.py`) --
   and, transitively, dependents of any excluded predecessor
   (`PREDECESSOR_UNSERVED`). Fuel and material prices are resolved from the
   snapshot's cost-rate entities (`solver/cost_rates.py`), falling back to
   the engine cost constants for unpriced resources.
2. Build a prime-mover / related-equipment compatibility matrix from power
   capabilities (`solver/feasibility.py`). Matrices are cached by dataset
   hash (a content hash of the power capabilities and margin), so a repeated
   solve over the same fleet skips the rebuild.
3. Filter candidates per task by operation type. The deterministic
   operation-filtered candidate table is cached under
   `$DATA_DIR/cache/preprocessing/candidate-filter`, keyed by the canonical
   task/fleet rows and the compatibility-matrix digest.
4. Cluster tasks by nearest depot; split large groups. Cluster specs are
   cached under `$DATA_DIR/cache/preprocessing/cluster-specs`, keyed by the
   canonical task/site/depot rows, target cluster size, and travel lookup.
   Depot affinity uses
   network travel times where the travel-link graph connects the pair
   (haversine otherwise), so a field whose road access favors a farther
   depot clusters with that depot. Clustering is
   chain-aware: tasks linked by `depends-on` precedence stay in one cluster
   so their ordering can be enforced in-model.
5. Pre-allocate prime movers, related equipment, and operators with a small
   CP-SAT global assignment model (`solver/allocation/global_model.py`): all
   clusters are decided at once, maximizing allocated bundles first and
   breaking ties by the shared greedy score; operators maximize certified
   coverage of cluster operation types with a depot-match tiebreak. The
   count-vs-margin tradeoff is profile-tunable
   (`allocationPolicy.countPriority` through
   `SolverParameters.assignment_count_priority`: 1.0 keeps count-first, 0.0
   maximizes summed scores so a contested resource goes to the
   highest-margin cluster). Allocation is hold-aware: each held asset's free
   share of the capacity horizon discounts candidate scores and operator
   rewards, so mostly-held resources are reserved only when nothing freer
   qualifies. The penalty-ordered greedy reservation loop remains the
   fallback when the model is disabled (`GLOBAL_ASSIGNMENT_ENABLED=0`),
   oversized, or finds no solution in time.
6. Enforce operator qualification: a task whose operation the cluster
   operator is not certified for is paired with a free qualified backup
   operator (recorded in the cluster's `task_operators` map and carried into
   its dispatch packages); only tasks no qualified operator can take are
   dropped (`NO_AVAILABLE_OPERATOR`). Enforce material availability
   (cumulative per-operation demand from the profile's `materialDemand`
   charged against depot inventory, highest penalty first ->
   `INSUFFICIENT_MATERIAL`). Material charging and reservations are one
   mechanism: every admitted charge becomes a provisional reservation
   record, settled against the final dispatch (confirmed with the scheduled
   window, released when the solve left the task unserved) and published as
   canonical `MaterialReservation` rows on the plan; assignments reference
   their reservation ids. Rolling revisions re-publish the reservations of
   frozen/carried tasks so each revision is self-contained.
7. Build a greedy margin-based warm start. Repositioning hours use the
   network shortest path from the vehicle's home depot to the field where
   one exists; the straight-line estimate from the vehicle's current
   position remains the fallback.
8. Solve each cluster as an OR-Tools routing problem in a spawned process
   pool. Auto pool sizing is memory-aware: the worker count is bounded by
   CPUs and by how many estimated worker footprints (base footprint plus the
   largest cluster's routing-model size) fit into available memory; an
   explicit `SOLVER_WORKERS` wins. Completed worker telemetry records
   `worker_max_rss_mb`; `$DATA_DIR/cache/solver-feedback/worker-memory.json`
   retains the max observed RSS as a deployment-specific floor on future
   auto-sizing estimates. Arc travel times come from the travel
   network: the lookup is the all-pairs shortest-path closure over the
   directed travel-link graph (Dijkstra per source, skipped past
   `TRAVEL_NETWORK_MAX_COMPOSE_NODES`), with a reverse-direction and
   haversine fallback for pairs without any network path
   (`solver/travel_time.py`). Arcs are priced per vehicle as travel fuel
   cost (burn rate x the resolved fuel price) in the same objective currency
   as the drop penalties (1 EUR = 600 penalty seconds), so a fuel-efficient
   machine wins time-equal legs and dropping an order is weighed against the
   money cost of serving it. Task
   starts are constrained into their admissible intervals: workable windows
   minus one shared blocked-interval set (location restriction windows plus
   the task's non-compliant weather windows). Blocked intervals carry
   occupancy semantics: reified constraints require the execution to finish
   by the block start or begin after its end, with the serving vehicle's
   service duration resolved in-model, so a task cannot run into a
   restriction or storm window it started before. `depends-on` precedence is
   enforced in-model (a dependent cannot start before its predecessor
   finishes). Service durations are quantity-driven: the generic work
   quantity plus its unit feed the duration estimate (area is the legacy
   alias), and a declared `service-duration` overrides it. A related
   asset's `work-rates` capability (a unit-keyed quantity-per-hour map)
   converts any unit kind (m3, items, ha) into effort directly; area-like
   quantities without a declared rate use the width-times-speed coverage
   model, other units fall back to a nominal effort. The model is built
   over a node table: the depot, a pickup node per paired task, a task node
   per order, and one mandatory depot reload stop per routing vehicle when
   any task demands a load. Loads are per-material capacity dimensions: a
   task's `load-material` charges the vehicle's matching compartment
   (`load-capacities`), falling back to the aggregate `load-capacity`
   (vehicles declaring neither are unconstrained). Reload stops reset the
   load dimensions (cvrp-reload slack construction), so demand beyond one
   vehicle fill becomes a second trip instead of dropped tasks
   (`DEPOT_RELOAD_ENABLED=0` restores single-trip semantics). A task
   declaring `pickup-location` becomes a paired pickup-and-delivery: same
   vehicle, pickup before the task, served or dropped together, with the
   load on board only between the pair. With `CLUSTER_LNS_ENABLED=1`,
   clusters whose total lateness penalty reaches
   `CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY` get a second improvement solve from
   the first solution (guided local search plus path/inactive LNS operators)
   bounded by `CLUSTER_LNS_TIME_LIMIT_S`; once feedback exists, the pool
   stamps each eligible cluster with an `lns_time_limit_s` scaled from
   retained LNS objective deltas
   (`$DATA_DIR/cache/solver-feedback/lns-budget.json`) within configured
   min/max multipliers. The first solution is kept unless strictly improved.
9. Aggregate dispatch packages, canonical reason codes, KPIs (priced with the
   resolved cost rates), and reports. Each dispatch package's fuel estimate
   covers the operation plus the inbound travel leg, and its
   `estimated_margin_eur` is the order revenue net of fuel and material at
   the resolved prices (`ResourcePrices`), so per-dispatch margins and KPI
   aggregates are priced from the same cost-rate data. A task whose predecessor went unserved
   in the solve is withdrawn post-solve (`PREDECESSOR_UNSERVED`), so no plan
   dispatches work whose precondition was dropped. Every cluster solve yields
   a machine-readable telemetry record (`solver/solve_telemetry.py`: status,
   wall time, OR-Tools search status, time-limit flag, objective values, LNS
   budget/delta, worker RSS); batch runs write `solve_telemetry.json` and
   plan scores carry the summary.

Enforcement activates only through the adapters (an `EnforcementPolicy` built
from the profile's enforced constraints); the raw batch `solve` pipeline is
unchanged.

The chain's planning time origin is explicit (`run_solver_chain(now=...)`):
cost-rate validity, time-window and restriction filters, routing deadlines,
and held-window offsets all derive from one timestamp. The periodic adapter
passes the snapshot effective time and the rolling compiler the revision
event time, so replayed and synthetic timelines produce exact scheduled
times; wall-clock now is only the fallback for the raw batch pipeline.

## Rolling dispatch

Event application is binding-driven (`stream/apply.py`): the target source
collection and its key column are resolved from the active domain's mapping
documents (canonical entity + identity binding), so the driver knows no
domain-specific column names. Supported triggers:

- `task.started` / `task.progress` / `task.completed`: lifecycle and partial
  completion; progress carries either a `completed_fraction` (scales every
  work-quantity column down to the remaining share) or an absolute
  `remaining_quantity` in the task's work unit (exact overwrite of the
  generic work-quantity column, for domains without a meaningful fraction);
  a fully completed task leaves planning, so re-solves dispatch only the
  remaining effort;
- `order.created` / `order.cancelled`;
- `asset.unavailable`: removes any asset by id -- vehicles, implements,
  operators, and stationary equipment share one path;
- `inventory.adjusted`: partial merge into a location row (depot fuel and
  material balances) without touching its other fields;
- `forecast.updated`: with a payload, upserts the forecast window (weather
  invalidation by data); without one, a pure replan trigger;
- `observation.recorded`: streamed sensor readings upserted by reading id, so
  a re-sent corrected reading replaces the earlier one; readings normalized
  to the canonical `work-progress` metric drive task progress directly from
  telemetry and complete the task at 100 percent;
- `entity.corrected`: a corrected source row upserted by its key column, so
  quality-rejected or wrongly-valued entities re-enter planning.

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
  resource calendar of busy intervals: prime movers and implements get exact
  in-model gap reuse (the routing model blocks the union of the pair's
  intervals as vehicle breaks, so either is reused only in a real
  non-overlapping gap), while operator calendars feed hold-aware allocation
  scoring (operators are not time-modelled inside routing). Held assets are
  classified by solver-row section membership, not id prefixes, so the
  mechanism is domain-neutral.
- Each event yields an immutable plan revision with churn and plan-instability
  metrics.
- `fl-op plan diff-revisions` compares consecutive revisions of a rolling run
  and explains why every changed assignment moved (corrective action, trigger,
  freeze, feasibility change, or optimization tradeoff), writing
  `revision_diff.json`/`.txt` under `.data/revision-diff/<ts>/`.

### Corrective rescheduling

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
  `$DATA_DIR/quality/service-prognosis.jsonl`; accumulated false-positive /
  false-negative rates above thresholds log monitoring-policy tuning
  recommendations. With `MONITORING_AUTO_TUNE_ENABLED=1` the loop closes:
  `snapshot/policy_tuning.py` adjusts `batteryForecastHorizonDays` and
  `compositeHealthThreshold` in bounded steps (max relative step, absolute
  clamps), written to a tuned-policy overlay under `$DATA_DIR/quality` with
  a JSONL audit trail; the reviewed profile document is never modified and
  deleting the overlay reverts to it. Conflicting signals (both rates above
  alert) skip the adjustment but still audit.
- **Completion lead-time feedback** (`stream/lead_time.py`): `task.completed`
  events, fully complete `task.progress` events, and complete
  `work-progress` telemetry append one record per finished task to
  `$DATA_DIR/quality/completion-lead-times.jsonl`. Each record measures
  deadline lead and schedule error against the plan the task was executing
  under; distribution stats are logged after stream runs.

Periodic plans get the same withdrawal/escalation record-keeping: each
periodic run reconciles against its predecessor
(`reconcile_previous_plan`), records the corrective actions on the plan,
persists a `service_reasons.json` artifact for the next run, and appends to
the same prognosis accuracy log.

**Watermark-driven replan triggering**: every published plan carries its
snapshot's `source_watermarks`. `fl-op plan freshness --data <dir> --plan
<dir|latest>` builds a snapshot from the data visible now and compares
(`stream/freshness.py`); with `--replan` a stale plan automatically triggers
a rolling replan. Each check writes a `freshness.json` artifact under
`$DATA_DIR/freshness/<ts>/`.

## Quality and completeness artifacts

- The snapshot carries a compact, exact bundle feasibility summary
  (`snapshot.bundle_summary`): feasible pair counts over the full
  prime-mover x related-equipment cross product, per-operation pair counts,
  and unmatched-resource counts, computed vectorised so the artifact stays
  constant-size at any fleet scale. It also carries the demand side: task
  counts per demanded operation type (including derived service tasks) and
  `scarce_operations`, the demanded operations whose feasible-pair supply is
  below the task count. Concrete bundles are enumerated lazily
  on demand (`snapshot/bundles.py:iter_bundles`), never materialized into
  the snapshot. The solver does its own compatibility filtering, so both are
  explanation artifacts, not assignment inputs.
- A mapped contract whose declared source file is absent from the data
  directory yields a `dq://dataset/source-file-missing` warning finding on the
  snapshot, so an incomplete entity set is visible instead of silent.
- Observation assessment emits `dq://observation/outlier`,
  `dq://observation/sensor-fault`, `dq://observation/metric-drift`,
  `dq://observation/source-flagged`, `dq://observation/future-timestamp`, and
  `dq://observation/timestamp-regression` findings; surviving readings carry a
  confidence and `quality_summary.observation_error_rates` records the share
  of bad readings per source contract.
- `snapshot.source_watermarks` records the newest trusted observed time per
  source contract: what arrived later belongs to the next revision, and
  consumers can tell stale visibility from a quiet world. Observation
  watermarks come from the assessed readings; task/asset/location/forecast
  sources mutated by execution events get theirs from the event applicator
  (the newest applied event's observed time per contract), merged at
  snapshot build with the newest time winning.
- Dataset builds append their error rates to
  `$DATA_DIR/quality/observation-error-rates.jsonl`; a source whose rate
  strictly increases over the last recorded runs is reported as degrading.
  The trend file itself is retained: past QUALITY_TREND_MAX_RECORDS records
  it is compacted in place to the newest records (atomic replace).

## Parameter tuning and experiment tracking

- `fl-op tune` (`tuning/optuna_tuner.py`) runs a seeded Optuna TPE study over
  the tunable solver parameters (`solver/parameters.py:SolverParameters`:
  cluster target size, greedy score weights, per-cluster time limit) against
  recorded KPI baselines built at the trial-scale time budget. It can average
  the objective across additional datasets (`--extra-data`) and, by default,
  records a multi-objective study: maximize business objective (margin minus
  unassigned penalty exposure), minimize plan-instability penalty, and
  minimize wall time. Parallel workers (`--jobs` or TUNE_N_JOBS) use Optuna
  RDB storage; without an explicit URI, `n_jobs > 1` creates
  `study.db` in the tuning run directory. Artifacts: `baseline.json`,
  `trials.json`, `best_params.json` under `$DATA_DIR/tune/<ts>/`, including
  per-dataset case scores and the Pareto frontier.
- `fl-op tune-promote --best-params <run>/best_params.json`
  (`tuning/solver_profile.py`) writes the reviewed tuned solver profile
  overlay `$DATA_DIR/tune/solver-parameters-tuned.json`. Periodic and rolling
  adapters layer that artifact onto the active profile's allocation policy
  when no explicit `SolverParameters` were passed, so deleting the artifact
  reverts to the checked-in profile defaults.
- Opt-in MLflow logging (`tuning/mlflow_logger.py`, MLFLOW_LOGGING_ENABLED):
  tuning trials, the baseline, periodic plans, and the final revision of
  each rolling run are logged with KPIs, version dimensions, and the
  solve-telemetry summary; local SQLite store under `$DATA_DIR/mlruns` by
  default, MLFLOW_TRACKING_URI for a real server. Best-effort only: a
  tracking failure degrades to a warning, never a failed run.

## Schema evolution and CI

- Every ODCS contract (registered domain contracts plus the canonical entity
  and plan contracts) has a committed baseline snapshot of its field schema
  under `contracts/evolution/` (`contracts/evolution.py`). The
  `evolution-check` gate classifies the current schema against the baseline
  and enforces the version-bump policy: added optional fields require at
  least a minor bump; removals, type changes, requiredness changes, and
  added required fields require a major bump; any change without a bump
  fails. `evolution-freeze` records reviewed baselines.
- CI (`.github/workflows/ci.yml`, `make ci`) regenerates all physical
  schemas from ODCS before any validation, then runs the suite validation,
  domain validations, the evolution gate, and the tests.

## Serving

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
