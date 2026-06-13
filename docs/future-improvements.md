# Future Improvements

This file is the open backlog with explicit `DONE` markers where an item was
implemented but kept here for context. Full implemented design notes belong in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The backlog is arranged in the recommended delivery order: domain-specific
drone logistics tuning first, then shared platform work that reduces rework for
later solver, serving, tuning, and integration improvements.

## Drone Logistics Deep Tuning

- DONE: The `drone_logistics` domain pack is implemented and is the default
  demo.
- DONE: Mixed UGV/UAV last-mile delivery is modeled through canonical task
  alternatives, prime-mover operation compatibility, and road/air mode-aware
  travel.
- DONE: The default demo validates the drone contracts, builds a drone
  snapshot, produces a mixed UGV/UAV periodic plan, and produces rolling
  revisions.

Completed domain tuning and richer operational fidelity:

- DONE: Scenario datasets cover heavy manufacturer deliveries, urgent
  restaurant meals, ordinary online-store parcels, bad-weather periods,
  no-fly-zone activation, road-only destinations, `UAV` speed wins, `UGV`
  feasibility wins, hub energy scarcity, and asset outage events through
  `drone-scenarios.json` and `scenario-events.jsonl`.
- DONE: Track domain KPIs: fill rate, on-time rate, delivery margin, mode
  split, `UGV` utilization, `UAV` utilization, support-team utilization,
  unassigned reasons, energy or fuel-equivalent usage, rolling churn,
  weather-blocked `UAV` tasks, and no-fly exclusion counts through
  `score.drone_logistics_kpis` and rolling revision summaries.
- DONE: Tune domain parameters: `UAV` weather thresholds, `UGV` road-speed
  buckets, delivery drop penalties, deadline penalties by customer class,
  `UGV`/`UAV` fleet ratio, payload capacity classes, energy cost rates,
  cluster size limits, LNS budgets, and rolling instability penalties through
  `contracts/domains/drone_logistics/tuning.yaml`.
- DONE: Add rolling replay workloads for `task.started`, `asset.unavailable`,
  weather degradation, no-fly-zone activation, hub inventory or energy
  shortage, urgent order insertion, and customer cancellation through
  `scenario-events.jsonl`.
- DONE: Store tuned overlays by domain, profile, adapter version, and expiry
  window before using shared tuning storage. Drone logistics tuning uses the
  scoped tuned-overlay path and does not silently change agricultural,
  construction, or roadside behavior.
- DONE: Compare tuned plans against a stable baseline using workload-weighted
  multi-dataset scoring, not only a single smoke dataset.
- DONE: Add a rolling demo test with at least one event that changes selected
  assignments or makes one delivery mode infeasible.

## Drone Logistics V1 Limits

- DONE: Drone battery and charging economics use explicit battery kWh,
  electricity cost-rate rows, resource-aware dispatch energy totals, and
  compatibility fuel-equivalent fields for older integrations.
- DONE: Generalize the unit and cost model for battery, charging, and
  electricity economics in the drone logistics domain.
- OPEN: No 3D airspace deconfliction, altitude corridor planning, or
  vehicle-to-vehicle separation is modeled.
- DONE: No-fly polygons are exclusion constraints.
- OPEN: Routing around restricted sub-polygons remains future work.
- OPEN: Charging-station scheduling and charging queue capacity are not modeled.
- OPEN: Mobile drone predictive maintenance remains future monitoring work unless
  monitoring policy is extended beyond the current stationary-service-task
  behavior.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

1. DONE: Drone logistics deep tuning. Add scenario datasets, domain KPIs, tuned
   overlays, and rolling replay workloads for mixed `UGV`/`UAV` delivery.
2. OPEN: Contract and registry governance. Classify semantic metadata drift into
   explicit versioning rules, schema the plan quality/score/corrective
   sections, and move domain-local ids toward versioned registry artifacts.
3. OPEN: Artifact and provenance foundation. Introduce a shared `snapshot_hash`
   namespace, artifact manifests, cache provenance, and artifact-registry
   selection metadata for tuned overlays.
4. OPEN: Multi-domain staging and policy composition. Add collision-free mixed-domain
   source staging, composite profile/policy merging, and generator capability
   metadata.
5. OPEN: Event visibility and continuous replanning. Extend ingested-at timestamps
   and source watermarks to all mutable sources, add bounded mid-stream offset
   commits, then run freshness/replan logic from a serving-side watcher.
6. OPEN: Temporal solver correctness. Add an operator time dimension, gap-aware
    held-asset scoring, non-overlapping backup-operator sharing, conservative
    unknown-weather handling, and finish-within-window enforcement.
7. OPEN: Unit, material, and resource semantics. Add a controlled unit vocabulary
    with conversions, per-unit-kind material demand, time-windowed material
    reservations/replenishment, and productivity modifiers.
8. OPEN: Routing topology and geography. Map current vehicle positions to network
    nodes, support optional reload and multiple reload trips, resolve supplier
    pickup locations outside the cluster site table, and improve partial
    restricted-area handling.
9. OPEN: Cost model expansion. Price driver time, machine wear, tolls, and other
    arc or service costs after routing topology is expressive enough for those
    rates to change decisions.
10. OPEN: Spatial execution feedback. Capture per-pass coverage geometry and use it
    to refine remaining work, partial-area restrictions, and rolling progress
    explanations.
11. OPEN: Closed-loop monitoring policy. Learn composite health weights from
    prognosis outcomes, consume completion lead-time distributions, and split
    auto-tuning by asset type and additional tunables such as battery
    thresholds.
12. OPEN: Experiment and tuning maturity. Add generic rolling replay datasets
    for real instability measurement, holdout validation, per-domain objective
    weights, CPU/RSS-aware worker selection, cluster-size memory coefficients,
    and per-cluster LNS budget learning.
13. OPEN: Serving and integration hardening. Add OIDC/JWT validation, route-level
    authorization, token rotation, audit/rate-limit hooks, object-store
    artifact backends, and additional durable event clients.
14. OPEN: Solver explanation research. Investigate exact resource-conflict
    attribution through richer solver instrumentation or an alternative model
    that exposes dual/shadow-price signals.

## Solver Quality

- OPEN: Operator time is not modelled in routing: a cluster operator (or per-task
  backup) nominally works all routing vehicles in parallel, and a held
  operator's calendar only discounts allocation scoring, so overlapping
  operator work is discouraged but not prevented. Exact operator gap reuse
  needs an operator time dimension in the routing model.
- OPEN: Hold-aware scoring discounts by the asset's free share of a fixed capacity
  horizon (the rolling dispatch horizon), not by whether the cluster's
  expected execution window actually fits the asset's specific gaps; a
  timing-aware comparison would discount more precisely.
- OPEN: A backup operator is claimed by one cluster for the whole run; sharing an
  idle operator across clusters at non-overlapping times is not modelled.
- OPEN: Weather blocking is forecast-bounded: time not covered by any forecast
  window is optimistically treated as workable, so a deadline beyond forecast
  coverage schedules into unknown weather; a conservative mode (treat
  uncovered time as blocked until a compliant forecast exists) would suit
  risk-averse domains.
- OPEN: Material demand is declared per hectare only (`materialDemand.perAreaHa`),
  so non-area work (`m3`, `items`) never charges material; a per-unit-kind
  demand basis would mirror the work-rate capability surface. Reservations
  also have no time dimension in feasibility: charges are horizon-cumulative,
  not windowed against replenishment.

## Ontology Coverage

- OPEN: Greedy repositioning takes the vehicle's home depot as its road access point
  for network times; a vehicle far from its depot still gets the straight-line
  estimate from its current position. Mapping a vehicle's position to the
  nearest network node would generalize this.
- OPEN: Reload visits are mandatory depot stops, one per routing vehicle, bounding
  each route to one extra trip; truly optional reload nodes need search support
  for coupled insertions (inactive-LNS or guided-local-search budgets), and
  more trips need more stops. Pickup locations resolve against the cluster's
  site table only (no supplier locations outside it).
- DONE: Drone logistics exercises pickup/dropoff-style delivery data.
- OPEN: Compartment-aware loading and richer pickup-and-delivery paths still
  need broader domain coverage.
- OPEN: Workable windows still bound execution *start* only (occupancy semantics
  cover restriction and weather blocks); finishing within the declared workable
  window is not enforced.
- DONE: Geometric restricted areas are implemented as polygon/centroid
  intersection filters.
- OPEN: Geometric restrictions do not clip the work area, model partial
  overlap severity, or route around a restricted sub-area.
- OPEN: The routing objective prices travel energy only; driver time, machine
  wear, and per-kilometre tolls have no cost rates, so a long slow road and a
  short fast one with equal energy cost are objective-equal. Additional cost-rate
  types would extend the same arc-pricing mechanism.
- OPEN: Work-rate units match by exact unit-code equality (`m3`, `items`); there is
  no controlled unit vocabulary or conversion between compatible units (a task
  quantity in `t` never matches a rate declared in `kg`). Rates are flat per
  implement; productivity modifiers such as ground class or prime-mover pairing
  are not modelled.
- DONE: Plan output schemas cover Avro and Parquet contract-declared fields.
- OPEN: Plan output schemas do not yet cover proto/Elasticsearch, and the
  artifact's free-form score, quality-summary, and corrective-action sections
  are not schematized.

## Data Contracts

- DONE: Evolution baselines carry reviewed history and metadata hashes.
- OPEN: Semantic metadata changes are still reviewed as hash drift, not
  classified into semver levels. A richer policy could distinguish unit
  conversion, enum expansion, and binding-retargeting changes with explicit
  bump rules.

## Observations And Monitoring

- DONE: Composite weights are declared statically per profile and per
  asset-type/instance override.
- OPEN: Learning composite weights from prognosis outcomes, the way thresholds
  are auto-tuned, remains open.

## Distributed Operation And Eventual Consistency

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- DONE: The freshness check is pull-based: a scheduler or operator invokes
  `plan freshness --replan`.
- OPEN: A serving-side daemon watching source visibility continuously and
  replanning in place remains open.
- DONE: Ingestion timestamps cover the agricultural sensor-readings source.
- OPEN: Other sources and other domain packs do not emit `ingested-at`, and a
  series with any reading missing it falls back to row order. Event watermarks
  skip `entity.corrected` because its target contract is resolved by key
  column, not declared.
- DONE: Offset commits are per run: the whole drained backlog commits after
  all of the run's revisions are published.
- OPEN: A daemon-style unbounded consumer would need periodic mid-stream commit
  points per converged batch to bound the redelivery window.

## Corrective Rescheduling

- DONE: Auto-tuning adjusts two policy fields globally (forecast horizon and
  composite threshold) from aggregate rates.
- OPEN: Per-asset-type tuning and additional tunables such as battery
  thresholds need per-type accuracy splits in the prognosis log.
- DONE: Completion lead-time feedback is append-only and aggregate-only today:
  the driver logs deadline lead and schedule error, and reports distribution
  statistics.
- OPEN: No policy currently consumes the lead-time distribution for automatic
  threshold changes. Folding lead-time error into guarded tuning would be the
  next closed-loop step after the reviewed tuned-profile flow.

## Rolling Operations

- DONE: Progress payloads and telemetry cover the completed fraction and the
  absolute remaining quantity.
- OPEN: Per-pass coverage geometry (spatially explicit progress over the work
  area) remains open.
- DONE: Revision diffs consume solver attribution from plan scores.
- OPEN: The attribution is still the routing conflict surface (cluster
  status/objective, LNS delta, time-limit state, same-cluster unserved tasks).
  OR-Tools routing does not expose LP-style duals or exact shadow prices, so
  exact resource-conflict attribution remains approximate.

## Parameter Tuning And Experiment Tracking

- DONE: The tuned solver overlay is one reviewed operational artifact under
  `DATA_DIR/tune`.
- DONE: Tuned overlays can be keyed by optimization profile id, domain,
  adapter version, and expiry window.
- OPEN: Deployments with several active profiles still need artifact-registry
  selection metadata before sharing non-filesystem storage.
- DONE: The multi-objective instability value is read from solver/plan KPIs
  when available.
- OPEN: Direct periodic tuning has no previous revision, so its instability
  objective is normally zero; a rolling replay tuning harness would measure
  real churn over event sequences.
- DONE: Cross-dataset tuning uses workload-size weighting so a tiny smoke
  dataset no longer influences the result as much as a production order book.
- OPEN: Holdout validation and per-domain objective weights would further
  reduce tuning overfit.
- DONE: Parallel tuning coordinates through Optuna RDB storage.
- OPEN: Study worker counts are still caller-selected (`--jobs` /
  `TUNE_N_JOBS`). The runtime does not yet choose tuning parallelism from
  observed CPU/RSS pressure per dataset.

## Serving And Integration

- DONE: Serving authentication is a deployment-shared static bearer token.
- OPEN: Serving does not yet provide OIDC/JWT validation, per-route
  authorization, token rotation, audit logging, or rate limiting; those still
  belong at an ingress/proxy layer or in a future auth provider.
- DONE: Shared serving storage is filesystem-backed (`SERVE_ARTIFACT_ROOT` or
  `$DATA_DIR`).
- OPEN: A true object-store or artifact-registry backend would need consistency
  semantics, artifact manifests, and cache invalidation for newly published
  runs.
- DONE: Event-source extension is a registry seam, and Kafka is the shipped
  durable broker client.
- OPEN: Additional production clients still need small adapter packages that
  register their factory and opt into deduplication when the source can
  redeliver.

## Multi-Domain

- DONE: Shared-fleet planning can union selected domain bindings
  (`ACTIVE_DOMAINS` or adapter `domains`).
- OPEN: Profile/policy selection is still a single profile supplied by the
  caller. There is no composite multi-domain profile merger for
  weather/material/monitoring policies.
- DONE: `generate-data --domain` generates one domain pack per invocation.
- OPEN: A mixed-domain source tree still needs external staging to avoid
  source-file name collisions such as multiple `operators.csv` files.
- DONE: Generator registration is a Python callable path in the local
  registry.
- OPEN: There is no plugin discovery, versioned generator packaging, or
  generator capability declaration yet; external packs still need their Python
  module importable in the running environment.
- DONE: Domain-local contract ids are aliases over the existing flat registry
  keys, not a nested registry-file format.
- OPEN: Generated schema filenames and evolution baseline filenames remain
  keyed by the global registry id.

## Performance

- DONE: Preprocessing cache keys are conservative content hashes and are safe
  across source changes.
- OPEN: Cache keys do not yet share a single named `snapshot_hash` namespace
  with all plan artifacts; a future artifact registry could make cache
  provenance easier to inspect and evict.
- DONE: Feasibility request caching is exact-request caching.
- OPEN: Similar orders or equivalent schedules with different JSON ordering do
  not share results, and the endpoint still hashes source bytes before it can
  return a cached response.
- DONE: Worker memory feedback uses the retained max observed RSS as a
  deployment floor.
- OPEN: Worker memory feedback does not yet fit separate coefficients by
  cluster size, node count, load dimensions, or domain pack.
- DONE: LNS feedback applies a global multiplier from historical objective
  deltas.
- OPEN: Per-cluster budget learning, for example by operation type, cluster
  size, or penalty distribution, would target the time where it pays off most.
