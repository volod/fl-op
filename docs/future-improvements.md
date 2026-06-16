# Future Improvements

This file tracks open future work only. Implemented behavior belongs in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The Ordered Implementation Sequence below is the index of open workstreams. Each
detail section that follows is titled with the sequence number it elaborates, so
every implementation note belongs to exactly one numbered item.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

5. Experiment and tuning maturity. Add generic rolling replay datasets for
   real instability measurement, holdout validation, per-domain objective
   weights, CPU/RSS-aware worker selection, cluster-size memory coefficients,
   per-cluster LNS budget learning, and shareable tuned overlays over
   non-filesystem storage.
6. Serving and integration hardening. Add OIDC/JWT validation, route-level
   authorization, token rotation, audit/rate-limit hooks, object-store
   artifact backends, and additional durable event clients.
7. Solver explanation research. Investigate exact resource-conflict
   attribution through richer solver instrumentation or an alternative model
   that exposes dual/shadow-price signals.
8. Solver model fidelity. Block a held operator's busy calendar as in-model
   breaks across clusters (as prime movers and implements already are), and
   thread per-vehicle travel speed and travel mode into the fallback
   travel-time estimate so `--objective time` can prefer genuinely faster
   movers.
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


## 5. Experiment and tuning maturity

- Direct periodic tuning has no previous revision, so its instability objective
  is normally zero; a rolling replay tuning harness would measure real churn
  over event sequences.
- Holdout validation and per-domain objective weights would further reduce
  tuning overfit.
- Study worker counts are still caller-selected (`--jobs` / `TUNE_N_JOBS`).
  The runtime does not yet choose tuning parallelism from observed CPU/RSS
  pressure per dataset.
- Worker memory feedback does not yet fit separate coefficients by cluster
  size, node count, load dimensions, or domain pack.
- Per-cluster LNS budget learning, for example by operation type, cluster size,
  or penalty distribution, would target the time where it pays off most.
- The artifact registry now surfaces reviewed tuned-overlay selection metadata
  (scope, source snapshot hashes, reviewer) so deployments with several active
  profiles can inspect which overlay a scoped run selects; sharing those overlays
  over non-filesystem storage remains open.

## 6. Serving and integration hardening

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

## 7. Solver explanation research

- Solver attribution is still the routing conflict surface: cluster status,
  objective, LNS delta, time-limit state, and same-cluster unserved tasks.
  OR-Tools routing does not expose LP-style duals or exact shadow prices, so
  exact resource-conflict attribution remains approximate.

## 8. Solver model fidelity

- Operator time is now modelled inside routing for operators shared across
  vehicles within a single cluster (vehicle-aware no-overlap reified
  constraints). Still open: a held operator's busy calendar is not blocked as
  in-model breaks the way prime movers and implements are, so cross-cluster
  operator gap reuse still relies on allocation scoring rather than exact
  in-model time blocking.
- Prime-mover travel speed does not affect routing travel time. When no network
  lookup is available, `travel_time.py` derives leg duration from the centralized
  `core/geometry.travel_time_seconds` helper at the fixed `FALLBACK_TRAVEL_SPEED_KMH`
  fallback speed, so travel time is geometry-fixed and identical across vehicles
  regardless of `PrimeMoverRow.travel_speed`. The only per-vehicle
  completion-time differentiator today is service time, driven by implement
  `working_width` and `max_speed`. Investigate threading per-vehicle travel speed
  (and travel mode) into the fallback travel-time estimate so `--objective time`
  can prefer genuinely faster movers, not just faster implements.

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
