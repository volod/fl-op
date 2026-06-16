# Current Implementation

How the system works today. For the contract layer see
[canonical-model.md](reference/canonical-model.md) and
[domain-mapping.md](reference/domain-mapping.md); for the entity ontology, use
cases, and algorithm overview see
[optimization-ontology.md](reference/optimization-ontology.md); for why and how
the system survives the gap between its entity model and the physical world see
[model-world-divergence.md](reference/model-world-divergence.md).

## Three layers

1. **Canonical optimization model** (`contracts/canonical/`) - the domain-neutral
   entity / capability / semantic-term contract the engine consumes.
2. **Domain mapping packs** (`contracts/domains/<domain>/`) - a pure physical ODCS
   schema, separate `*.mapping.yaml` projections onto the canonical model, and an
   optimization profile. Physical schemas may carry extra real-data fields beyond
   what the optimizer needs; those are retained for analysis and ignored by the
   engine.
3. **Engine** (`src/fl_op/{snapshot,solver,adapters}`) - consumes canonical
   entities only; no dependency on any domain model layer.

Four domain packs exist today and are runnable end to end with registered
contracts, data generators, and profiles: drone logistics, agricultural custom
services, construction earthworks, and roadside infrastructure. Drone logistics
is the default domain. It models autonomous last-mile delivery for
manufacturers, restaurants, and online stores with mixed uncrewed ground
vehicles (`UGV`) and uncrewed aerial vehicles (`UAV`), payload modules,
operators, logistics hubs, delivery points, road/air travel links, weather,
restricted zones, explicit battery kWh capacity/use, electricity cost-rate
rows, and compatibility fuel-equivalent fields for older integrations. Drone
datasets also write `drone-scenarios.json` and `scenario-events.jsonl`; drone
scenarios cover heavy manufacturer deliveries, urgent restaurant meals,
ordinary online-store parcels, bad-weather periods, no-fly activation,
road-only destinations, UAV speed wins, UGV feasibility wins, hub energy
scarcity, and asset outage events. Drone plans include
`score.drone_logistics_kpis`: fill rate, on-time rate, delivery margin, mode
split, UGV/UAV utilization, support-team utilization, unassigned reasons,
energy or fuel-equivalent usage, rolling churn, weather-blocked UAV tasks,
and no-fly exclusions. Checked-in drone tuning defaults live in
`contracts/domains/drone_logistics/tuning.yaml`; they cover UAV weather
thresholds, UGV road-speed buckets, delivery/drop penalties, customer-class
deadline penalties, UGV/UAV fleet mix, payload capacity classes, energy cost
rates, cluster-size limits, LNS budgets, and rolling instability penalties.
Drone rolling replay scenarios exercise `task.started`, `asset.unavailable`,
weather degradation, no-fly activation, hub inventory or energy shortage,
urgent order insertion, and customer cancellation. The roadside pack is
monitoring-driven: service vehicles, service kits, and technicians dispatch
`EQUIPMENT_SERVICE` visits derived from inspection findings about stationary
signage and sensor assets along road segments.
The construction pack is earthworks-native: volume-shaped jobs (excavation,
trenching, hauling) carry m3 quantities and volume-moving attachments declare
m3-per-hour work rates, so durations come from the rate, not an area proxy.
By default one domain is active per run: registry.yaml `activeDomain`,
currently `drone_logistics`, overridable with `ACTIVE_DOMAIN=agricultural`,
`ACTIVE_DOMAIN=construction`, or `ACTIVE_DOMAIN=roadside`.
Shared-fleet runs can select several packs with
`ACTIVE_DOMAINS=agricultural,construction` or by passing adapter config
`domains=[...]`; the snapshot and solver projection then use the union of the
selected domains' canonical bindings. The `generate-data` command's `--domain`
option defaults to the registry active domain and resolves the generator
callable declared by that domain's registry entry. Profile input contract refs
resolve inside the active domain
(`operators` can mean construction operators in the construction profile).
Solver inputs resolve their binding tables by canonical entity and asset role,
never by contract id, so switching domains or unioning selected domains needs
no solver change. Multi-domain policy merging is not automatic: the caller
still supplies one optimization profile.

## Data and contracts

`fl-op generate-data` writes one timestamped dataset under
`$DATA_DIR/generate-data/<timestamp>/` (Avro by default; CSV/Parquet via
`--format`). `metadata.json` records the chosen format and generated domain so
downstream commands use the right codec and, when no domain override is present,
build snapshots with the matching mapping/profile.

Physical schemas (Avro/Protobuf/Elasticsearch/Parquet) are generated from the
physical ODCS contracts into `contracts/generated/` (gitignored). Generated
schemas are structural only - they carry no optimization metadata. The
canonical plan OUTPUT contract generates physical schemas too
(`contracts/plan_schema_gen.py`, Avro and Parquet): nested records named
after the plan.json payload fields, joined from the same binding table the
publication validator uses, so downstream consumers can validate received
plan artifacts without this codebase. The plan output contract governs the
common score metrics, quality-summary fields, and corrective-action records in
addition to the envelope, assignments, unassigned tasks, and material
reservations; domain-specific nested score payloads such as solver attribution
and drone KPIs remain extra artifact data.

`fl-op contracts validate` checks: generated-schema structural fingerprints, the
canonical model, and per-domain **mapping completeness** (every mapping binds only
to declared canonical fields + known terms, and covers every required canonical
binding). The registry also exposes every source projection as a versioned
artifact ref (`domain/local-id@odcs:<version>+mapping:<version>`), validates
that those refs are unique, and still resolves legacy global ids and
domain-local aliases for compatibility. `fl-op contracts validate-domain
--domain <d>` additionally reports each contract's optimization-mapped vs
extra (analytical) physical fields.

`fl-op contracts evolution-check` enforces both structural and semantic
versioning. ODCS field changes keep the existing policy: added optional fields
need at least a minor contract-version bump, while removed fields, type
changes, requiredness changes, and added required fields need a major bump.
Canonical mapping metadata is snapshotted separately in the evolution history:
unit or quantity-kind conversions and enum/list expansions require a minor
mapping-version bump; binding or semantic-term retargeting, removals, enum
contractions, and unknown semantic rewrites require a major mapping-version
bump. Reviewed baselines carry both the normalized semantic metadata and the
registry artifact ref, so metadata edits are classified before the hash gate is
accepted.

### Multi-domain staging and policy composition

A snapshot build can span several domains at once. The registry composes the
selected domains' optimization profiles into one effective profile
(`FileRegistry.composite_profile`): the first domain that declares a profile is
the primary (it supplies identity, scalar defaults, and objective hierarchy) and
each later profile is layered on via `OptimizationProfile.composed_with`. Policy
merges are conservative so adding a domain never silently relaxes another:
weather limits collapse to the stricter (lower) bound and sensitivity maps union
(primary wins on shared operation types); monitoring scalars keep the primary
value while `assetTypeOverrides`/`assetOverrides` maps union (primary wins on key
collisions); constraints union by id with an enforced constraint winning a
conflict. With no profile-bearing domain selected the build falls back to engine
defaults unchanged.

Mixed-domain packs can declare the same `sourceFile` name (for example two
domains both staging `operators.csv`). The snapshot builder stages each domain
under its own subdirectory (`data_dir/<domain>/operators.csv`); the per-domain
file wins when present, otherwise the flat layout is used so single-domain
datasets load unchanged. `SnapshotBuilder.source_collisions` reports any
contracts from different domains still resolving to one physical file (which
would double-count entities) and `missing_source_files` reports declared
datasets absent from the directory. Both surface as warning `QualityFinding`s on
the snapshot (`dq://dataset/source-file-collision`,
`dq://dataset/source-file-missing`) rather than failing silently.

Every generator-bearing domain exposes capability metadata
(`FileRegistry.generator_capabilities`, surfaced by
`data/domain_generators.py` and the `fl-op domain-capabilities` CLI command):
the generator callable, declared profile, the canonical entities the domain's
contracts project, the staged contract ids, and source formats. Derived fields
always reflect the registry, so capabilities cannot drift from the contracts.

## Planning pipeline

1. Validate contracts (`fl-op contracts validate`).
2. Map source rows into canonical assets, locations, tasks, forecasts,
   observations, commitments, travel links, cost rates, and operational
   bundles. Which datasets are mapped is derived from the registry (selected
   domains + mapping entity); domain-local contract aliases are resolved by
   the registry, and entity dispatch is a registered emitter table
   (`mapping/builders.py:ENTITY_EMITTERS`), so new datasets and entities plug
   in without engine changes. Source values are normalized to the canonical
   unit declared in each binding through a controlled unit vocabulary with
   conversions (`mapping/units.py:convert_to_canonical`, e.g. W↔kW, g↔kg,
   mL↔L, m²↔ha), so compatible units are reconciled rather than matched by
   exact unit-code string equality; an undeclared conversion fails loudly
   (`UnitConversionError`).
3. Statistically assess observation series (`snapshot/assessment.py`):
   order each series by observed time (never arrival order), flag
   arrival-order timestamp regressions (arrival order is the explicit
   `ingested-at` timestamps when the whole series carries them -- exact
   across restarts -- with source row order as the legacy fallback),
   exclude readings claiming times beyond
   the clock-skew tolerance ahead of planning time, bound the series by the
   retention window and aggregate over-long histories into time windows
   (endpoints preserved; each window representative carries min/mean/max and
   reading-count aggregates so spikes survive downsampling), exclude
   readings flagged bad by their source and
   outliers (MAD-based modified z-score), floor the confidence of
   fault-suspected series (battery rising without service, frozen non-zero
   values), detect metric drift on non-trending metrics, and aggregate
   per-source error rates into the quality summary. Source quality flags fold
   into per-reading confidence. Per-source watermarks (the newest trusted
   observed time per contract) are stamped onto the snapshot
   (`source_watermarks`). Degraded sources are reported per build and trended
   across runs (`snapshot/quality_trend.py`).
4. Apply the equipment monitoring policy
   (`snapshot/monitoring.py`): assets with low battery, a battery drain trend
   projected below threshold within the forecast horizon, degraded health, an
   overdue service interval, a drifting metric (calibration), or a low composite
   health score (weighted battery/health/service-due/drift signals; the
   weights and headrooms are profile-tunable next to the thresholds) yield
   canonical service tasks anchored at their home location. Stationary equipment
   (sensor stations, fixed road/field equipment) is always covered; mobile
   assets (prime movers, drones) are covered when the effective policy sets
   `monitorMobileAssets` (globally or per asset type), so predictive maintenance
   can extend to the fleet without disturbing domains that only monitor fixed
   equipment. Readings below the policy's minimum confidence are ignored.
   Thresholds and task attributes come from the profile's `monitoring` section,
   with constant-backed defaults, per-asset-type overrides
   (`assetTypeOverrides`), and instance-level overrides by asset id
   (`assetOverrides`, a single critical station) layered on top; the
   guarded auto-tuning overlay (see corrective rescheduling) layers above
   the reviewed profile.
   Observation metric codes are normalized from raw
   source vocabularies via the mapping document's `metricCodes` table.
5. Build an immutable, reproducibly-hashed `PlanningSnapshot` (purely canonical).
6. An adapter projects the snapshot into canonical solver rows
   (`solver/inputs.py`) and runs the OR-Tools solver chain; derived service
   tasks are dispatched alongside ordered work. Projection is demand-driven
   over the selected domain set: each section unions binding tables by
   canonical entity and asset role, skips missing optional values so solver
   row defaults survive, and still emits the same domain-neutral row types.
7. Validate every published plan against the canonical plan output contract
   (`contracts/canonical/odcs/plan.odcs.yaml`, enforced by
   `contracts/plan_contract.py`): a plan whose required bindings do not
   resolve fails publication instead of writing a non-conforming artifact.
8. Synthesize execution events and run rolling-dispatch revisions.

## Solver chain

Shared by batch `solve` and the canonical adapters; it operates on canonical
solver rows (keyed by `asset_id`, `rated_power`, `task_id`, ...):

1. Enforce the profile's weather-window constraint (`solver/enforcement.py`):
   a weather-sensitive task with no compliant forecast window at its nearest
   forecast location is excluded with `NO_VALID_WEATHER_WINDOW`. Sensitivity
   per operation type and limits come from the profile's `weatherPolicy`.
   For the kept sensitive tasks the filter also returns their non-compliant
   forecast windows as blocked intervals, which the routing model keeps
   execution out of (step 8), so weather-sensitive work is scheduled *into*
   its compliant windows, not merely admitted because one exists.
   When `weatherPolicy.requireForecastCoverage` is set the filter switches to
   conservative coverage: every interval inside the task's `[now, deadline]`
   horizon that is not proven safe by a compliant forecast window is blocked
   (not just the explicitly non-compliant windows), and a sensitive task with no
   compliant coverage at all is dropped (`NO_VALID_WEATHER_WINDOW`). This stops
   work from being scheduled into time beyond forecast coverage; the flag ORs on
   composition so any contributing policy that requires coverage wins.
   Structural data semantics are filtered alongside: tasks none of whose
   workable windows can still be met (`CONTRACT_WINDOW_INFEASIBLE`,
   `solver/task_relations.py`), tasks blocked by their location's declared
   restrictions -- prohibited operation types, geometric restricted-area
   intersections, or restriction windows covering every admissible start
   (`RESTRICTED_ZONE`, `solver/restrictions.py`) -- and, transitively,
   dependents of any excluded predecessor
   (`PREDECESSOR_UNSERVED`). Fuel, electricity, material, driver-labour,
   machine-wear, and toll prices are resolved from the snapshot's cost-rate
   entities (`solver/cost_rates.py`), falling back to the engine cost constants
   for unpriced resources (the operating rates -- labour, wear, toll -- fall
   back to zero, so they stay inert unless the data prices them).
   Geometric restrictions are a pre-solve filter: a task's site polygon
   (or centroid when the site has no polygon) is tested against other
   locations whose polygon declares the task's operation as prohibited. A
   partial overlap clips rather than drops: the task is kept with its work area
   (and area-like work quantity) and its revenue scaled to the unrestricted
   fraction of the site polygon (`core/geometry.unrestricted_area_fraction` via
   shapely), so only the genuinely off-limits part of a field is removed and the
   objective credits only the work that can actually be done. The task is
   dropped only when the unrestricted fraction falls below
   `RESTRICTION_MIN_WORKABLE_AREA_FRACTION` (effectively fully covered) or the
   site is a point lying inside a restricted area.
2. Build a prime-mover / related-equipment compatibility matrix from power
   capabilities (`solver/feasibility.py`). Matrices are cached by dataset
   hash (a content hash of the power capabilities and margin), so a repeated
   solve over the same fleet skips the rebuild.
3. Filter candidates per task by operation type. The deterministic
   operation-filtered candidate table is cached under
   `$DATA_DIR/cache/preprocessing/candidate-filter`, keyed by the canonical
   task/fleet rows and the compatibility-matrix digest. Prime movers may also
   declare `compatible-operations`; when present, the pair is feasible only if
   both the prime mover and related equipment support the task operation. This
   is what keeps `UGV_DELIVERY` on UGVs and `UAV_DELIVERY` on UAVs, while
   older domains whose prime movers do not declare operation compatibility keep
   their previous behavior.
4. Cluster tasks by nearest depot; split large groups. Cluster specs are
   cached under `$DATA_DIR/cache/preprocessing/cluster-specs`, keyed by the
   canonical task/site/depot rows, target cluster size, travel lookup, and
   prime-mover operation-compatibility sets.
   Depot affinity uses
   operation-mode network travel times where the travel-link graph connects the pair
   (haversine otherwise), so a field whose road access favors a farther
   depot clusters with that depot. Clustering is chain-aware: tasks linked by
   `depends-on` precedence stay in one cluster so their ordering can be
   enforced in-model. Tasks with the same `alternative_group_ref` also stay
   together; if prime movers declare operation compatibility, single-operation
   units are split by operation and multi-operation alternative units are kept
   standalone so the routing model can choose one mode for that delivery
   without mixing incompatible vehicle classes.
5. Pre-allocate prime movers, related equipment, and operators with a small
   CP-SAT global assignment model (`solver/allocation/global_model.py`): all
   clusters are decided at once, maximizing allocated bundles first and
   breaking ties by the shared greedy score; operators maximize certified
   coverage of cluster operation types with a depot-match tiebreak. The
   count-vs-margin tradeoff is profile-tunable
   (`allocationPolicy.countPriority` through
   `SolverParameters.assignment_count_priority`: 1.0 keeps count-first, 0.0
   maximizes summed scores so a contested resource goes to the
   highest-margin cluster). Allocation is hold-aware and gap-aware: the
   discount on a held asset's candidate scores and operator rewards is its
   largest contiguous free gap in the capacity horizon, not its total free
   time (`solver/allocation/scoring.py:build_free_capacity`), so a fragmented
   calendar with high total free time but no single gap long enough to host a
   contiguous execution window scores lower, and mostly-held resources are
   reserved only when nothing freer qualifies. The penalty-ordered greedy reservation loop remains the
   fallback when the model is disabled (`GLOBAL_ASSIGNMENT_ENABLED=0`),
   oversized, or finds no solution in time.
6. Enforce operator qualification: a task whose operation the cluster
   operator is not certified for is paired with a free qualified backup
   operator (recorded in the cluster's `task_operators` map and carried into
   its dispatch packages); only tasks no qualified operator can take are
   dropped (`NO_AVAILABLE_OPERATOR`). Enforce material availability
   (cumulative per-operation demand from the profile's `materialDemand`
   charged against depot inventory, highest penalty first ->
   `INSUFFICIENT_MATERIAL`). Material charging and reservations are one
   mechanism: every admitted charge becomes a provisional reservation
   record, settled against the final dispatch (confirmed with the scheduled
   window, released when the solve left the task unserved) and published as
   canonical `MaterialReservation` rows on the plan; assignments reference
   their reservation ids. Rolling revisions re-publish the reservations of
   frozen/carried tasks so each revision is self-contained.
7. Build a greedy warm start. In the default `cost` objective, the score is
   gross margin minus repositioning cost. In the opt-in `time` objective, the
   score is estimated arrival plus service duration, inverted so faster
   bundles rank first; the shared penalty-per-day urgency term still helps
   high-penalty work win scarce resources during global pre-allocation.
   Repositioning seconds are the best (smallest) of three vehicle-mode
   estimates: the straight-line hop from the vehicle's current position, the
   network shortest path from its home depot, and the hop onto the nearest
   travel-network node to its current position plus that node's network path to
   the field. The nearest-node mapping (`scikit-learn` haversine `BallTree`
   over located network nodes) generalizes the road access point beyond the
   home depot, so a vehicle working far from its depot joins the network at a
   local node; the pure straight-line estimate is always available as the
   fallback. A UGV uses road-mode links, a UAV uses air-mode links, and legacy
   links without a mode behave as `any`.
8. Solve each cluster as an OR-Tools routing problem in a spawned process
   pool. Auto pool sizing is memory-aware: the worker count is bounded by
   CPUs and by how many estimated worker footprints (base footprint plus the
   largest cluster's routing-model size) fit into available memory; an
   explicit `SOLVER_WORKERS` wins. Completed worker telemetry records
   `worker_max_rss_mb`; `$DATA_DIR/cache/solver-feedback/worker-memory.json`
   retains the max observed RSS as a deployment-specific floor on future
   auto-sizing estimates. Arc travel times come from the travel
   network: the lookup is the all-pairs shortest-path closure over the
   directed travel-link graph (Dijkstra per source, skipped past
   `TRAVEL_NETWORK_MAX_COMPOSE_NODES`) and is indexed by `networkMode`
   (`road`, `air`, or `any`), with a reverse-direction and haversine fallback
   for pairs without any network path (`solver/travel_time.py`). That fallback
   leg duration, like every distance in the engine, routes through the
   centralized `core/geometry.py` module: a `pyproj` geodesic engine configured
   as a sphere of mean Earth radius reproduces the legacy haversine results to
   floating-point noise while serving both scalar and vectorized call sites, the
   geometric fallback speed is `FALLBACK_TRAVEL_SPEED_KMH` (env-configurable),
   nearest-neighbor depot affinity uses a `scikit-learn` haversine `BallTree`,
   and `shapely` point/linestring/bounding-box primitives back future map-based
   interface control. Per-vehicle time matrices keep road and air travel
   isolated. The selected objective is
   `SolverParameters.optimization_objective`, exposed by `plan periodic`,
   `plan rolling`, and `demo` as `--objective cost|time`; `cost` is the
   default. Cost mode prices arcs per vehicle by summing every priced driver of
   the leg in the same objective currency as the drop penalties (1 EUR = 600
   penalty seconds): travel energy cost (consumption rate x the resolved
   resource price), an operating surcharge for driver labour and machine wear
   over travel plus on-task service hours, and a per-kilometre toll over the
   leg distance. The operating and toll rates default to zero, so without
   cost-rate data the arc cost collapses to the energy-only term; when priced,
   they let driver time, wear, and tolls change the choice (an idle-fuel-cheap
   but slow bundle can lose to a faster one once labour is priced). An
   energy-efficient machine still wins time-equal legs, and dropping an order is
   weighed against the money cost of serving it. Time mode prices arcs as
   travel plus service seconds and adds soft cumulative-time costs on task
   nodes, so served tasks are pulled earlier without changing the hard
   deadline/window/drop-disjunction mechanics. Those completion-time costs are
   urgency-scaled per task: the base `TIME_OBJECTIVE_COMPLETION_WEIGHT` is
   stepped up by `priority_class` distance from the baseline class
   (`TIME_OBJECTIVE_CLASS_WEIGHT_STEP`) and by deadline slack tighter than
   `TIME_OBJECTIVE_SLACK_REFERENCE_S` (`TIME_OBJECTIVE_SLACK_WEIGHT_BONUS`),
   all gated by `TIME_OBJECTIVE_URGENCY_CALIBRATION` and env-configurable, so
   higher-class and tighter-deadline tasks finish sooner when the schedule
   forces a choice. Each
   task or pickup node is constrained to routing
   vehicles whose prime mover and related equipment can serve the task's
   operation, preventing an aerial bundle from serving a ground variant or the
   reverse. Task
   starts are constrained into their admissible intervals: workable windows
   minus one shared blocked-interval set (location restriction windows plus
   the task's non-compliant weather windows). Blocked intervals carry
   occupancy semantics: reified constraints require the execution to finish
   by the block start or begin after its end, with the serving vehicle's
   service duration resolved in-model, so a task cannot run into a
   restriction or storm window it started before. Finish-within-window
   enforcement closes the gap left by start-only window pruning: for tasks that
   declare workable windows, a reified constraint requires the whole execution
   interval `[start, start + service]` -- with the per-vehicle service duration
   resolved in-model -- to land inside one declared window, so a task whose
   service cannot fit any single window is dropped rather than started inside a
   window it would overrun. An operator time dimension serializes tasks that
   resolve to the same operator across different routing vehicles in a cluster:
   the visit order already serializes same-vehicle tasks, but a cluster can run
   several (prime, related) pairs in parallel while one certified operator (the
   cluster `operator_ref` or a task's `task_operators` backup) backs more than
   one of them. For each such pair of active tasks a vehicle-aware reified
   no-overlap constraint requires one execution interval `[start, start +
   service]` to finish before the other starts; a dropped task is exempted
   through its active variable, so a shared operator forces parallel pairs into
   series. `depends-on` precedence is
   enforced in-model (a dependent cannot start before its predecessor
   finishes). Service durations are quantity-driven: the generic work
   quantity plus its unit feed the duration estimate (area is the legacy
   alias), and a declared `service-duration` overrides it. A related
   asset's `work-rates` capability (a unit-keyed quantity-per-hour map)
   converts any unit kind (m3, items, ha) into effort directly; area-like
   quantities without a declared rate use the width-times-speed coverage
   model, other units fall back to a nominal effort. The model is built
   over a node table: the depot, a pickup node per paired task, a task node
   per order, and depot reload stops when any task demands a load. The model
   offers each routing vehicle enough reload stops to clear the cluster's
   heaviest single-material demand in successive fills
   (`ceil(total / smallest matching compartment) - 1`, capped by
   `DEPOT_RELOAD_MAX_TRIPS_PER_VEHICLE`); the first stop per vehicle is
   mandatory (it sits at the depot, costs only its handling time, and keeps a
   refill a single cheapest-insertion move) while the additional multi-trip
   stops carry a zero-penalty disjunction, so a route reloads only as often as
   its demand requires. Loads are per-material capacity dimensions: a
   task's `load-material` charges the vehicle's matching compartment
   (`load-capacities`), falling back to the aggregate `load-capacity`
   (vehicles declaring neither are unconstrained). The drone-logistics pack
   exercises this end-to-end: its UAV/UGV contracts declare a
   `load_capacities_kg` compartment map (a parcel bay at full payload plus a
   smaller meal box) bound to `asset.capabilities.loadCapacities`, matching the
   `parcel`/`meal` materials its delivery orders carry. The other packs declare
   no compartment map by design: agricultural orders carry one aggregate
   material per pass, and construction machines and roadside service vehicles
   model no carried load (earthworks move material in-situ; roadside crews are
   gated by operator/kit qualification), so they rely on the aggregate capacity.
   Reload stops reset the
   load dimensions (cvrp-reload slack construction), so demand beyond one
   vehicle fill becomes additional trips instead of dropped tasks
   (`DEPOT_RELOAD_ENABLED=0` restores single-trip semantics). A task
   declaring `pickup-location` becomes a paired pickup-and-delivery: same
   vehicle, pickup before the task, served or dropped together, with the
   load on board only between the pair. The pickup location resolves against
   every known location (sites plus depots/hubs), so a pickup at a hub outside
   the cluster's site table lands at the hub's coordinates; a ref absent from
   both tables logs a warning and falls back to the depot. All four packs
   exercise pickup-and-delivery: drone deliveries pair a hub pickup, the
   agricultural pack collects material at the field's nearest yard, the
   construction pack collects equipment at the nearest yard, and the roadside
   maintenance-jobs projection carries a `pickup_location_ref` (service depot)
   for externally-created tasks. Tasks with the same
   `alternative_group_ref` form a grouped disjunction with max cardinality one:
   at most one UGV/UAV delivery variant is served, and if one variant is served
   sibling failures are suppressed in the published unassigned list. If all
   variants fail, the unassigned record is keyed by the real delivery group.
   With `CLUSTER_LNS_ENABLED=1`,
   clusters whose total lateness penalty reaches
   `CLUSTER_LNS_MIN_PENALTY_EUR_PER_DAY` get a second improvement solve from
   the first solution (guided local search plus path/inactive LNS operators)
   bounded by `CLUSTER_LNS_TIME_LIMIT_S`; once feedback exists, the pool
   stamps each eligible cluster with an `lns_time_limit_s` scaled from
   retained LNS objective deltas
   (`$DATA_DIR/cache/solver-feedback/lns-budget.json`) within configured
   min/max multipliers. The first solution is kept unless strictly improved.
9. Aggregate dispatch packages, canonical reason codes, KPIs (priced with the
   resolved cost rates), and reports. Each dispatch package's energy estimate
   covers the operation plus the inbound travel leg, carries explicit resource
   type and unit fields, reports the per-leg driver labour, machine wear, and
   toll costs (the toll cost and the inbound distance it is priced from are
   non-zero only when a toll rate is supplied, which is what builds the
   geodesic distance matrix), and its `estimated_margin_eur` is the order
   revenue net of energy, material, labour, wear, and tolls at the resolved
   prices (`ResourcePrices`), so per-dispatch margins and KPI aggregates (which
   also surface `total_labor_cost_eur`, `total_machine_wear_cost_eur`,
   `total_toll_cost_eur`, and `total_distance_km`) are priced from the same
   cost-rate data. A task whose predecessor
   went unserved in the solve is withdrawn post-solve
   (`PREDECESSOR_UNSERVED`), so no plan dispatches work whose precondition was
   dropped. Every cluster solve yields a machine-readable telemetry record
   (`solver/solve_telemetry.py`: status, wall time, OR-Tools search status,
   time-limit flag, objective values, LNS budget/delta, worker RSS); batch
   runs write `solve_telemetry.json` and plan scores carry the summary.
   Plan scores also record the selected `optimization_objective` plus
   completion-time KPIs (`total_completion_time_s`,
   `avg_completion_time_s`, `p95_completion_time_s`,
   `max_completion_time_s`) and deadline adherence (`on_time_rate_pct`,
   `n_tasks_with_deadlines`, `n_on_time`, `n_late`). Common scalar/count score
   fields are declared in the canonical plan output contract; richer nested
   score maps remain advisory extension data.
   Adapter-normalized plans also carry per-task attribution maps in
   `plan.score`: assigned tasks record their cluster status/objectives,
   LNS delta, time-limit state, estimated margin, and same-cluster unserved
   conflicts; unassigned tasks record their cluster status and normalized
   infeasibility detail. Rolling revision diffs consume these maps for
   post-hoc explanations.

Enforcement activates only through the adapters (an `EnforcementPolicy` built
from the profile's enforced constraints); the raw batch `solve` pipeline is
unchanged.

The chain's planning time origin is explicit (`run_solver_chain(now=...)`):
cost-rate validity, time-window and restriction filters, routing deadlines,
and held-window offsets all derive from one timestamp. The periodic adapter
passes the snapshot effective time and the rolling compiler the revision
event time, so replayed and synthetic timelines produce exact scheduled
times; wall-clock now is only the fallback for the raw batch pipeline.

## Rolling dispatch

Event application is binding-driven (`stream/apply.py`): the target source
collection and its key column are resolved from the selected domain mapping
documents (canonical entity + identity binding), so the driver knows no
domain-specific column names. Supported triggers:

- `task.started` / `task.progress` / `task.completed`: lifecycle and partial
  completion; progress carries either per-pass coverage geometry (see below), a
  `completed_fraction` (scales every work-quantity column down to the remaining
  share), or an absolute `remaining_quantity` in the task's work unit (exact
  overwrite of the generic work-quantity column, for domains without a
  meaningful fraction); a fully completed task leaves planning, so re-solves
  dispatch only the remaining effort;
- `order.created` / `order.cancelled`;
- `asset.unavailable`: removes any asset by id -- vehicles, implements,
  operators, and stationary equipment share one path;
- `inventory.adjusted`: partial merge into a location row (depot fuel, energy,
  and material balances) without touching its other fields;
- `forecast.updated`: with a payload, upserts the forecast window (weather
  invalidation by data); without one, a pure replan trigger;
- `observation.recorded`: streamed sensor readings upserted by reading id, so
  a re-sent corrected reading replaces the earlier one; readings normalized
  to the canonical `work-progress` metric drive task progress directly from
  telemetry (carrying coverage geometry or a percent value) and complete the
  task at 100 percent;
- `entity.corrected`: a corrected source row upserted by its key column, so
  quality-rejected or wrongly-valued entities re-enter planning.

Per-pass coverage geometry (`stream/coverage.py`, `core/geometry.py`) makes
progress spatially explicit. A `task.progress` event or `work-progress`
telemetry observation may carry the geometry covered in that pass instead of a
scalar: either an explicit `covered_polygon` ([lat, lon] vertices) or a
`covered_path` ([lat, lon] points) swept by a `swath_width_m` implement width
(buffered in a longitude space scaled by `cos(latitude)` so the swath is
metrically round). Passes accumulate per task and union geometrically, so two
passes over the same strip are not double-counted; the overlap-corrected covered
geodesic area over the task's original work area gives the completed share, which
shrinks every work column from its original value (cumulative, never re-shrinking
an already-reduced value). Reaching `COVERAGE_COMPLETE_FRACTION` (default 0.99)
finishes the task. Each pass appends one record (covered/remaining area, covered
fraction, pass count) to `$DATA_DIR/quality/coverage-passes.jsonl`, and
`coverage_stats` aggregates the rolling spatial-progress summary logged after
stream runs.

Event application is idempotent by `event-id`: at-least-once delivery may
replay an event, and a replay mutates nothing and produces no revision.
Broker-backed runs extend this across process restarts with a durable
event-id store (`stream/dedup.py`, an append-only id log under
`$DATA_DIR/stream`, compacted in place): each published revision's applied
event ids are recorded after publication and ids published by earlier runs
are suppressed on redelivery. The JSONL development source replays event
files intentionally and never uses the store.
Events whose observed times fall within the convergence window
(`STREAM_CONVERGENCE_WINDOW_S`, default off) coalesce into one rebuild and one
revision, so a partition flushing its backlog converges before replanning.

Reuses the solver chain on a filtered canonical payload:

- Started tasks and tasks inside the freeze window are frozen.
- Assignments whose task and assets still exist are carried forward unchanged.
- Tasks affected by new or unavailable assets are re-solved. Every asset held
  by a frozen/carried assignment stays available to the re-solve as a
  resource calendar of busy intervals: prime movers and implements get exact
  in-model gap reuse (the routing model blocks the union of the pair's
  intervals as vehicle breaks, so either is reused only in a real
  non-overlapping gap), while operator calendars feed hold-aware allocation
  scoring. Within a cluster, operators are also time-modelled inside routing: an
  operator shared by tasks on different routing vehicles gets vehicle-aware
  no-overlap constraints so the shared operator's parallel tasks serialize. Held
  assets are
  classified by solver-row section membership, not id prefixes, so the
  mechanism is domain-neutral.
- Each event yields an immutable plan revision with churn and plan-instability
  metrics.
- `fl-op plan diff-revisions` compares consecutive revisions of a rolling run
  and explains why every changed assignment moved (corrective action, trigger,
  freeze, feasibility change, or optimization tradeoff). For plain re-solves it
  prefers the per-task solver attribution carried in plan scores: cluster id,
  routing status/objective, first-solution objective, LNS delta, time-limit
  state, change penalty, and same-cluster conflicts. Reports are written as
  `revision_diff.json`/`.txt` under `.data/revision-diff/<ts>/`.

### Corrective rescheduling

Plans survive being wrong (`adapters/rolling/corrective.py`); every self-repair
is recorded as a `CorrectiveAction` on the revision and counted in its score:

- **Asset loss mid-plan**: a frozen (started) or carried assignment whose asset
  disappeared is released and its task re-solved
  (`reassigned-after-asset-loss`), instead of staying bound to a dead bundle.
- **False positive prognosis**: a derived service task no longer justified by
  newer readings is withdrawn (`service-withdrawn`), recording why it was
  derived (previous revision's monitoring reasons) and the contradicting
  current readings.
- **False negative prognosis**: critical battery or failed health derives an
  escalated service task (top priority, one-day deadline); a previously
  non-escalated assignment is forced out of carry-forward and re-solved
  (`service-escalated`).
- **Prognosis accuracy feedback** (`stream/prognosis.py`): every revision
  appends its service-task outcomes to
  `$DATA_DIR/quality/service-prognosis.jsonl`, with a per-asset-type breakdown
  (`by_asset_type`) so accuracy can be split by station class. Accumulated
  false-positive / false-negative rates above thresholds log monitoring-policy
  tuning recommendations, globally and per asset type. With
  `MONITORING_AUTO_TUNE_ENABLED=1` the loop closes: `snapshot/policy_tuning.py`
  adjusts `batteryForecastHorizonDays`, `compositeHealthThreshold`, and
  `batteryLowThresholdPct` in bounded steps (max relative step, absolute
  clamps). The global rates plus the service-completion lead-time distribution
  (a high share of service tasks finishing after their deadline loosens the
  policy, the same direction as escalations) drive the global step; per-type
  accuracy splits additionally tune each station class into the overlay's
  `assetTypeOverrides`. All steps are written to a tuned-policy overlay under
  `$DATA_DIR/quality` with a JSONL audit trail (one record per scope); the
  reviewed profile document is never modified and deleting the overlay reverts
  to it. Conflicting signals (a tighten and a loosen at once) skip that scope's
  adjustment but still audit.
- **Completion lead-time feedback** (`stream/lead_time.py`): `task.completed`
  events, fully complete `task.progress` events, and complete
  `work-progress` telemetry append one record per finished task to
  `$DATA_DIR/quality/completion-lead-times.jsonl`. Each record measures
  deadline lead and schedule error against the plan the task was executing
  under; distribution stats (including the service-task late share consumed by
  guarded monitoring tuning) are logged after stream runs.

Periodic plans get the same withdrawal/escalation record-keeping: each
periodic run reconciles against its predecessor
(`reconcile_previous_plan`), records the corrective actions on the plan,
persists a `service_reasons.json` artifact for the next run, and appends to
the same prognosis accuracy log.

**Watermark-driven replan triggering**: every published plan carries its
snapshot's `source_watermarks`. `fl-op plan freshness --data <dir> --plan
<dir|latest>` builds a snapshot from the data visible now and compares
(`stream/freshness.py`); with `--replan` a stale plan automatically triggers
a rolling replan. Each check writes a `freshness.json` artifact under
`$DATA_DIR/freshness/<ts>/`.

## Quality and completeness artifacts

- The snapshot carries a compact, exact bundle feasibility summary
  (`snapshot.bundle_summary`): feasible pair counts over the full
  prime-mover x related-equipment cross product, per-operation pair counts,
  and unmatched-resource counts, computed vectorised so the artifact stays
  constant-size at any fleet scale. It also carries the demand side: task
  counts per demanded operation type (including derived service tasks) and
  `scarce_operations`, the demanded operations whose feasible-pair supply is
  below the task count. Concrete bundles are enumerated lazily
  on demand (`snapshot/bundles.py:iter_bundles`), never materialized into
  the snapshot. The solver does its own compatibility filtering, so both are
  explanation artifacts, not assignment inputs.
- A mapped contract whose declared source file is absent from the data
  directory yields a `dq://dataset/source-file-missing` warning finding on the
  snapshot, so an incomplete entity set is visible instead of silent.
- Observation assessment emits `dq://observation/outlier`,
  `dq://observation/sensor-fault`, `dq://observation/metric-drift`,
  `dq://observation/source-flagged`, `dq://observation/future-timestamp`, and
  `dq://observation/timestamp-regression` findings; surviving readings carry a
  confidence and `quality_summary.observation_error_rates` records the share
  of bad readings per source contract.
- `snapshot.source_watermarks` records the newest trusted observed time per
  source contract: what arrived later belongs to the next revision, and
  consumers can tell stale visibility from a quiet world. Observation
  watermarks come from the assessed readings; task/asset/location/forecast
  sources mutated by execution events get theirs from the event applicator
  (the newest applied event's observed time per contract), merged at
  snapshot build with the newest time winning.
- Dataset builds append their error rates to
  `$DATA_DIR/quality/observation-error-rates.jsonl`; a source whose rate
  strictly increases over the last recorded runs is reported as degrading.
  The trend file itself is retained: past QUALITY_TREND_MAX_RECORDS records
  it is compacted in place to the newest records (atomic replace).

## Parameter tuning and experiment tracking

- `fl-op tune` (`tuning/optuna_tuner.py`) runs a seeded Optuna TPE study over
  the tunable solver parameters (`solver/parameters.py:SolverParameters`:
  cluster target size, greedy score weights, per-cluster time limit, LNS
  budget, and rolling change penalty) against recorded KPI baselines built at
  the trial-scale time budget. Additional datasets (`--extra-data`) are scored
  with workload weights derived from task counts, and, by default, the study
  records a multi-objective frontier: maximize business objective (margin minus
  unassigned penalty exposure), minimize plan-instability penalty, and
  minimize wall time. Parallel workers (`--jobs` or TUNE_N_JOBS) use Optuna
  RDB storage; without an explicit URI, `n_jobs > 1` creates
  `study.db` in the tuning run directory. Artifacts: `baseline.json`,
  `trials.json`, `best_params.json` under `$DATA_DIR/tune/<ts>/`, including
  per-dataset case scores, workload-weight contributions, and the Pareto
  frontier.
- `fl-op tune-promote --best-params <run>/best_params.json`
  (`tuning/solver_profile.py`) writes the reviewed tuned solver profile
  overlay. Without scope flags it writes the legacy shared artifact
  `$DATA_DIR/tune/solver-parameters-tuned.json`. With `--domain`, `--profile`,
  and `--adapter-version`, it writes a scoped artifact under
  `$DATA_DIR/tune/<domain>/<profile>/<adapter-version>/solver-parameters-tuned.json`
  and records optional `--expires-at` metadata. Periodic and rolling adapters
  layer matching scoped artifacts onto the active profile's allocation policy
  when no explicit `SolverParameters` were passed. Drone logistics reads only
  its checked-in tuning file and matching scoped overlays, so the shared legacy
  overlay does not silently alter drone behavior.
- Opt-in MLflow logging (`tuning/mlflow_logger.py`, MLFLOW_LOGGING_ENABLED):
  tuning trials, the baseline, periodic plans, and the final revision of
  each rolling run are logged with KPIs, version dimensions, and the
  solve-telemetry summary; local SQLite store under `$DATA_DIR/mlruns` by
  default, MLFLOW_TRACKING_URI for a real server. Best-effort only: a
  tracking failure degrades to a warning, never a failed run.

## Schema evolution and CI

- Every ODCS contract (registered domain contracts plus the canonical entity
  and plan contracts) has a committed reviewed snapshot under
  `contracts/evolution/` (`contracts/evolution.py`). New freezes write the
  latest schema at the top level and retain a `history` array, so
  `evolution-check` validates every adjacent reviewed schema migration pair
  plus the current contract. The version-bump policy is unchanged: added optional
  fields require at least a minor bump; removals, type changes, requiredness
  changes, and added required fields require a major bump; any change without a
  bump fails. Registered domain snapshots also carry the reviewed
  `optimizationMetadataHash`; current-vs-latest metadata drift is gated in the
  same review flow as structural schema evolution, while already-reviewed
  historical metadata hashes remain audit records. Flat pre-history baseline
  files remain readable as a one-entry history.
- CI (`.github/workflows/ci.yml`, `make ci`) regenerates all physical
  schemas from ODCS before any validation, then runs the suite validation,
  domain validations, the evolution gate, and the tests.

## Artifact provenance and registry

- `fl_op/provenance/namespace.py` is the single content-hashing primitive for the
  whole codebase. `canonical_json` serializes any payload deterministically
  (sorted keys, compact separators, `str` fallback); `content_hash(namespace,
  payload)` wraps the payload in `{namespace, namespace_version, payload}` before
  hashing so two subsystems never collide. By default the version folded in is the
  global `PROVENANCE_NAMESPACE_VERSION`, so a single bump invalidates every derived
  cache at once. A call site that needs a hash whose stability is decoupled from
  global cache invalidation passes an explicit `version`.
- `snapshot/hashing.py:compute_snapshot_hash` routes through `content_hash` under
  the `"snapshot"` namespace, but pinned to its own `SNAPSHOT_HASH_VERSION` rather
  than the global namespace version. A snapshot hash is a durable identity (tuned
  overlays and manifests cite it as provenance), so a cache-invalidating bump of
  `PROVENANCE_NAMESPACE_VERSION` must never re-identify snapshots or orphan the
  overlays that reference them. `SNAPSHOT_HASH_VERSION` is bumped only when the
  snapshot's canonical content layout itself changes.
- The content-addressed caches were unified onto `content_hash`: compatibility
  matrix keys (`solver/feasibility.py:compat_cache_key`), preprocessing /
  candidate-filter keys (`solver/preprocessing.py:_hash_payload`), and
  `/feasibility` request keys (`solver/query_pipeline.py:
  feasibility_request_cache_key`) all share the versioned primitive. Leaf
  binary digests over raw numpy bytes (`preprocessing._array_digest`) and file
  bytes (`query_pipeline._file_digest`) stay on a bare SHA-256 and are folded
  into the namespaced payload, since binary content has no canonical-JSON form.
- Artifact manifests (`provenance/manifest.py`) are additive provenance
  sidecars. `write_manifest` drops a `manifest.json` next to a run's primary
  artifacts recording the artifact kind, schema versions, generation time,
  derived snapshot hashes, optional tuned-overlay scope, and a SHA-256 of every
  file in the run directory (recursive, manifest excluded). `manifestHash` is a
  `content_hash` over all fields except the volatile `generatedAt`, so two runs
  that produced byte-identical artifacts from the same inputs share a manifest
  hash. Snapshot builds (`planning/snapshots.py`) now emit a manifest beside
  `snapshot.json` with the snapshot hash and planning mode as scope.
  `verify_manifest` re-hashes the on-disk files and reports missing, mismatched,
  or untracked files.
- The artifact registry (`provenance/registry.py`) is a read-only scanner over
  `$DATA_DIR`. It aggregates three views: per-namespace cache provenance (entry
  counts, total bytes, last-modified) for the caches listed in
  `CACHE_PROVENANCE_DIRNAMES`; every manifest sidecar with its declared snapshot
  hashes and scope; and reviewed tuned solver overlays with their selection
  metadata (scope, `source_snapshot_hashes`, reviewer, review time). It never
  mutates artifacts.
- `fl-op artifacts` (`cli/artifacts_commands.py`) exposes the foundation:
  `artifacts registry` logs the aggregated provenance summary and, with
  `--write`, persists the index to
  `$DATA_DIR/registry/artifact-registry.json`; `artifacts verify --run-dir
  <dir>` re-checks a run's files against its manifest and exits non-zero on any
  mismatch.

## Serving

- `fl-op serve` (`serving/api.py`, FastAPI + uvicorn, loopback by default)
  exposes published plan retrieval (`/plans/{periodic|rolling}` listing,
  per-run and `latest` plan documents, rolling revision summaries and
  per-revision plans) and `POST /feasibility`, the query-contract evaluation
  for a new order; the evaluation core (`solver/query_pipeline.py:
  evaluate_query`) is shared with the CLI pipeline. `/health` is public; all
  plan and feasibility routes require `Authorization: Bearer <token>` when
  `SERVE_AUTH_TOKEN` is set, and a non-loopback bind is rejected unless that
  token is configured. The API reads artifacts through
  `serving/artifacts.py`: by default this is `$DATA_DIR`, or
  `SERVE_ARTIFACT_ROOT` for a shared mounted artifact tree. It never mutates
  datasets or plans. Exact feasibility responses are cached under
  `$DATA_DIR/cache/feasibility`, keyed by the source bytes the query reads,
  schedule.json, and the order payload; uncached requests also reuse the
  compat and candidate-filter caches.
- Rolling planning ingests execution events from the source selected by
  EVENT_SOURCE_KIND (`stream/broker.py:open_event_source`): JSONL and Kafka
  are registered built-ins, and integrations can register additional source
  factories with `register_event_source`. Kafka validates messages through the
  same `parse_event` and drains the visible backlog before the run publishes
  revisions. Broker offsets are never auto-committed: the consumer stays open
  after the drain and commits only once the run's revisions are written,
  right after the durable dedup store records the published event ids. Any
  registered source kind can opt into that dedup store. A crash before
  publication replays the backlog; a crash between record and commit
  redelivers events the store suppresses - effectively exactly-once from
  broker to published revision.
- The serving-side watcher (`fl-op plan watch --data <dir>`,
  `planning/plans.py:run_plan_watch`) keeps a single `StreamSession` alive and
  drains bounded event cycles forever instead of draining once and exiting like
  `plan rolling`. The session's `start()` publishes the baseline revision, then
  each cycle opens a fresh bounded event source, applies its backlog, and
  extends the same continuity chain. Offset commits are bounded per cycle:
  after a cycle's revisions are written and its event ids recorded in the
  dedup store, the cycle records-then-commits its source offsets
  (`event_source.commit()`, which commits and closes) so a crash redelivers
  just the in-flight cycle rather than the whole session. An empty cycle idles
  `PLAN_WATCH_POLL_INTERVAL_S` before re-polling so a quiet topic does not
  spin; `--max-cycles` (`PLAN_WATCH_MAX_CYCLES`) bounds the loop for tests and
  graceful shutdown, and `0`/`None` runs unbounded. Revisions
  land under `plan-watch/<timestamp>/` with a rolling `revisions_summary.json`,
  and each cycle that produces revisions logs one MLflow tracking run tagged
  with its `watch_cycle`. The watcher pairs with `plan freshness`
  (see "Watermark-driven replan triggering") for a poll-and-replan loop.
