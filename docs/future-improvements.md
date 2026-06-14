# Future Improvements

This file tracks open future work only. Implemented behavior belongs in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The backlog is arranged in the recommended delivery order: governance and
artifact foundations first, then planner semantics, domain fidelity, tuning,
serving, and integration hardening.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

1. DONE - Multi-domain staging and policy composition. Added collision-free
   mixed-domain source staging, composite profile/policy merging, and
   generator capability metadata. See current-implementation.md.
2. DONE - Event visibility and continuous replanning. Ingested-at timestamps
   and per-source watermarks cover all mutable sources, bounded mid-stream
   broker offset commits land per published revision, and a serving-side
   watcher (`plan watch`) drains bounded cycles forever while `plan freshness`
   compares a plan's visibility horizon against the data visible now. See
   current-implementation.md.
3. DONE - Temporal solver correctness. Conservative unknown-weather handling
   (`requireForecastCoverage` blocks every horizon interval not proven safe by a
   compliant forecast and drops tasks with no coverage), finish-within-window
   enforcement (a task's whole `[start, start + service]` interval, with
   per-vehicle service duration, must land inside one declared workable window),
   gap-aware held-asset scoring (allocation discounts a held asset by its largest
   contiguous free gap, penalizing fragmented calendars), non-overlapping
   backup-operator sharing across clusters (a single idle certified operator
   backs multiple clusters when their demand windows do not overlap), and an
   operator time dimension in the routing model (tasks resolving to the same
   operator across different vehicles in a cluster get vehicle-aware no-overlap
   reified constraints, so a shared operator serializes parallel pairs). See
   current-implementation.md.
4. DONE - Optional time-objective validation. Added an e2e slow/cheap vs
   fast/expensive comparison workload proving `--objective time` favors the
   faster implement and lands an earlier completion without dropping the
   assignment, plus deadline-slack/priority-class urgency calibration for time
   mode (per-task urgency-scaled completion weights, env-configurable). See
   current-implementation.md.
5. DONE - Unit, material, and resource semantics. Add a controlled unit vocabulary
   with conversions, per-unit-kind material demand, time-windowed material
   reservations/replenishment, and productivity modifiers.
6. OPEN - Routing topology and geography. Map current vehicle positions to network
   nodes, support optional reload and multiple reload trips, resolve supplier
   pickup locations outside the cluster site table, and improve partial
   restricted-area handling.
7. OPEN - Cost model expansion. Price driver time, machine wear, tolls, and other arc
   or service costs after routing topology is expressive enough for those
   rates to change decisions.
8. OPEN - Spatial execution feedback. Capture per-pass coverage geometry and use it
    to refine remaining work, partial-area restrictions, and rolling progress
    explanations.
9. OPEN - Closed-loop monitoring policy. Learn composite health weights from
    prognosis outcomes, consume completion lead-time distributions, and split
    auto-tuning by asset type and additional tunables such as battery
    thresholds.
10. OPEN - Experiment and tuning maturity. Add generic rolling replay datasets for
    real instability measurement, holdout validation, per-domain objective
    weights, CPU/RSS-aware worker selection, cluster-size memory coefficients,
    and per-cluster LNS budget learning.
11. OPEN - Serving and integration hardening. Add OIDC/JWT validation, route-level
    authorization, token rotation, audit/rate-limit hooks, object-store
    artifact backends, and additional durable event clients.
12. OPEN - Solver explanation research. Investigate exact resource-conflict
    attribution through richer solver instrumentation or an alternative model
    that exposes dual/shadow-price signals.

## Drone Logistics Remaining Limits

- OPEN - No 3D airspace deconfliction, altitude corridor planning, or
  vehicle-to-vehicle separation is modeled.
- OPEN - Routing around restricted sub-polygons remains future work.
- OPEN - Charging-station scheduling and charging queue capacity are not modeled.
- OPEN - Mobile drone predictive maintenance remains future monitoring work unless
  monitoring policy is extended beyond the current stationary-service-task
  behavior.

## Solver Quality

- OPEN - Operator time is now modelled inside routing for operators shared across
  vehicles within a single cluster (vehicle-aware no-overlap reified
  constraints). Still open: a held operator's busy calendar is not blocked as
  in-model breaks the way prime movers and implements are, so cross-cluster
  operator gap reuse still relies on allocation scoring rather than exact
  in-model time blocking.
- DONE - Material demand is declared per hectare only (`materialDemand.perAreaHa`),
  so non-area work (`m3`, `items`) never charges material. Reservations also
  have no time dimension in feasibility: charges are horizon-cumulative, not
  windowed against replenishment.
- OPEN - Prime-mover travel speed does not affect routing travel time. When no network
  lookup is available, `travel_time.py` derives leg duration from the centralized
  `core/geometry.travel_time_seconds` helper at the fixed `FALLBACK_TRAVEL_SPEED_KMH`
  fallback speed, so travel time is geometry-fixed and identical across vehicles
  regardless of `PrimeMoverRow.travel_speed`. The only per-vehicle
  completion-time differentiator today is service time, driven by implement
  `working_width` and `max_speed`. Investigate threading per-vehicle travel speed
  (and travel mode) into the fallback travel-time estimate so `--objective time`
  can prefer genuinely faster movers, not just faster implements.

## Ontology Coverage

- OPEN - Greedy repositioning takes the vehicle's home depot as its road access point
  for network times; a vehicle far from its depot still gets the straight-line
  estimate from its current position. Mapping a vehicle's position to the
  nearest network node would generalize this.
- OPEN - Reload visits are mandatory depot stops, one per routing vehicle, bounding
  each route to one extra trip. Truly optional reload nodes need search
  support for coupled insertions, and more trips need more stops.
- OPEN - Pickup locations resolve against the cluster's site table only; supplier
  locations outside that table are not yet supported.
- OPEN - Compartment-aware loading and richer pickup-and-delivery paths still need
  broader domain coverage.
- OPEN - Geometric restrictions do not clip the work area, model partial overlap
  severity, or route around a restricted sub-area.
- OPEN - Driver time, machine wear, tolls, and richer service costs are still absent
  from cost mode. Additional cost-rate types would extend the existing
  arc-pricing mechanism.
- DONE - Work-rate units match by exact unit-code equality (`m3`, `items`); there is
  no controlled unit vocabulary or conversion between compatible units. Rates
  are flat per implement; productivity modifiers such as ground class or
  prime-mover pairing are not modelled.
- DONE - Plan output schemas do not yet cover Protobuf/Elasticsearch.

## Observations And Monitoring

- OPEN - Learning composite weights from prognosis outcomes, the way thresholds are
  auto-tuned, remains open.

## Distributed Operation And Eventual Consistency

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- DONE - A serving-side daemon watching source visibility continuously and replanning
  in place remains open.
- OPEN - Other sources and other domain packs do not emit `ingested-at`, and a series
  with any reading missing it falls back to row order. Event watermarks skip
  `entity.corrected` because its target contract is resolved by key column,
  not declared.
- DONE - A daemon-style unbounded consumer would need periodic mid-stream commit
  points per converged batch to bound the redelivery window.

## Corrective Rescheduling

- OPEN - Per-asset-type tuning and additional tunables such as battery thresholds need
  per-type accuracy splits in the prognosis log.
- OPEN - No policy currently consumes the lead-time distribution for automatic
  threshold changes. Folding lead-time error into guarded tuning would be the
  next closed-loop step after the reviewed tuned-profile flow.

## Rolling Operations

- OPEN - Per-pass coverage geometry (spatially explicit progress over the work area)
  remains open.
- OPEN - Solver attribution is still the routing conflict surface: cluster status,
  objective, LNS delta, time-limit state, and same-cluster unserved tasks.
  OR-Tools routing does not expose LP-style duals or exact shadow prices, so
  exact resource-conflict attribution remains approximate.

## Parameter Tuning And Experiment Tracking

- OPEN - The artifact registry now surfaces reviewed tuned-overlay selection metadata
  (scope, source snapshot hashes, reviewer) so deployments with several active
  profiles can inspect which overlay a scoped run selects; sharing those overlays
  over non-filesystem storage remains open.
- OPEN - Direct periodic tuning has no previous revision, so its instability objective
  is normally zero; a rolling replay tuning harness would measure real churn
  over event sequences.
- OPEN - Holdout validation and per-domain objective weights would further reduce
  tuning overfit.
- OPEN - Study worker counts are still caller-selected (`--jobs` / `TUNE_N_JOBS`).
  The runtime does not yet choose tuning parallelism from observed CPU/RSS
  pressure per dataset.

## Serving And Integration

- OPEN - Serving does not yet provide OIDC/JWT validation, per-route authorization,
  token rotation, audit logging, or rate limiting; those still belong at an
  ingress/proxy layer or in a future auth provider.
- OPEN - Artifact manifests and namespace-versioned cache invalidation now exist
  (`fl_op/provenance/`), and the read-only artifact registry indexes runs and
  caches under the data root. A true object-store backend with cross-writer
  consistency semantics for newly published runs remains open.
- OPEN - Additional production event clients still need small adapter packages that
  register their factory and opt into deduplication when the source can
  redeliver.

## Multi-Domain

- DONE - Profile/policy selection is still a single profile supplied by the caller.
  There is no composite multi-domain profile merger for weather/material/
  monitoring policies.
- DONE - A mixed-domain source tree still needs external staging to avoid source-file
  name collisions such as multiple `operators.csv` files.
- OPEN - There is no plugin discovery, versioned generator packaging, or generator
  capability declaration yet; external packs still need their Python module
  importable in the running environment.
- OPEN - Generated schema filenames and evolution baseline filenames remain keyed by
  the global registry id, though registry artifacts now expose versioned
  domain-local refs.

## Performance

- OPEN - File-based feasibility inputs (sources and `schedule.json`) with different
  JSON byte ordering still miss cached feasibility results because file inputs
  are hashed by raw bytes; only the inline order payload is now order-insensitive
  (canonical JSON via the shared `content_hash` primitive). The endpoint also
  still hashes source bytes before it can return a cached response.
- OPEN - Worker memory feedback does not yet fit separate coefficients by cluster
  size, node count, load dimensions, or domain pack.
- OPEN - Per-cluster LNS budget learning, for example by operation type, cluster size,
  or penalty distribution, would target the time where it pays off most.
