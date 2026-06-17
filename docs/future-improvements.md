# Future Improvements

This file tracks open future work only. Implemented behavior belongs in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The Ordered Implementation Sequence below is the index of open workstreams. Each
detail section that follows is titled with the sequence number it elaborates, so
every implementation note belongs to exactly one numbered item.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

9. Event visibility completeness. Emit `ingested-at` from every source and
   domain pack (a series missing it on any reading falls back to row order
   today) and extend event watermarks to cover `entity.corrected`.
10. Multi-domain extensibility and packaging. Add plugin discovery, versioned
    generator packaging, and generator capability declaration, and key
    generated schema and evolution-baseline filenames off versioned
    domain-local refs rather than the global registry id.
11. Drone logistics fidelity. Model 3D airspace deconfliction, altitude
    corridor planning, and vehicle-to-vehicle separation, plus charging-station
    scheduling and charging queue capacity.
12. Feasibility input caching. Hash file-based feasibility inputs (sources and
    `schedule.json`) by canonical content rather than raw bytes so
    byte-order-different inputs reuse cached results.
21. Routing topology and geography. Remaining work is research-grade and not a
   blocking engine gap: coupled-insertion search support for fully-optional
   reloads, routing the path around restricted sub-polygons, and a dedicated
   external supplier-location source. The delivered routing behavior (network
   access, reloads, pickup resolution, compartments, pickup-and-delivery,
   work-area clipping) lives in current-implementation.md.
22. Cost model expansion. Remaining work is incremental polish, not a blocking
   engine gap: network-distance and per-link (toll-road) toll pricing, fixed
   per-visit service fees, and per-vehicle/per-operator operating rates. The
   delivered cost model (driver-time, machine-wear, and toll cost-rate types
   priced into arc costs, dispatch margins, and KPIs) lives in
   current-implementation.md.
23. Spatial execution feedback. Remaining work is incremental: measuring
   coverage against the restriction-clipped (workable) remainder and threading
   the residual work-area polygon into the solver's partial-area clip, and
   consuming coverage in periodic (batch) planning. The delivered per-pass
   coverage geometry (swept-path/polygon passes accumulated into spatially
   refined remaining work and a rolling coverage trail) lives in
   current-implementation.md.
24. Closed-loop monitoring policy. Remaining work is research-grade: learning
   the composite health-score weights from prognosis outcomes. The delivered
   closed-loop behaviour (per-asset-type prognosis accuracy splits with per-type
   guarded tuning, lead-time-informed tuning, battery-threshold tunables, and
   mobile-asset monitoring) lives in current-implementation.md.
25. Experiment and tuning maturity. Remaining work is incremental: holdout
   validation and per-domain objective weights, per-cluster LNS budget learning
   by operation type or penalty distribution, and shareable tuned overlays over
   non-filesystem storage. The delivered maturity (perturbed-resolve real
   instability measurement, CPU/RSS-aware tuning parallelism, and a fitted
   worker-memory coefficient model) lives in current-implementation.md.
26. Serving and integration hardening. Remaining work is incremental, not a
   blocking gap: durable cross-instance rate limiting and a shared audit sink at
   the ingress layer, JWT revocation (a `jti` denylist), wiring publishers to
   write newly published runs through the object-store commit marker (and a
   networked object-store client behind the existing protocol), and further
   event-client adapter packages.
27. Solver explanation research. Remaining work is research-grade: exact marginal
   (shadow-price) attribution via a finite-difference resource-relaxation
   re-solve probe or an LP/MIP relaxation that exposes duals, and per-task
   conflict attribution for window/precedence-driven drops. The delivered primal
   resource-conflict attribution (a binding-resource signal from
   routing-dimension utilization) lives in current-implementation.md.



## 9. Event visibility completeness

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

- Other sources and other domain packs do not emit `ingested-at`, and a series
  with any reading missing it falls back to row order. Event watermarks skip
  `entity.corrected` because its target contract is resolved by key column,
  not declared.

## 10. Multi-domain extensibility and packaging

- There is no plugin discovery, versioned generator packaging, or generator
  capability declaration yet; external packs still need their Python module
  importable in the running environment.
- Generated schema filenames and evolution baseline filenames remain keyed by
  the global registry id, though registry artifacts now expose versioned
  domain-local refs.

## 11. Drone logistics fidelity

- No 3D airspace deconfliction, altitude corridor planning, or
  vehicle-to-vehicle separation is modeled.
- Charging-station scheduling and charging queue capacity are not modeled.

## 12. Feasibility input caching

- File-based feasibility inputs (sources and `schedule.json`) with different
  JSON byte ordering still miss cached feasibility results because file inputs
  are hashed by raw bytes; only the inline order payload is now order-insensitive
  (canonical JSON via the shared `content_hash` primitive). The endpoint also
  still hashes source bytes before it can return a cached response.

## 21. Routing topology and geography

- Fully-optional reload insertions (research-grade). One reload per vehicle
  stays mandatory as an anchor. Making all reloads optional was tried and
  reverted — the greedy warm start seeds only one task per implement, so the
  remaining tasks are added by local search, and without a reload already in the
  route cheapest-insertion cannot perform the coupled "insert reload + insert
  task" move within the time limit and drops the task. Removing the anchor needs
  coupled-insertion search support (or a capacity-aware warm start that seeds all
  cluster tasks, not just one per implement).
- External supplier-location source. Pickup locations resolve against every
  known location (sites plus depots/hubs); a ref outside both tables (a true
  external supplier) falls back to the depot with a warning. A dedicated
  supplier-location source in the canonical model is not yet modelled.
- Routing around a restricted sub-polygon (research-grade). Geometric
  restrictions clip the work area by the unrestricted fraction; routing the path
  *around* a restricted sub-polygon (rather than scaling the work area down) is
  not modelled. OR-Tools arcs are point-to-point and do not represent intra-arc
  obstacle detours, so this needs added waypoints or arc-crossing penalties.

## 22. Cost model expansion

Delivered behavior (the `labor`, `machine-wear`, and `toll` cost-rate types
resolved through `solver/cost_rates.py` and priced into arc costs, dispatch
margins, greedy scoring, and KPIs) lives in current-implementation.md. The
residual open work is:

- Tolls are priced per kilometre of geodesic (haversine) arc distance applied
  uniformly to every leg. Two refinements remain open: pricing toll distance
  off network-link distance where a travel link exists (the travel lookup
  carries seconds, not distance, today), and a per-link toll attribute so only
  genuinely tolled road segments are charged rather than a flat fleet-wide
  EUR/km.
- Richer service costs are modelled as driver labour plus machine wear over
  on-task service hours. A fixed per-visit service fee (a per-node cost that
  shifts the serve-vs-drop trade-off independent of service duration) is not
  modelled; OR-Tools routing has no direct per-node fixed serve cost, so it
  would need a drop-penalty offset or an equivalent encoding.
- Labour and machine-wear rates are fleet-level (one resolved rate per run).
  Per-vehicle wear curves and per-operator wage bands would let the objective
  prefer cheaper-to-run machines or operators, but need per-asset cost-rate
  resolution rather than the single fleet rate.

## 23. Spatial execution feedback

Delivered behavior (per-pass coverage geometry parsed and accumulated in
`stream/coverage.py` over the `core/geometry.py` swath/area primitives, refining
remaining work from the overlap-corrected covered area and logging a per-pass
coverage trail with an aggregate rolling summary) lives in
current-implementation.md. The residual open work is:

- Coverage measures the covered geodesic area against the task's gross original
  work area. Measuring it against the restriction-clipped *workable* remainder,
  and threading the residual work-area polygon (site minus restricted minus
  covered) into the solver's partial-area clip so restriction severity is
  computed on the uncovered remainder, needs the task to carry work-area
  geometry rather than only a scalar area.
- Coverage feedback runs in the rolling stream only; periodic (batch) planning
  does not yet consume per-pass coverage geometry.
- Rolling progress explanations are the per-pass trail and its aggregate stats;
  richer spatially-explicit explanations (remaining-geometry rendering,
  per-cluster coverage rollups) remain open.

## 24. Closed-loop monitoring policy

Delivered behavior lives in current-implementation.md: per-asset-type prognosis
accuracy splits in the outcome log with per-type guarded tuning into the
overlay's `assetTypeOverrides`; lead-time-informed tuning (a high service-task
late share loosens the policy); battery-threshold tunables; and mobile-asset
predictive monitoring via the `monitorMobileAssets` policy flag. The residual
open work is:

- Learning the composite health-score weights (`compositeWeightBattery`,
  `compositeWeightHealth`, `compositeWeightService`, `compositeWeightDrift`)
  from prognosis outcomes. Unlike the monotonic thresholds, four normalized
  weights cannot be directed by a scalar false-positive/false-negative rate;
  principled learning needs per-signal subscores logged at derivation time and
  a small fitting step that separates escalated (truly needed) from withdrawn
  (not needed) prognoses, so it stays research-grade rather than a guarded
  bounded step.

## 25. Experiment and tuning maturity

Delivered behavior lives in current-implementation.md: real plan-instability
measurement via a perturbed re-solve (`--measure-instability`, removing the
busiest prime mover and counting avoidable churn x `rolling_change_penalty`);
CPU/RSS-aware tuning parallelism (`--jobs 0`); and a fitted worker-memory
coefficient model (base MB plus MB per routing-model cell) replacing the
hardcoded constants once enough feedback accrues. The residual open work is:

- Holdout validation and per-domain objective weights would further reduce
  tuning overfit (tune on a train split, report the recommended parameters'
  objective on held-out datasets; weight each case's contribution by domain
  rather than task count alone).
- The instability harness perturbs by removing one mover; richer generic
  rolling replay datasets over real event sequences would measure churn over
  many events rather than a single perturbation.
- The worker-memory fit is a single per-cell coefficient; separate coefficients
  by load dimension count or domain pack would refine it further, and
  per-cluster LNS budget learning (by operation type, cluster size, or penalty
  distribution) would target the budget where it pays off most.
- Reviewed tuned overlays (solver-parameter and monitoring-policy) are still
  filesystem-only; sharing them over non-filesystem (object-store) storage
  remains open, tied to the object-store artifact backend (item 6).

## 26. Serving and integration hardening

Delivered behavior lives in current-implementation.md: the serving security
gateway (static-token rotation and OIDC/JWT authentication, per-route scope
authorization, an opt-in in-process rate limiter, and per-request audit
logging), the commit-marker object-store artifact backend with run
materialization, and the Redis Streams event-client adapter. The residual open
work is:

- Auth is per-instance and additive. The in-process rate limiter and the audit
  sink are local to one process, so durable cross-instance rate quotas and a
  shared audit store still belong at an ingress/proxy or external service. The
  static authenticator's accept-set rotation cannot revoke a token before its
  natural expiry, and JWT validation has no `jti` denylist.
- The object-store backend is read-side: it serves commit-marked runs and
  materializes them locally, but the planning/publication pipeline still writes
  runs to the filesystem. The built-in client is the filesystem-backed
  reference; a networked client still has to be added behind the
  `ObjectStoreClient` protocol (no vendor SDK is bundled). Wiring publishers to
  write through `publish_run` (so newly published runs land in the object store
  behind the marker) remains open, as do materialization eviction/TTL and
  large-object streaming.
- More production event clients (NATS, RabbitMQ, a cloud pub/sub, ...) still
  need their own adapter packages following the broker SPI, and poison messages
  are acknowledged-and-skipped rather than routed to a
  dead-letter queue.

## 27. Solver explanation research

Delivered behavior lives in current-implementation.md: per-cluster primal
resource-conflict attribution (`solver/cluster/conflict.py`) that reads the
solved routes' Time/Load dimension utilization and fleet usage and names the
`binding_resource` behind dropped tasks (`capacity:<material>` / `time` /
`fleet` / `other`, or `solve_budget` / `model_infeasible` when no solution is
found), threaded into the per-task attribution maps, the revision-diff
explanation, and a `binding_resources` telemetry tally. The residual open work
is research-grade:

- The attribution is a heuristic over aggregate primal utilization, not an exact
  marginal value. Exact resource-conflict attribution needs either a
  finite-difference probe (re-solve the cluster with one resource marginally
  relaxed -- an added vehicle, an extended horizon, more capacity -- and read the
  served-count/objective delta as the empirical shadow price) or an alternative
  LP/MIP relaxation whose dual values expose shadow prices directly. OR-Tools' CP
  routing exposes neither, so both remain open.
- Drops that no aggregate dimension explains are attributed to `other`; pinning
  the specific binding constraint (a particular time window or precedence edge)
  for an individual dropped task is not modelled.
