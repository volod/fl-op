[Implementation guide](../current-implementation.md) > Solver chain

# Solver chain

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
   retains the max observed RSS as a deployment-specific floor and accumulates
   (model-cells, RSS) regression sums. Once enough samples exist, a fitted
   linear memory model (base MB plus MB per routing-model cell) replaces the
   hardcoded base/per-cell constants in the worker-footprint estimate, so
   auto-sizing learns the deployment's real per-cell cost. Arc travel times come from the travel
   network: the lookup is the all-pairs shortest-path closure over the
   directed travel-link graph (Dijkstra per source, skipped past
   `TRAVEL_NETWORK_MAX_COMPOSE_NODES`) and is indexed by `networkMode`
   (`road`, `air`, or `any`), with a reverse-direction and haversine fallback
   for pairs without any network path (`solver/travel_time.py`). That fallback
   leg duration, like every distance in the engine, routes through the
   centralized `core/geometry.py` module: a `pyproj` geodesic engine configured
   as a sphere of mean Earth radius reproduces the legacy haversine results to
   floating-point noise while serving both scalar and vectorized call sites, the
   geometric fallback speed is each prime mover's declared `travel_speed`
   (defaulting to `FALLBACK_TRAVEL_SPEED_KMH`, env-configurable) so a genuinely
   faster mover gets shorter no-network legs and `--objective time` can prefer it
   -- network legs keep their declared, vehicle-independent times, so per-vehicle
   speed differentiates exactly where the engine has no measured time to defer
   to. Nearest-neighbor depot affinity uses a `scikit-learn` haversine `BallTree`,
   and `shapely` point/linestring/bounding-box primitives back future map-based
   interface control. Per-vehicle time matrices keep road and air travel
   isolated and price each mover's fallback legs at its own speed. The selected objective is
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
   series. A *held* operator (carried/frozen on another assignment of a rolling
   plan) also blocks its own tasks in-model: each of that operator's task
   intervals must avoid the operator's busy windows, so a held operator is reused
   only in a genuine gap -- the exact in-model time blocking prime movers and
   implements already get as vehicle breaks (`SetBreakIntervalsOfVehicle`),
   rather than the hold-aware allocation scoring alone it relied on before. (Two
   clusters in the *same* solve contending for one operator are still scored, not
   time-modelled, since clusters solve independently.) `depends-on` precedence is
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
   time-limit flag, objective values, LNS budget/delta, worker RSS, and the
   resource-conflict signal below); batch runs write `solve_telemetry.json` and
   plan scores carry the summary (including a `binding_resources` tally over the
   clusters that dropped tasks).
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
   Each cluster also carries a primal resource-conflict attribution
   (`solver/cluster/conflict.py`): OR-Tools' CP routing exposes no LP duals or
   shadow prices, so instead of a marginal value the solve reads how hard each
   routing dimension is pushed on the solved routes -- the Time dimension's
   route-end cumulative over the horizon, each Load dimension's peak fill, and
   the share of the fleet used -- and names the `binding_resource` behind any
   dropped tasks by a fixed priority: `capacity:<material>` (a load dimension at
   or above `RESOURCE_CONFLICT_TIGHT_UTILIZATION`), then `time` (routes at the
   horizon), then `fleet` (every vehicle committed, no per-route dimension
   tight), else `other` (a spare vehicle remains, so the drop is a
   window/cost trade-off). A cluster with no solution attributes to
   `solve_budget` (timed out) or `model_infeasible`. Capacity is ranked above the
   always-saturated single-vehicle fleet count so the real physical limit is not
   masked. The signal flows into the per-task attribution maps and the
   revision-diff explanation. It is a heuristic over the primal solution, not an
   exact dual; exact marginal attribution remains future research.

Enforcement activates only through the adapters (an `EnforcementPolicy` built
from the profile's enforced constraints); the raw batch `solve` pipeline is
unchanged.

The chain's planning time origin is explicit (`run_solver_chain(now=...)`):
cost-rate validity, time-window and restriction filters, routing deadlines,
and held-window offsets all derive from one timestamp. The periodic adapter
passes the snapshot effective time and the rolling compiler the revision
event time, so replayed and synthetic timelines produce exact scheduled
times; wall-clock now is only the fallback for the raw batch pipeline.
</content>
