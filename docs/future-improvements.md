# Future Improvements

What remains below are the known gaps and next steps recorded while 
implementing each area. They are forward-looking work, not compatibility work.

## Implementation sequence

1. Snapshot-effective-time basis: compute held-window offsets and routing
   deadlines from the snapshot effective time instead of wall-clock now
   (Solver Quality). Small, and makes every later rolling/solver change
   reproducible against replayed or synthetic timelines.
2. Quick standalone wins, no dependencies in or out: demand-side bundle
   scarcity counts (Snapshot Scale); CI matrix over the supported Python
   range (Data Contracts); instance-level monitoring policy overrides,
   windowed min/mean/last downsampling aggregates, and error-rate file
   rotation (Observations and Monitoring).
3. Work-rate capability surface: per-unit-kind work rates so non-area
   quantities (m3, items) drive duration estimation uniformly (Ontology
   Coverage); includes absolute-remaining-quantity progress payloads
   (Rolling Operations) and unblocks earthworks-native construction
   quantities (Multi-Domain).
4. Routing interval semantics: service-duration-aware occupancy constraints
   so a task cannot run into a restriction window it started before
   (Ontology Coverage), and scheduling execution into compliant weather
   windows (Solver Quality). One shared in-model time-window mechanism.
5. Allocation quality: held implements and operators as resource calendars
   with gap reuse; hold-aware pre-allocation scoring (discount by remaining
   gap capacity); per-task operator pairing; the profile-tunable
   count-vs-margin assignment objective (all Solver Quality). One
   assignment/allocation workstream on top of tasks 1 and 4.
6. Cost-true routing and travel network: routing arc costs from resolved
   cost rates and per-dispatch margins net of fuel/material (Ontology
   Coverage); multi-hop shortest paths over the travel-link graph and
   network times in clustering and greedy repositioning (Ontology Coverage).
7. Material reservations: unify cluster-admission material charging with the
   canonical MaterialReservation outputs (Solver Quality); after task 5 so
   reservations reflect the final allocation mechanism.
8. Load-dimension extensions: per-material compartments, depot reloads
   (multi-trip), pickup-and-delivery pairing (Ontology Coverage). The
   largest routing-model change, isolated after tasks 4-6.
9. Ingestion and visibility foundations: explicit ingested-at column per
   reading and watermarks for task/asset sources (Distributed Operation);
   physical output schemas generated from the plan contract (Ontology
   Coverage) so downstream consumers can validate plans they receive.
10. Exactly-once event pipeline: durable event-id dedup store paired with
    broker offset commits on revision publication (Distributed Operation +
    Serving and Integration).
11. Automated corrective decisions, on the trust foundations from tasks
    9-10: watermark-driven replan triggering (Distributed Operation);
    periodic-plan withdrawal/escalation reconciliation (Corrective
    Rescheduling); profile-tunable composite-score weights (Observations
    and Monitoring), then guarded automatic threshold tuning with bounded
    steps and an audit trail (Corrective Rescheduling).
12. Execution feedback loop: task completion events enabling
    forecast-lead-time measurement, and telemetry-derived progress instead
    of explicit progress events (Corrective Rescheduling).
13. Tuning loop closure: apply best_params.json as a reviewed tuned-profile
    artifact; parallel trials over RDB storage; cross-dataset objective;
    multi-objective study including instability and wall time (Parameter
    Tuning). Best after tasks 5-8 land so tuned parameters cover the new
    knobs.
14. Caching and budgets: dataset-hash caching extended to the candidate
    filter and cluster specs (Performance), then reused for /feasibility
    request caching (Serving and Integration); worker RSS feedback
    calibrating the memory model and LNS budgets proportional to recorded
    objective deltas (Performance).
15. Serving hardening: authentication for non-local deployments; shared
    artifact storage for multi-instance serving; additional broker clients
    through the consumer-factory seam as demand appears (Serving and
    Integration).
16. Domain pack tooling: registrable per-pack data generators, then the
    roadside pack promoted to runnable with a monitoring-driven end-to-end
    test, and per-domain contract-id namespacing (Multi-Domain).
17. Research-grade, demand-driven: revision-diff attribution from solver
    dual/conflict information (Rolling Operations); geometric restricted
    areas (Ontology Coverage); evolution migration history with pairwise
    checks unified with the metadata-hash gate (Data Contracts);
    cross-domain planning over a shared fleet (Multi-Domain).

## Solver Quality

- Pre-allocation is hold-unaware: the global assignment model may reserve a
  held vehicle for a cluster whose work cannot fit the vehicle's free gaps;
  discounting candidate scores by remaining gap capacity would avoid wasted
  reservations.
- The assignment objective is count-first (allocate as many bundles as the
  limits admit, scores only break ties); a profile-tunable tradeoff would let
  a domain prefer fewer, higher-margin allocations.
- Held windows cover vehicles only; held implements and operators are still
  excluded for the whole hold duration. Modelling them as resource calendars
  would unlock the same gap reuse.
- Held-window offsets are computed against wall-clock now, consistent with
  deadline handling in the routing model; basing both on the snapshot
  effective time would make replayed/synthetic timelines exact.
- One operator per cluster: a task whose operation the cluster operator lacks
  is excluded even when another qualified operator sits idle; per-task
  operator pairing (or multiple operators per cluster) would recover those.
- Weather is enforced as any-compliant-window feasibility per task; scheduling
  task execution *into* its compliant windows needs time-window support in the
  routing model.
- Material is charged at cluster admission; integrating it with the canonical
  MaterialReservation outputs would make reservations and feasibility one
  mechanism.

## Ontology Coverage

- Travel links are consumed as direct pair lookups; composing multi-hop
  shortest paths over a road graph, and using network times in clustering
  and greedy repositioning (both still haversine), remain open.
- The load dimension is one aggregate mass per route with single-trip
  semantics; per-material compartments, depot reloads (multi-trip), and true
  pickup-and-delivery pairing (paired pickup/dropoff nodes) would extend it.
- Restriction windows block execution *start* only; a task may run into a
  restriction window it started before. Occupancy semantics need
  service-duration-aware interval constraints. Zone restrictions are
  per-location operation lists; geometric restricted areas (polygons
  intersecting field geometry) are not modelled.
- Cost rates price greedy scoring and KPI aggregates; routing arc costs stay
  time-based and per-dispatch margin estimates do not yet subtract resolved
  fuel/material costs.
- Non-area work quantities (m3, items) fall back to a nominal effort;
  work-rate capabilities per unit kind would make duration estimation
  uniform.
- The plan contract is validated structurally (required bindings resolve);
  generating physical output schemas (Avro/Parquet) from it, as is done for
  input contracts, would let downstream consumers validate plan artifacts
  without this codebase.

## Snapshot Scale

- The bundle feasibility summary covers the power-feasibility rule only;
  folding per-operation candidate counts after the task-level operation
  filter would also expose demand-side scarcity (which operations are short
  on bundles for the actual order book).

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
- CI runs one OS/Python combination; a matrix over the supported Python
  range would catch version-specific regressions.

## Observations and Monitoring

- Make composite-score signal weights and headrooms profile-tunable; they are
  engine constants today (`COMPOSITE_WEIGHT_*`, headroom constants).
- Instance-level policy overrides (a single critical station), layered under
  the per-asset-type overrides.
- Aggregate downsampling: the current downsampler picks evenly spaced readings;
  windowed min/mean/last aggregates would preserve extremes for spiky metrics.
- Trend the quality artifact by retention too: the append-only error-rate file
  grows unboundedly; add rotation or a windowed compaction.

## Distributed Operation and Eventual Consistency

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- Watermark-driven replan triggering: snapshots record per-source watermarks,
  but comparing a published plan's watermarks against newly visible data to
  *automatically* force a corrective replan is still manual.
- First-class ingestion timestamps: arrival order is approximated by source
  row order today; an explicit ingested-at column per reading would make
  regression and skew detection exact across restarts.
- Durable event-id deduplication: the idempotency set is in-memory per stream
  run; replays across process restarts need a persistent dedup store (or
  broker offsets).
- Watermarks for non-observation sources: only observation contracts carry
  watermarks; task/asset sources mutated by events deserve the same
  visibility horizon.

## Corrective Rescheduling

- Automatic threshold tuning: accumulated false-positive / false-negative
  rates currently log recommendations only; a guarded auto-adjustment of the
  domain monitoring policy (bounded step, audit trail) would close the loop.
- The corrective machinery is rolling-only; periodic batch plans get no
  withdrawal or escalation reconciliation against their predecessor.
- Progress derivation: `task.progress` events carry remaining work into
  re-solves, but progress must be reported explicitly; deriving it
  automatically from execution telemetry remains open.
- Forecast-lead-time measurement: outcomes record only withdrawn/escalated
  counts; measuring how early or late each prognosis was (lead-time error
  distribution) needs task completion events.

## Rolling Operations

- `task.progress` scales remaining work by a completed fraction; progress
  reported as absolute remaining quantity (or per-pass coverage geometry)
  would suit domains without a simple fraction.
- The revision diff explains plain re-solve changes as "optimization
  tradeoff"; attributing them further (which resource was taken by which
  higher-priority task) needs solver-side dual/conflict information.

## Parameter Tuning and Experiment Tracking

- Best parameters are reported, not applied: feeding best_params.json back
  into the active profile (a reviewed "tuned profile" artifact consumed by
  plan runs) would close the loop.
- The tuning objective ignores plan instability and solve wall time; a
  multi-objective study would expose those tradeoffs instead of collapsing
  to margin minus penalty.
- Trials run sequentially in-process; Optuna parallel workers over an RDB
  storage would cut wall time for larger studies.
- Tuning sees one snapshot; averaging the objective over several datasets
  would generalize parameters instead of overfitting one order book.

## Serving and Integration

- The API serves one host's filesystem; multi-instance deployments need
  shared artifact storage (or an artifact registry) behind the same routes.
- `/feasibility` rebuilds canonical rows and the compatibility matrix per
  request; caching them by dataset hash (the solver already caches the
  compat matrix this way) would cut request latency.
- There is no authentication; the default loopback bind is the only guard,
  so non-local deployments need an authenticating proxy in front.
- Broker consumption is effectively at-most-once relative to replanning
  (offsets auto-commit independently of revision publication); pairing
  offset commits with published revisions, plus the durable event-id dedup
  store tracked under Distributed Operation, remain open.
- Kafka is the only authored broker client; the injectable consumer factory
  is the seam for other brokers, but no other pack exists yet.

## Multi-Domain

- Construction duration estimation reuses the area-driven coverage model
  (`plot_ha` through the generic work quantity); earthworks-native
  quantities (m3 with machine work rates) wait on the work-rate capability
  surface tracked under Ontology Coverage.
- The roadside pack is validation-level; promoting it to runnable needs a
  data generator plus a monitoring-driven end-to-end test (inspection
  findings -> derived EQUIPMENT_SERVICE visits -> dispatch), for which all
  engine machinery already exists.
- One domain is active per run (registry `activeDomain` or the
  ACTIVE_DOMAIN override); cross-domain planning over a shared fleet in one
  run is not modelled.
- `generate-data --domain` dispatches to hardcoded generators; a domain pack
  cannot yet register its own generator.
- Contract ids share one global registry namespace, forcing the
  `construction-operators` rename; per-domain namespacing would remove it.

## Performance

- The per-worker memory model is an estimate (base footprint + matrix-cell
  coefficient); feeding back measured worker RSS from completed solves would
  calibrate the coefficients per deployment.
- LNS budgets are fixed per cluster; budgets proportional to cluster value
  (the objective deltas recorded in solve telemetry provide the evaluation
  data) remain open.
- The compat cache covers the power matrix; caching the operation-type
  candidate filter and cluster specs by the same dataset-hash scheme would
  extend reuse to the full preprocessing stage.
