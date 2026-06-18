# Future Improvements

This file tracks open future work only. Implemented behavior belongs in
[current-implementation.md](current-implementation.md); historical completion
plans are intentionally not repeated here.

The Ordered Implementation Sequence below is the index of open workstreams. Each
detail section that follows is titled with the sequence number it elaborates, so
every implementation note belongs to exactly one numbered item.

## Ordered Implementation Sequence

Recommended order, optimized for dependency reuse and low rework:

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
28. Event visibility completeness. Remaining work is incremental: future
   event-client adapters (NATS, RabbitMQ, cloud pub/sub) must carry the
   broker-arrival stamping the Kafka and Redis adapters already do, a producer
   on a transport with no broker-arrival metadata still needs to stamp
   `ingested-at` itself, and a new domain pack's observation source must declare
   the `ingestedAt` binding. The delivered visibility (file and in-repo event
   producers emitting a true `ingested-at`, the live Kafka/Redis adapters
   stamping the broker's own arrival time when a producer omits it, purely
   event-fed series ordering by arrival and flagging regressions, and
   `entity.corrected` advancing its contract's watermark) lives in
   current-implementation.md.
29. Multi-domain extensibility and packaging. Remaining work is incremental: key
    generated schema and evolution-baseline filenames off versioned domain-local
    refs rather than the global registry id, and the declared generator/pack
    version is recorded but not yet checked for engine compatibility. The
    delivered extensibility (entry-point domain-pack plugin discovery merged into
    the registry, generator/pack version and a builtin-vs-plugin source in the
    capability metadata) lives in current-implementation.md.
30. Drone logistics fidelity. Remaining work is incremental, not a blocking
    gap: the airspace deconfliction holds now re-time dispatch, so what is left
    is the deeper routing coupling (re-routing flights to avoid conflicts rather
    than only holding them, and a charging-queue-driven reassignment/drop that
    consumes the turnaround-readiness signal) plus richer energy modelling
    (per-charger ratings, partial state-of-charge, opportunity charging, battery
    swap). The delivered behavior (altitude-corridor + deadline-bounded temporal
    (4D) deconfliction with vehicle-to-vehicle separation whose holds are applied
    to dispatch, and per-hub charging-bay queue scheduling with turnaround
    readiness) lives in current-implementation.md.


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

## 28. Event visibility completeness

Effect catalog:
[reference/model-world-divergence.md](reference/model-world-divergence.md).

Delivered behavior lives in current-implementation.md: file and event sources
emit a true `ingested-at` through one shared delivery-delay model
(`data/ingestion.py:stamp_ingested`); the in-repo event producers (the rolling
demo's `events.jsonl` and the drone scenario stream) stamp it on every emitted
event, and at the ingestion boundary the live broker adapters (Kafka and Redis
Streams) stamp the broker's own arrival time -- the Kafka record timestamp, the
Redis entry id's `<millisecondsTime>` -- as `ingested_at` when a producer omits
it (`stream/source.py:stamp_broker_ingested`). So a purely event-fed or
broker-fed observation series orders by arrival and flags arrival-order
regressions instead of looking ordered under the observed-time proxy. A
producer-supplied `ingested_at` always wins, and the observed-time proxy (then
source-row order) stays as the defensive net when neither a producer nor a
broker arrival time is available. The residual open work is:

- Future event-client adapters (NATS, RabbitMQ, a cloud pub/sub) must carry the
  same broker-arrival stamping; the shared `stamp_broker_ingested` primitive is
  ready, but each adapter has to extract its transport's arrival timestamp. A
  producer publishing over a transport with no broker-arrival metadata still has
  to stamp `ingested_at` itself, else the consumer falls back to the proxy.
- A new domain pack's observation source must declare the `ingestedAt` binding;
  the source-row-order fallback remains the defensive net for any source that
  does not.

## 29. Multi-domain extensibility and packaging

Delivered behavior lives in current-implementation.md: external domain packs
self-register through the `fl_op.domain_packs` entry-point group
(`contracts/plugins.py`), discovered and merged into the registry index at load
so a plugin domain is first-class across lookups, capabilities, and
`generate-data` without any in-repo registry.yaml edit; the in-repo registry
wins key conflicts, discovery is defensive and opt-out (`FL_OP_DISABLE_PLUGINS`),
and plugin entries never enter the persisted registry.yaml. Generator capability
metadata now declares a domain/generator `version` and a builtin-vs-plugin
`source` (with the plugin's entry point and distribution). The residual open
work is:

- Generated schema filenames and evolution-baseline filenames remain keyed by
  the global registry id, though registry artifacts already expose versioned
  domain-local refs; rekeying the committed evolution baselines (and the
  canonical `canonical-*` ones) off the domain-local ref is a separate,
  larger change to committed CI artifacts.
- A pack's declared `version` is recorded but not yet checked for engine
  compatibility, and a discovered contribution is shape-coerced rather than
  semantically validated at discovery time (the normal contract-validation path
  still applies once its contracts are loaded).

## 30. Drone logistics fidelity

Delivered behavior lives in current-implementation.md: two post-solve fidelity
passes embedded in `score.drone_logistics_kpis`. 4D airspace deconfliction
(`planning/airspace.py`) reconstructs each aerial flight's lateral path and its
travel-inclusive airborne window from canonical geometry, builds a
lateral-proximity + time conflict graph, greedily colours conflicting flights
into vertically separated altitude corridors (altitude-corridor planning +
vehicle-to-vehicle separation), then resolves the remaining same-corridor
conflicts with a deadline-bounded temporal-separation pass (holding the later
flight until the corridor clears, capped at deadline slack). The resulting holds
are applied to the published dispatch (`apply_airspace_holds` re-times the held
flights' assignment start/finish in both adapters, leaving frozen/pinned work
untouched) and flow into the charging pass's arrival times, so the deconflicted
schedule is dispatched rather than only annotated. Charging-station scheduling
(`planning/charging.py`) replenishes each used asset's spent energy at its home
hub, scheduling sessions into the hub's parallel charging bays (`chargingSlots`,
a generic canonical Location capacity field) sharing its aggregate
`chargingPowerKw`, so sessions queue when every bay is busy and the pass reports
per-hub utilization, queue waits, and each asset's recharge turnaround/readiness
with an at-risk count. The residual open work is:

- The airspace pass resolves conflicts only by altitude corridor and a temporal
  hold; it does not yet re-route a flight (different waypoints/corridor geometry)
  to avoid a conflict, and the temporal-separation list schedule is greedy and
  earlier-flight-first, not a jointly optimal corridor-and-time assignment. The
  charging-turnaround readiness signal is still advisory: a hub whose queue
  cannot keep its fleet charged does not yet drive reassignment to a freer hub or
  defer/drop dispatch (the queue-aware re-solve coupling remains open).
- The airborne window spans inbound transit plus service over the straight
  hub->pickup->drop polyline; exact per-leg airborne intervals
  (climb/cruise/descent profiles, pickup dwell) and flown corridor geometry are
  not modelled.
- Charging energy is a post-solve estimate (consumption rate x on-plan busy
  hours, capped at battery capacity). Per-bay power is the hub aggregate split
  evenly across bays (not per-charger ratings), and partial state-of-charge,
  opportunity charging between deliveries, and battery-swap modelling are not
  represented.
