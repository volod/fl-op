# Future Improvements

These are forward-looking improvements for the current implementation. They are
not compatibility work.

## Implementation sequence

Preferred order, driven by dependencies (data trust gates decisions, decisions
gate distribution, solver enforcement gates new ontology surface):

1. Statistical data-error assessment - DONE (see
   [current-implementation.md](current-implementation.md)).
2. Observations and Monitoring - DONE (composite scoring, per-asset-type
   policies, retention/downsampling, quality-flag ingestion, cross-run
   error-rate trending; follow-up findings below).
3. Corrective Rescheduling - DONE (asset-loss repair, withdrawal and
   escalation records, entity.corrected events, prognosis accuracy feedback;
   follow-up findings below).
4. Distributed Operation and Eventual Consistency - DONE (per-source snapshot
   watermarks, clock-skew and timestamp-regression findings, idempotent event
   application, correction upserts, time-window series aggregation,
   convergence-window replan debouncing; effect catalog in
   [reference/model-world-divergence.md](reference/model-world-divergence.md);
   follow-up findings below).
5. Rolling Operations - DONE (task.progress partial completion,
   inventory.adjusted, forecast upserts, operator unavailability via
   asset.unavailable, `fl-op plan diff-revisions` explaining every moved
   assignment; follow-up findings below).
6. Solver Quality - DONE (declared-constraint enforcement: weather windows,
   operator qualification, material availability; CP-SAT global assignment
   pre-allocation with greedy fallback; optional LNS improvement pass for
   high-value clusters; held rolling assignments as vehicle time-window
   breaks; follow-up findings below).
7. Ontology Coverage - DONE (task precedence, multiple workable windows,
   generic work quantity, travel-network entity, vehicle load capacities,
   restricted zones / time-restricted areas, cost rates as data entities,
   canonical plan output contract; every added surface is solver-consumed;
   follow-up findings below).
8. Multi-Domain - DONE (construction pack promoted to runnable: registered
   contracts, data generator, ACTIVE_DOMAIN selection, domain-neutral solver
   projection; roadside-infrastructure example pack authored; follow-up
   findings below).
9. Snapshot Scale and Performance - DONE (exact bundle feasibility summary
   with lazy enumeration replacing the capped bundle list; dataset-hash
   compat-matrix cache; memory-aware pool sizing; machine-readable
   per-cluster solve telemetry with LNS deltas; follow-up findings below).
10. Parameter Tuning, Data Contracts CI / schema evolution, Serving.

## Solver Quality

All three remaining items are implemented:

- Greedy cluster pre-allocation is replaced by a small CP-SAT global
  assignment model (`solver/allocation/global_model.py`): scarce vehicles,
  implements, and operators are decided across all clusters at once, so a
  high-penalty cluster can no longer starve a later cluster when an
  alternative resource mix serves both. The greedy reservation loop remains
  as the fallback (`GLOBAL_ASSIGNMENT_ENABLED=0`, oversized model, or no
  CP-SAT solution in time).
- An optional Large Neighbourhood Search improvement pass
  (`CLUSTER_LNS_ENABLED=1`) re-solves high-value clusters (total lateness
  penalty at or above `CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY`) from the first
  OR-Tools solution with guided local search and path/inactive LNS operators,
  bounded by `CLUSTER_LNS_TIME_LIMIT_S`.
- Held rolling assignments are tracked as vehicle time-window constraints: a
  frozen/carried vehicle re-enters the incremental re-solve with its busy
  intervals modelled as break intervals on the routing time dimension, so it
  is reused only in a real non-overlapping gap instead of being excluded
  outright.

Follow-up findings from the global assignment, LNS, and held-window work:

- Pre-allocation is hold-unaware: the assignment model may reserve a held
  vehicle for a cluster whose work cannot fit the vehicle's free gaps;
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
- The LNS pass is a fixed second solve; the objective delta is now recorded
  in the machine-readable solve telemetry, while budgets proportional to
  cluster value remain open (see Performance).

Follow-up findings from the implemented constraint enforcement (weather
windows, operator qualification, material availability):

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

All gaps identified in
[reference/optimization-ontology.md](reference/optimization-ontology.md) are
implemented, each with solver consumption:

- Task precedence (`urn:xopt:relationship:depends-on`): chain-aware
  clustering, in-model precedence constraints, transitive cascade exclusion,
  and post-solve dependent withdrawal.
- Multiple workable time windows (`urn:xopt:time:workable-windows`):
  chain-level pre-filter plus in-model admissible start intervals.
- Generic work quantity (`urn:xopt:attribute:work-quantity` + unit +
  `service-duration` override): duration estimation is quantity-driven; area
  is its legacy alias.
- Travel network (`travel-link` entity, `routes` source contract): routing
  arc times use directed link lookups with reverse-direction and haversine
  fallbacks, so a sparse network is valid input
  (`solver/travel_time.py`).
- Vehicle load capacities (`urn:xopt:capability:load-capacity` vs
  `urn:xopt:attribute:load-demand`): a routing capacity dimension bounds
  each route's cumulative delivered mass; capacity-free vehicles stay
  unconstrained and the dimension is skipped without load demands.
- Restricted zones and time-restricted areas
  (`location.restrictedOperations`, `location.restrictionWindows`):
  chain-level exclusion (`RESTRICTED_ZONE`) plus in-model start-interval
  blocking (`solver/restrictions.py`).
- Cost rates (`cost-rate` entity, `prices` source contract): fuel and
  material prices resolve from validity-windowed rate rows into greedy
  scoring and KPIs, with the engine constants as fallback
  (`solver/cost_rates.py`).
- Canonical plan output contract
  (`contracts/canonical/odcs/plan.odcs.yaml`): published plans are validated
  binding-by-binding at publication time (`contracts/plan_contract.py`).

Follow-up findings from the ontology-coverage work:

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

Implemented: the capped materialized bundle list is gone. The snapshot
carries a compact `BundleFeasibilitySummary` (`snapshot/bundles.py`) with
exact counts computed vectorised over the full prime-mover x related-equipment
cross product (feasible pairs, per-operation pair counts, unmatched
resources), so the artifact stays constant-size at any fleet scale and the
former truncation diagnostics are unnecessary. Consumers that need concrete
bundles enumerate them lazily through `iter_bundles` (deterministic order,
filterable by operation type or participating asset); nothing is materialized
beyond the yielded bundle.

Follow-up findings:

- The summary covers the power-feasibility rule only; folding per-operation
  candidate counts after the task-level operation filter would also expose
  demand-side scarcity (which operations are short on bundles for the actual
  order book).

## Data Contracts

- Generate Parquet descriptors and Avro schemas in CI before validation so stale
  generated files cannot pass unnoticed.
- Add a schema-evolution policy: versioned contract migrations with explicit
  compatibility checks between contract versions.

## Observations and Monitoring

Follow-up findings from the implemented composite scoring, per-type policies,
retention, quality-flag ingestion, and cross-run trending:

- Make composite-score signal weights and headrooms profile-tunable; they are
  engine constants today (`COMPOSITE_WEIGHT_*`, headroom constants).
- Instance-level policy overrides (a single critical station), layered under
  the per-asset-type overrides.
- Aggregate downsampling: the current downsampler picks evenly spaced readings;
  windowed min/mean/last aggregates would preserve extremes for spiky metrics.
- Trend the quality artifact by retention too: the append-only error-rate file
  grows unboundedly; add rotation or a windowed compaction.

## Distributed Operation and Eventual Consistency

Follow-up findings from the implemented watermarks, idempotency, skew
handling, window aggregation, and convergence debouncing (effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md)):

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

Follow-up findings from the implemented asset-loss repair, withdrawal and
escalation records, `entity.corrected` events, and prognosis feedback:

- Automatic threshold tuning: accumulated false-positive / false-negative
  rates currently log recommendations only; a guarded auto-adjustment of the
  domain monitoring policy (bounded step, audit trail) would close the loop.
- The corrective machinery is rolling-only; periodic batch plans get no
  withdrawal or escalation reconciliation against their predecessor.
- Partial-completion repair: `task.progress` events now carry remaining work
  into re-solves; what remains open is deriving progress automatically from
  execution telemetry instead of explicit progress events.
- Forecast-lead-time measurement: outcomes record only withdrawn/escalated
  counts; measuring how early or late each prognosis was (lead-time error
  distribution) needs task completion events.

## Rolling Operations

Follow-up findings from the implemented event effects and revision diff:

- `task.progress` scales remaining work by a completed fraction; progress
  reported as absolute remaining quantity (or per-pass coverage geometry)
  would suit domains without a simple fraction.
- The revision diff explains plain re-solve changes as "optimization
  tradeoff"; attributing them further (which resource was taken by which
  higher-priority task) needs solver-side dual/conflict information.
- Weather upserts update forecast data, but the weather-window constraint is
  still not enforced in the solver (tracked under Solver Quality).

## Parameter Tuning and Experiment Tracking

- Tune solver parameters (cluster target size, greedy score weights, per-cluster
  time limits) with Optuna against recorded KPI baselines.
- Log run KPIs, version dimensions, and solve telemetry to MLflow so parameter
  experiments are comparable across datasets.

## Serving and Integration

- A thin service API exposing query-contract feasibility checks and published
  plan retrieval.
- Event-bus ingestion (broker-backed) for `observation.recorded` and other
  execution events, replacing the JSONL stream source in deployments.

## Multi-Domain

Both items are implemented:

- The construction pack is runnable end to end: its six contracts are
  registered (the operator master as `construction-operators`; contract ids
  are a global namespace), the construction-earthworks profile is registered,
  `fl-op generate-data --domain construction` produces a conforming dataset
  (machines, attachments, operators, yards, sites, jobs), and
  `ACTIVE_DOMAIN=construction fl-op plan periodic` runs the identical
  snapshot -> adapter -> plan pipeline. Enabling change: the solver-input
  projection resolves binding tables by canonical entity and asset role,
  never by contract id, and the rolling compiler classifies held assets by
  solver-row section membership instead of id prefixes.
- The roadside-infrastructure example pack
  (`contracts/domains/roadside/`) is a validation-level mapping pack:
  stationary signage/sensor assets along road segments (anchored to their
  segment via `asset.homeDepotRef`), maintenance depots, lane-closure
  curfews as canonical restriction windows, and inspection rounds as the
  observation source (condition ratings normalized to canonical metric codes
  via `metricCodes`); its profile carries per-asset-type monitoring
  overrides (speed radars get stricter battery thresholds).

Follow-up findings from the multi-domain work:

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

All three items are implemented:

- Compatibility matrices are cached by dataset hash
  (`solver/feasibility.py:cached_compat_matrix`): the key is a content hash
  over exactly the inputs the matrix derives from (asset ids, power
  capabilities, the power margin), stored as `.npz` under
  `$DATA_DIR/cache/compat-matrix` with a bounded entry count
  (`COMPAT_MATRIX_CACHE_MAX_ENTRIES`, `COMPAT_MATRIX_CACHE_ENABLED=0` to
  disable). Safe by construction: any input change changes the key, and any
  cache trouble falls back to a plain rebuild.
- Auto pool sizing is memory-aware (`solver/cluster_pool.py:
  compute_pool_sizing`): the per-worker footprint is estimated from the
  largest cluster's routing-model size (n_nodes^2 x (n_vehicles + 1) cells on
  top of `SOLVER_WORKER_BASE_MEMORY_MB`) and the worker count is additionally
  bounded by available memory (`/proc/meminfo` MemAvailable with a sysconf
  fallback), keeping `SOLVER_MEMORY_HEADROOM_PCT` free. An explicit
  `SOLVER_WORKERS` still wins; unmeasurable memory keeps CPU-based sizing.
- Every cluster solve yields a machine-readable telemetry record
  (`solver/solve_telemetry.py`): model size, wall time, OR-Tools search
  status, time-limit flag, objective values, and the LNS attempt/improvement
  delta; crashed workers and pool-timeout cancellations get synthesized
  records. Batch runs write `solve_telemetry.json` (records + summary) and
  periodic/rolling plan scores carry the summary.

Follow-up findings:

- The per-worker memory model is an estimate (base footprint + matrix-cell
  coefficient); feeding back measured worker RSS from completed solves would
  calibrate the coefficients per deployment.
- LNS budgets are still fixed per cluster; budgets proportional to cluster
  value (the recorded objective deltas now provide the evaluation data)
  remain open.
- The compat cache covers the power matrix; caching the operation-type
  candidate filter and cluster specs by the same dataset-hash scheme would
  extend reuse to the full preprocessing stage.
