# Future Improvements

What remains below are the known gaps and next steps recorded while 
implementing each area. They are forward-looking work, not compatibility work.

## Implementation sequence

1. Snapshot-effective-time basis - DONE: the solver chain takes an explicit
   planning time origin (`run_solver_chain(now=...)`) threaded down to the
   routing model; the periodic adapter passes the snapshot effective time,
   the rolling compiler the revision event time. Cost-rate validity,
   time-window/restriction filters, routing deadlines, and held-window
   offsets all derive from it; wall-clock now remains only the fallback for
   the raw batch pipeline.
2. Quick standalone wins - DONE: the bundle summary carries the demand side
   (task counts per demanded operation and `scarce_operations`, computed
   after monitoring so derived service tasks count); CI runs a matrix over
   Python 3.10-3.13; monitoring policies take instance-level
   `assetOverrides` layered on the per-asset-type overrides; downsampling
   window representatives carry min/mean/max/count aggregates so spikes
   survive aggregation; the error-rate trend file is compacted in place to
   the newest QUALITY_TREND_MAX_RECORDS records.
3. Work-rate capability surface - DONE: the canonical work-rates capability
   (`urn:xopt:capability:work-rates`, a unit-keyed quantity-per-hour map on
   related equipment) drives duration estimation uniformly for any unit
   kind; a declared rate wins, the width-times-speed coverage model stays
   the area fallback, the nominal effort covers the rest. task.progress
   accepts an absolute `remaining_quantity` payload next to
   `completed_fraction`, and the construction pack is earthworks-native
   (m3 job quantities, m3-per-hour attachment rates).
4. Routing interval semantics - DONE: one shared blocked-interval mechanism
   in the routing model covers location restriction windows and
   non-compliant weather windows. Start domains subtract every block, and
   reified occupancy constraints (finish by the block start or begin after
   its end, with the serving vehicle's service duration resolved by element
   lookup on the vehicle variable) keep the whole execution interval out of
   each block. The weather filter now returns each kept sensitive task's
   non-compliant forecast windows, threaded to the cluster workers.
5. Allocation quality - DONE: every held asset (prime mover, implement,
   operator) travels into the rolling re-solve as a resource calendar of
   busy intervals. The routing model blocks the union of the pair's
   intervals as vehicle breaks (exact gap reuse for held implements), and
   each held asset's free share of the capacity horizon discounts
   pre-allocation candidate scores and operator rewards (hold-aware
   scoring). Qualification enforcement pairs a free qualified backup
   operator per uncovered task (the cluster's task_operators map, carried
   into dispatch packages) instead of dropping the task, and the
   count-vs-margin assignment objective is profile-tunable
   (allocationPolicy.countPriority -> SolverParameters
   .assignment_count_priority; 1.0 keeps count-first, 0.0 maximizes score).
6. Cost-true routing and travel network - DONE: the travel lookup is the
   all-pairs shortest-path closure over the directed link graph (Dijkstra
   per source, node-cap guarded), so multi-hop connections count; depot
   clustering and greedy repositioning use network times where paths exist
   (haversine fallback). Routing arcs are priced per vehicle as travel fuel
   cost in the shared objective currency (1 EUR = 600 penalty seconds, the
   drop-penalty conversion), so a fuel-efficient machine wins time-equal
   legs; dispatch fuel includes the inbound travel leg and
   estimated_margin_eur is revenue net of fuel and material at the resolved
   cost-rate prices (ResourcePrices threaded to the cluster workers).
7. Material reservations - DONE: cluster-admission material charging is the
   reservation mechanism. Every admitted charge becomes a provisional
   reservation record, settled against the final dispatch (confirmed with
   the scheduled window, or released when the solve left the task unserved)
   and published as canonical MaterialReservation rows on the plan, with
   assignments linking their reservation ids. Rolling revisions re-publish
   the reservations of frozen/carried tasks so each revision is
   self-contained.
8. Load-dimension extensions - DONE: the routing model is built over a node
   table (depot, pickup, task, reload nodes) instead of order-indexed nodes.
   One capacity dimension per load material (`load-material` on tasks,
   per-material `load-capacities` compartments on prime movers, aggregate
   `load-capacity` fallback); each routing vehicle gets a mandatory depot
   reload stop that resets the load dimensions via the cvrp-reload slack
   construction, so demand beyond one fill becomes a second trip instead of
   dropped tasks (DEPOT_RELOAD_ENABLED=0 restores single-trip semantics);
   and a task declaring `pickup-location` becomes a paired
   pickup-and-delivery (same vehicle, pickup first, served or dropped
   together, load carried only between the pair).
9. Ingestion and visibility foundations - DONE: readings carry an explicit
   `ingested-at` timestamp (canonical observation field, agricultural
   sensor-readings column and generator); when a whole series carries it,
   timestamp-regression detection orders arrivals by ingestion time instead
   of approximating with source row order, exact across restarts. The event
   applicator records a visibility watermark per mutated source contract
   (task/asset/location/forecast), merged into `snapshot.source_watermarks`
   next to the observation watermarks. The canonical plan output contract
   generates physical Avro/Parquet schemas (`contracts generate`), built
   over the artifact payload names from the publication validator's binding
   table, so consumers validate plan.json without this codebase.
10. Exactly-once event pipeline - DONE: broker offsets are never
    auto-committed; the consumer stays open after the drain and commits only
    after the run's revisions are published. A durable event-id store
    (`stream/dedup.py`, an append-only id log under DATA_DIR/stream,
    compacted in place) records each published revision's applied event ids
    right before the offset commit, and the event applicator suppresses ids
    published by earlier runs. A crash before publication replays the
    backlog (nothing lost); a crash between record and commit redelivers
    events the store suppresses (nothing duplicated) - effectively-once end
    to end. The JSONL development source replays files intentionally and
    never uses the store.
11. Automated corrective decisions - DONE: plans carry their snapshot's
    source watermarks and `fl-op plan freshness` compares a published plan
    against the data visible now (`stream/freshness.py`), triggering a
    rolling replan automatically with `--replan` when newer data passed the
    plan's horizon. Periodic plans reconcile against their predecessor
    (`reconcile_previous_plan`): withdrawn and escalated service prognoses
    become corrective actions, the service-reasons artifact persists per run
    for the next reconciliation, and outcomes feed the same prognosis
    accuracy log rolling uses. Composite-score weights and headrooms are
    profile-tunable (MonitoringPolicySpec/Override). Guarded auto-tuning
    (`snapshot/policy_tuning.py`, opt-in via MONITORING_AUTO_TUNE_ENABLED)
    adjusts batteryForecastHorizonDays and compositeHealthThreshold in
    bounded steps with absolute clamps, written to a tuned-policy overlay
    under DATA_DIR/quality with a JSONL audit trail; the reviewed profile is
    never modified and deleting the overlay reverts to it.
12. Execution feedback loop - DONE: `task.completed` events remove finished
    work from the rolling source state and capture completion evidence before
    the row disappears; fully complete `task.progress` events and
    `work-progress` telemetry observations share the same completion path.
    The stream driver writes one record per completion to
    `$DATA_DIR/quality/completion-lead-times.jsonl`, measuring deadline lead
    and schedule error against the previous plan. Agricultural sensor
    readings normalize `work_progress_pct` to the canonical `work-progress`
    metric, so streamed telemetry can scale or complete task work without an
    explicit progress event.
13. Tuning loop closure - DONE: `fl-op tune` can evaluate one parameter set
    over multiple datasets (`--extra-data`) and records per-case scores,
    averaged business objective, wall time, and plan-instability objective
    values. The default study is multi-objective (maximize margin net of
    unassigned penalty exposure, minimize instability, minimize wall time)
    and publishes the Pareto frontier plus a primary-best recommendation in
    `best_params.json`. Parallel workers (`--jobs` / TUNE_N_JOBS) run over
    Optuna RDB storage, defaulting to a local `study.db` when no storage URI
    is supplied. `fl-op tune-promote` turns `best_params.json` into the
    reviewed tuned solver overlay
    `$DATA_DIR/tune/solver-parameters-tuned.json`, and periodic/rolling
    adapters apply it on top of the checked-in profile defaults unless the
    caller passed explicit `SolverParameters`.
14. Caching and budgets - DONE: preprocessing now caches the
    operation-filtered vehicle/implement candidates and cluster specs under
    `$DATA_DIR/cache/preprocessing`, keyed by stable content hashes over the
    canonical solver rows, compatibility matrix digest, target size, and
    travel lookup. `/feasibility` reuses the compat/candidate caches and
    stores exact request responses under `$DATA_DIR/cache/feasibility`, keyed
    by source-file bytes, schedule.json, and the order payload. Cluster
    telemetry records per-worker RSS; `$DATA_DIR/cache/solver-feedback`
    persists max observed worker RSS as a floor on future auto worker memory
    estimates, and LNS feedback scales per-cluster improvement budgets from
    recorded objective deltas within bounded multipliers.
15. Serving hardening - DONE: `fl-op serve` now supports a static bearer
    token (`SERVE_AUTH_TOKEN`) for all plan and feasibility routes while
    leaving `/health` public, and refuses non-loopback binds unless that
    token is configured. The API reads through a small `ArtifactStore`
    abstraction; the default filesystem store can point at
    `SERVE_ARTIFACT_ROOT`, so several service instances can expose the same
    mounted plan/dataset tree behind unchanged routes. Event ingestion now
    resolves `EVENT_SOURCE_KIND` through a registered source-factory table:
    JSONL and Kafka are built in, and integrations can register additional
    source kinds plus whether they need the durable event-id dedup store.
16. Domain pack tooling - DONE: `generate-data --domain` now resolves the
    generator callable declared by the domain spec in `contracts/registry.yaml`
    (`fl_op.data.domain_generators.GenerationRequest` is the shared call
    shape), so packs register generators without CLI branches. Roadside is
    fully runnable: it registers service vehicles, service kits, technicians,
    road segments, service depots, optional maintenance jobs, signage, and
    inspection rounds; its generator emits a dispatchable service fleet and
    inspection findings that derive `EQUIPMENT_SERVICE` visits, covered by an
    end-to-end monitoring-to-plan test. Contract refs are now domain-local
    aliases resolved by `(domain, local_id)`, so construction can refer to
    `operators` while the registry keeps the compatibility key
    `construction-operators`.
17. Research-grade, demand-driven: revision-diff attribution from solver
    dual/conflict information (Rolling Operations); geometric restricted
    areas (Ontology Coverage); evolution migration history with pairwise
    checks unified with the metadata-hash gate (Data Contracts);
    cross-domain planning over a shared fleet (Multi-Domain).

## Solver Quality

- Operator time is not modelled in routing: a cluster operator (or per-task
  backup) nominally works all routing vehicles in parallel, and a held
  operator's calendar only discounts allocation scoring, so overlapping
  operator work is discouraged but not prevented. Exact operator gap reuse
  needs an operator time dimension in the routing model.
- Hold-aware scoring discounts by the asset's free share of a fixed capacity
  horizon (the rolling dispatch horizon), not by whether the cluster's
  expected execution window actually fits the asset's specific gaps; a
  timing-aware comparison would discount more precisely.
- A backup operator is claimed by one cluster for the whole run; sharing an
  idle operator across clusters at non-overlapping times is not modelled.
- Weather blocking is forecast-bounded: time not covered by any forecast
  window is optimistically treated as workable, so a deadline beyond forecast
  coverage schedules into unknown weather; a conservative mode (treat
  uncovered time as blocked until a compliant forecast exists) would suit
  risk-averse domains.
- Material demand is declared per hectare only (materialDemand.perAreaHa), so
  non-area work (m3, items) never charges material; a per-unit-kind demand
  basis would mirror the work-rate capability surface. Reservations also have
  no time dimension in feasibility: charges are horizon-cumulative, not
  windowed against replenishment.

## Ontology Coverage

- Greedy repositioning takes the vehicle's home depot as its road access
  point for network times; a vehicle far from its depot still gets the
  straight-line estimate from its current position. Mapping a vehicle's
  position to the nearest network node would generalize this.
- Reload visits are mandatory depot stops, one per routing vehicle, bounding
  each route to one extra trip; truly optional reload nodes need search
  support for coupled insertions (inactive-LNS or guided-local-search
  budgets), and more trips need more stops. Pickup locations resolve against
  the cluster's site table only (no supplier locations outside it), and no
  domain pack yet generates compartment or pickup-and-delivery data, so
  those paths are exercised by engine tests only.
- Workable windows still bound execution *start* only (occupancy semantics
  cover restriction and weather blocks); finishing within the declared
  workable window is not enforced. Zone restrictions are per-location
  operation lists; geometric restricted areas (polygons intersecting field
  geometry) are not modelled.
- The routing objective prices travel fuel only; driver time, machine wear,
  and per-kilometre tolls have no cost rates, so a long slow road and a
  short fast one with equal fuel burn are objective-equal. Additional
  cost-rate types would extend the same arc-pricing mechanism.
- Work-rate units match by exact unit-code equality ("m3", "items"); there
  is no controlled unit vocabulary or conversion between compatible units
  (a task quantity in "t" never matches a rate declared in "kg"). Rates are
  flat per implement; productivity modifiers (ground class, prime-mover
  pairing) are not modelled.
- Plan output schemas cover Avro and Parquet only (no proto/Elasticsearch),
  and only the contract-declared fields: the artifact's free-form score,
  quality-summary, and corrective-action sections are not schematized.

## Data Contracts

- The schema-evolution policy compares against a single committed baseline
  (the last reviewed state); a full migration history (one snapshot per
  released version, with pairwise compatibility checks) would let consumers
  more than one version behind validate their upgrade path.
- Evolution compatibility is structural (names, types, requiredness).
  Semantic changes (unit switches, enum value sets in custom properties) are
  invisible to it; they are guarded separately by the
  optimizationMetadataHash drift check, but the two gates are not unified
  into one review flow.

## Observations and Monitoring

- Composite weights are declared statically per profile (and per asset
  type/instance via overrides); learning them from prognosis outcomes, the
  way thresholds are auto-tuned, remains open.

## Distributed Operation and Eventual Consistency

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- The freshness check is pull-based: a scheduler (or operator) invokes
  `plan freshness --replan`; a serving-side daemon watching source
  visibility continuously and replanning in place remains open.
- Ingestion timestamps cover the agricultural sensor-readings source only;
  other sources (and other domain packs) do not emit ingested-at, and a
  series with any reading missing it falls back to row order. Event
  watermarks skip `entity.corrected` (its target contract is resolved by
  key column, not declared).
- Offset commits are per run: the whole drained backlog commits after all of
  the run's revisions are published. A daemon-style unbounded consumer would
  need periodic mid-stream commit points (per converged batch) to bound the
  redelivery window.

## Corrective Rescheduling

- Auto-tuning adjusts two policy fields globally (forecast horizon,
  composite threshold) from aggregate rates; per-asset-type tuning and
  additional tunables (battery thresholds) would need per-type accuracy
  splits in the prognosis log.
- Completion lead-time feedback is append-only and aggregate-only today: the
  driver logs deadline lead and schedule error, and reports distribution
  statistics, but no policy currently consumes the lead-time distribution for
  automatic threshold changes. Folding lead-time error into guarded tuning
  would be the next closed-loop step after the reviewed tuned-profile flow.

## Rolling Operations

- Progress payloads and telemetry cover the completed fraction and the
  absolute remaining quantity; per-pass coverage geometry (spatially explicit
  progress over the work area) remains open.
- The revision diff explains plain re-solve changes as "optimization
  tradeoff"; attributing them further (which resource was taken by which
  higher-priority task) needs solver-side dual/conflict information.

## Parameter Tuning and Experiment Tracking

- The tuned solver overlay is intentionally one reviewed operational artifact
  under `DATA_DIR/tune`; it is not yet keyed by optimization profile id,
  domain, adapter version, or expiry window. Deployments with several active
  profiles should promote to separate artifact roots or add artifact-registry
  selection metadata before sharing storage.
- The multi-objective instability value is read from solver/plan KPIs when
  available. Direct periodic tuning has no previous revision, so its
  instability objective is normally zero; a rolling replay tuning harness
  would measure real churn over event sequences.
- Cross-dataset tuning weights each dataset equally. Workload-size weighting,
  holdout validation, or per-domain objective weights would reduce the risk
  that a tiny smoke dataset influences the result as much as a production
  order book.
- Parallel tuning now coordinates through Optuna RDB storage, but worker
  resource budgets are static. Feeding observed CPU/RSS and objective deltas
  into worker counts and LNS budgets remains part of the caching/budgets work.

## Serving and Integration

- Serving authentication is a deployment-shared static bearer token. It does
  not yet provide OIDC/JWT validation, per-route authorization, token
  rotation, audit logging, or rate limiting; those still belong at an
  ingress/proxy layer or in a future auth provider.
- Shared serving storage is currently filesystem-backed (`SERVE_ARTIFACT_ROOT`
  or `$DATA_DIR`). A true object-store or artifact-registry backend would
  need consistency semantics, artifact manifests, and cache invalidation for
  newly published runs.
- Event-source extension is now a registry seam, but Kafka remains the only
  durable broker client shipped with the repository. Additional production
  clients still need small adapter packages that register their factory and
  opt into deduplication when the source can redeliver.

## Multi-Domain

- One domain is active per run (registry `activeDomain` or the
  ACTIVE_DOMAIN override); cross-domain planning over a shared fleet in one
  run is not modelled.
- Generator registration is a Python callable path in the local registry.
  There is no plugin discovery, versioned generator packaging, or generator
  capability declaration yet; external packs still need their Python module
  importable in the running environment.
- Domain-local contract ids are aliases over the existing flat registry keys,
  not a nested registry-file format. That preserves compatibility but still
  leaves generated schema filenames and evolution baseline filenames keyed by
  the global registry id.

## Performance

- Preprocessing cache keys are conservative content hashes. They are safe
  across source changes, but they do not yet share a single named
  `snapshot_hash` namespace with all plan artifacts; a future artifact
  registry could make cache provenance easier to inspect and evict.
- Feasibility request caching is exact-request caching. Similar orders or
  equivalent schedules with different JSON ordering do not share results, and
  the endpoint still hashes source bytes before it can return a cached
  response.
- Worker memory feedback uses the retained max observed RSS as a deployment
  floor. It does not yet fit separate coefficients by cluster size, node
  count, load dimensions, or domain pack.
- LNS feedback applies a global multiplier from historical objective deltas.
  Per-cluster budget learning (for example by operation type, cluster size,
  or penalty distribution) would target the time where it pays off most.
