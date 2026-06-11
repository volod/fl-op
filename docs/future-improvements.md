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
7. Ontology Coverage (add surface only once the solver can consume it).
8. Multi-Domain.
9. Snapshot Scale and Performance.
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
- The LNS pass is a fixed second solve; budgets proportional to cluster value
  and recording the objective delta in machine-readable solve telemetry
  remain open (see Performance).

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

Gaps identified in
[reference/optimization-ontology.md](reference/optimization-ontology.md):

- Task precedence / dependency relations for multi-stage work sequences.
- Multiple task time windows; only a single deadline binding exists today.
- A travel-network entity (distance/time matrices, road graphs) so travel is
  not limited to haversine distance and asset travel speed.
- A generic work-quantity semantic term; duration estimation is area-driven.
- Vehicle load capacities for pickup-and-delivery flows.
- Restricted zones and time-restricted areas beyond soil-type restrictions.
- Cost rates as data entities (fuel and material prices are engine constants).
- A canonical output contract for plans, mirroring the input entity contracts.

## Snapshot Scale

- Replace the capped materialized bundle list with a lazy bundle index or a
  compact feasibility summary.

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

- A construction-domain data generator and end-to-end solver wiring, promoting
  the existing mapping pack from validation-only to runnable.
- A roadside-infrastructure example pack: stationary signage/sensor assets along
  road segments with inspection rounds as observation sources.

## Performance

- Cache compatibility matrices by dataset hash.
- Add process-pool sizing based on measured per-cluster memory instead of CPU
  count alone.
- Record per-cluster solve quality and timeout diagnostics in machine-readable
  artifacts.
