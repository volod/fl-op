# Future Improvements

This file tracks open future work only. Implemented behavior belongs in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The backlog is arranged in the recommended delivery order: governance and
artifact foundations first, then planner semantics, domain fidelity, tuning,
serving, and integration hardening.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

1. Multi-domain staging and policy composition. Add collision-free
   mixed-domain source staging, composite profile/policy merging, and
   generator capability metadata.
2. Event visibility and continuous replanning. Extend ingested-at timestamps
   and source watermarks to all mutable sources, add bounded mid-stream offset
   commits, then run freshness/replan logic from a serving-side watcher.
3. Temporal solver correctness. Add an operator time dimension, gap-aware
   held-asset scoring, non-overlapping backup-operator sharing, conservative
   unknown-weather handling, and finish-within-window enforcement.
4. Optional time-objective validation. Add a slow/cheap vs fast/expensive
   comparison workload and deadline-urgency calibration for `--objective time`.
5. Unit, material, and resource semantics. Add a controlled unit vocabulary
   with conversions, per-unit-kind material demand, time-windowed material
   reservations/replenishment, and productivity modifiers.
6. Routing topology and geography. Map current vehicle positions to network
   nodes, support optional reload and multiple reload trips, resolve supplier
   pickup locations outside the cluster site table, and improve partial
   restricted-area handling.
7. Cost model expansion. Price driver time, machine wear, tolls, and other arc
   or service costs after routing topology is expressive enough for those
   rates to change decisions.
8. Spatial execution feedback. Capture per-pass coverage geometry and use it
    to refine remaining work, partial-area restrictions, and rolling progress
    explanations.
9. Closed-loop monitoring policy. Learn composite health weights from
    prognosis outcomes, consume completion lead-time distributions, and split
    auto-tuning by asset type and additional tunables such as battery
    thresholds.
10. Experiment and tuning maturity. Add generic rolling replay datasets for
    real instability measurement, holdout validation, per-domain objective
    weights, CPU/RSS-aware worker selection, cluster-size memory coefficients,
    and per-cluster LNS budget learning.
11. Serving and integration hardening. Add OIDC/JWT validation, route-level
    authorization, token rotation, audit/rate-limit hooks, object-store
    artifact backends, and additional durable event clients.
12. Solver explanation research. Investigate exact resource-conflict
    attribution through richer solver instrumentation or an alternative model
    that exposes dual/shadow-price signals.

## Drone Logistics Remaining Limits

- No 3D airspace deconfliction, altitude corridor planning, or
  vehicle-to-vehicle separation is modeled.
- Routing around restricted sub-polygons remains future work.
- Charging-station scheduling and charging queue capacity are not modeled.
- Mobile drone predictive maintenance remains future monitoring work unless
  monitoring policy is extended beyond the current stationary-service-task
  behavior.

## Optional Time-Objective Tuning

- Add an e2e comparison dataset with slow/cheap and fast/expensive resources
  proving `--objective time` lowers completion-time KPIs without weakening
  assignment count, hard deadlines, or safety restrictions.
- Add deadline-slack/customer-class calibration for time mode when a deployment
  needs stronger urgency ordering than the current hard deadlines plus
  penalty-per-day scoring provide.

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
  coverage schedules into unknown weather. A conservative mode would treat
  uncovered time as blocked until a compliant forecast exists.
- Material demand is declared per hectare only (`materialDemand.perAreaHa`),
  so non-area work (`m3`, `items`) never charges material. Reservations also
  have no time dimension in feasibility: charges are horizon-cumulative, not
  windowed against replenishment.

## Ontology Coverage

- Greedy repositioning takes the vehicle's home depot as its road access point
  for network times; a vehicle far from its depot still gets the straight-line
  estimate from its current position. Mapping a vehicle's position to the
  nearest network node would generalize this.
- Reload visits are mandatory depot stops, one per routing vehicle, bounding
  each route to one extra trip. Truly optional reload nodes need search
  support for coupled insertions, and more trips need more stops.
- Pickup locations resolve against the cluster's site table only; supplier
  locations outside that table are not yet supported.
- Compartment-aware loading and richer pickup-and-delivery paths still need
  broader domain coverage.
- Workable windows still bound execution start only, except for the occupancy
  semantics on restriction and weather blocks; finishing within the declared
  workable window is not enforced.
- Geometric restrictions do not clip the work area, model partial overlap
  severity, or route around a restricted sub-area.
- Driver time, machine wear, tolls, and richer service costs are still absent
  from cost mode. Additional cost-rate types would extend the existing
  arc-pricing mechanism.
- Work-rate units match by exact unit-code equality (`m3`, `items`); there is
  no controlled unit vocabulary or conversion between compatible units. Rates
  are flat per implement; productivity modifiers such as ground class or
  prime-mover pairing are not modelled.
- Plan output schemas do not yet cover Protobuf/Elasticsearch.

## Observations And Monitoring

- Learning composite weights from prognosis outcomes, the way thresholds are
  auto-tuned, remains open.

## Distributed Operation And Eventual Consistency

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- A serving-side daemon watching source visibility continuously and replanning
  in place remains open.
- Other sources and other domain packs do not emit `ingested-at`, and a series
  with any reading missing it falls back to row order. Event watermarks skip
  `entity.corrected` because its target contract is resolved by key column,
  not declared.
- A daemon-style unbounded consumer would need periodic mid-stream commit
  points per converged batch to bound the redelivery window.

## Corrective Rescheduling

- Per-asset-type tuning and additional tunables such as battery thresholds need
  per-type accuracy splits in the prognosis log.
- No policy currently consumes the lead-time distribution for automatic
  threshold changes. Folding lead-time error into guarded tuning would be the
  next closed-loop step after the reviewed tuned-profile flow.

## Rolling Operations

- Per-pass coverage geometry (spatially explicit progress over the work area)
  remains open.
- Solver attribution is still the routing conflict surface: cluster status,
  objective, LNS delta, time-limit state, and same-cluster unserved tasks.
  OR-Tools routing does not expose LP-style duals or exact shadow prices, so
  exact resource-conflict attribution remains approximate.

## Parameter Tuning And Experiment Tracking

- The artifact registry now surfaces reviewed tuned-overlay selection metadata
  (scope, source snapshot hashes, reviewer) so deployments with several active
  profiles can inspect which overlay a scoped run selects; sharing those overlays
  over non-filesystem storage remains open.
- Direct periodic tuning has no previous revision, so its instability objective
  is normally zero; a rolling replay tuning harness would measure real churn
  over event sequences.
- Holdout validation and per-domain objective weights would further reduce
  tuning overfit.
- Study worker counts are still caller-selected (`--jobs` / `TUNE_N_JOBS`).
  The runtime does not yet choose tuning parallelism from observed CPU/RSS
  pressure per dataset.

## Serving And Integration

- Serving does not yet provide OIDC/JWT validation, per-route authorization,
  token rotation, audit logging, or rate limiting; those still belong at an
  ingress/proxy layer or in a future auth provider.
- Artifact manifests and namespace-versioned cache invalidation now exist
  (`fl_op/provenance/`), and the read-only artifact registry indexes runs and
  caches under the data root. A true object-store backend with cross-writer
  consistency semantics for newly published runs remains open.
- Additional production event clients still need small adapter packages that
  register their factory and opt into deduplication when the source can
  redeliver.

## Multi-Domain

- Profile/policy selection is still a single profile supplied by the caller.
  There is no composite multi-domain profile merger for weather/material/
  monitoring policies.
- A mixed-domain source tree still needs external staging to avoid source-file
  name collisions such as multiple `operators.csv` files.
- There is no plugin discovery, versioned generator packaging, or generator
  capability declaration yet; external packs still need their Python module
  importable in the running environment.
- Generated schema filenames and evolution baseline filenames remain keyed by
  the global registry id, though registry artifacts now expose versioned
  domain-local refs.

## Performance

- File-based feasibility inputs (sources and `schedule.json`) with different
  JSON byte ordering still miss cached feasibility results because file inputs
  are hashed by raw bytes; only the inline order payload is now order-insensitive
  (canonical JSON via the shared `content_hash` primitive). The endpoint also
  still hashes source bytes before it can return a cached response.
- Worker memory feedback does not yet fit separate coefficients by cluster
  size, node count, load dimensions, or domain pack.
- Per-cluster LNS budget learning, for example by operation type, cluster size,
  or penalty distribution, would target the time where it pays off most.
