# Future Improvements

This file is the open backlog only. Implemented design notes belong in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

## Future Implementation Plan

Recommended order, optimized for dependency reuse and low rework:

1. Contract and registry governance. Classify semantic metadata drift into
   explicit versioning rules, schema the plan quality/score/corrective
   sections, and move domain-local ids toward versioned registry artifacts.
   This gives later solver, tuning, and serving changes a stable review gate.
2. Artifact and provenance foundation. Introduce a shared `snapshot_hash`
   namespace, artifact manifests, cache provenance, and artifact-registry
   selection metadata for tuned overlays. This should precede object-store
   serving and profile/domain-scoped tuning.
3. Multi-domain staging and policy composition. Add collision-free
   mixed-domain source staging, composite profile/policy merging, and
   generator capability metadata. Shared-fleet planning already exists, but
   production multi-domain runs need these operational controls first.
4. Event visibility and continuous replanning. Extend ingested-at timestamps
   and source watermarks to all mutable sources, add bounded mid-stream offset
   commits, then run the existing freshness/replan logic from a serving-side
   watcher.
5. Temporal solver correctness. Add an operator time dimension, gap-aware
   held-asset scoring, non-overlapping backup-operator sharing, conservative
   unknown-weather handling, and finish-within-window enforcement. These
   changes all depend on the same interval model and should be designed
   together.
6. Unit, material, and resource semantics. Add a controlled unit vocabulary
   with conversions, per-unit-kind material demand, time-windowed material
   reservations/replenishment, and productivity modifiers. This normalizes
   resource accounting before richer routing/load features consume it.
7. Routing topology and geography. Map current vehicle positions to network
   nodes, support optional reload and multiple reload trips, resolve supplier
   pickup locations outside the cluster site table, generate domain pack
   load/pickup data, and upgrade restricted polygons from exclusion filters
   to clipped or severity-weighted work areas.
8. Cost model expansion. Price driver time, machine wear, tolls, and other
   arc or service costs after the routing topology is expressive enough for
   those rates to change decisions.
9. Spatial execution feedback. Capture per-pass coverage geometry and use it
   to refine remaining work, partial-area restrictions, and rolling progress
   explanations.
10. Closed-loop monitoring policy. Learn composite health weights from
    prognosis outcomes, consume completion lead-time distributions, and split
    auto-tuning by asset type and additional tunables such as battery
    thresholds.
11. Experiment and tuning maturity. Add rolling replay datasets for real
    instability measurement, holdout/workload-weighted multi-dataset scoring,
    profile/domain/version-scoped tuned overlays, CPU/RSS-aware worker
    selection, cluster-size memory coefficients, and per-cluster LNS budget
    learning.
12. Serving and integration hardening. Add OIDC/JWT validation, route-level
    authorization, token rotation, audit/rate-limit hooks, object-store
    artifact backends, and additional durable event clients once the artifact
    and event foundations are stable.
13. Solver explanation research. Investigate exact resource-conflict
    attribution through richer solver instrumentation or an alternative model
    that exposes dual/shadow-price signals; treat this as research work after
    the core temporal and resource model is stable.

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
  workable window is not enforced. Geometric restricted areas are implemented
  as polygon/centroid intersection filters; they do not clip the work area,
  model partial overlap severity, or route around a restricted sub-area.
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

- Evolution baselines now carry reviewed history and metadata hashes, but
  semantic metadata changes are still reviewed as hash drift, not classified
  into semver levels. A richer policy could distinguish unit conversion,
  enum expansion, and binding-retargeting changes with explicit bump rules.

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
- Revision diffs consume solver attribution from plan scores, but the
  attribution is the routing conflict surface (cluster status/objective,
  LNS delta, time-limit state, same-cluster unserved tasks). OR-Tools routing
  does not expose LP-style duals or exact shadow prices, so "which resource
  was taken by which task" remains approximate.

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
- Parallel tuning coordinates through Optuna RDB storage, but study worker
  counts are still caller-selected (`--jobs` / TUNE_N_JOBS). The runtime does
  not yet choose tuning parallelism from observed CPU/RSS pressure per dataset.

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

- Shared-fleet planning can union selected domain bindings
  (`ACTIVE_DOMAINS` or adapter `domains`), but profile/policy selection is
  still a single profile supplied by the caller. There is no composite
  multi-domain profile merger for weather/material/monitoring policies.
- `generate-data --domain` generates one domain pack per invocation; a
  mixed-domain source tree still needs external staging to avoid source-file
  name collisions such as multiple `operators.csv` files.
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
