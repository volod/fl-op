# Optimization ontology

The canonical model (`contracts/canonical/`) is a domain-neutral ontology for
resource-operations optimization: which resources exist, what work is demanded,
where it happens, under which obligations and environmental conditions, and
what is currently being observed in the field. Every domain pack (agricultural,
construction, ...) projects its physical vocabulary onto this one ontology, and
the engine reasons only in ontology terms.

This page describes the ontology and the optimization use cases it covers. For
contract mechanics see [canonical-model.md](canonical-model.md); for projecting
a domain onto the ontology see [domain-mapping.md](domain-mapping.md).

## Entity ontology

```
                     anchored at / serviced at
        +-----------------------------------------+
        |                                         v
   +---------+   capability / availability   +----------+
   |  asset  |   state (maintenance)         | location |---- inventory.*
   +---------+                               +----------+
        ^  ^                                      ^  ^
        |  | about                       performed|  | for
        |  |                                  at  |  |
        |  +-----------------+                    |  |
        |                    |                    |  |
   replanning           +-------------+      +--------+   +----------+
   trigger              | observation |      |  task  |   | forecast |
        |               +-------------+      +--------+   +----------+
        |                                        ^
   +-----------------+        constrains         |
   | execution-event |                    +------------+
   +-----------------+                    | commitment |
                                          +------------+

   Output: Plan -> Assignment(asset bundle, task, schedule) + UnassignedTask(reason)
```

| Entity | Ontological role | Contract |
|---|---|---|
| `asset` | Anything that participates in executing work: prime movers, related equipment, operators, and stationary equipment. One entity, distinguished by `roles` and `mobility`. Static abilities are `capabilities`, working hours are `availability`, maintenance master data is `state.*`. | `odcs/asset.odcs.yaml` |
| `location` | A place work happens or resources anchor: work sites, depots, loading stations. Carries geometry (point, polygon, area), ground classification, and material inventory positions. | `odcs/location.odcs.yaml` |
| `task` | A demanded unit of work: operation type, location, work quantity, deadline, priority, revenue, lateness penalty, lifecycle status. | `odcs/task.odcs.yaml` |
| `commitment` | A contractual obligation attached to work: deadline, penalty, hardness, validity window. Domains embedding these in order rows map them on `task` instead. | `odcs/commitment.odcs.yaml` |
| `forecast` | A predicted environmental condition for a location and time interval (wind, precipitation, soil moisture). | `odcs/forecast.odcs.yaml` |
| `observation` | A measured value about an entity at a point in time: sensor reading, telemetry sample, inspection result. One shape serves historical batches and realtime streams. | `odcs/observation.odcs.yaml` |
| `execution-event` | The dynamics envelope: a typed trigger (`task.started`, `task.progress`, `task.completed`, `order.created`, `order.cancelled`, `asset.unavailable`, `inventory.adjusted`, `forecast.updated`, `observation.recorded`, `entity.corrected`) that mutates state and forces a rolling re-solve. | `odcs/execution-event.odcs.yaml` |
| `travel-link` | One directed travel-network edge between two locations with a measured travel time (distance-matrix entry / road-graph arc). The network may be sparse: pairs without a link fall back to haversine distance and asset travel speed. | `odcs/travel-link.odcs.yaml` |
| `cost-rate` | A priced resource rate (fuel, consumable material) with an optional validity window. Engine cost constants are the fallback for unpriced resources. | `odcs/cost-rate.odcs.yaml` |
| `plan` | The canonical OUTPUT contract: the plan/revision envelope plus assignment, unassigned-task, and material-reservation records. Produced by adapters and validated on publication (`contracts/plan_contract.py`), never consumed as snapshot input. | `odcs/plan.odcs.yaml` |

Outputs (`Plan`, `Assignment`, `UnassignedTask`, `MaterialReservation`) are
canonical Python entities (`src/fl_op/canonical/plan.py`) produced by adapters
and governed by the `plan` output contract above, mirroring the input entity
contracts.

## Semantic-term vocabulary

Terms are grouped by namespace in `contracts/canonical/model.yaml`; each fixes
value type, quantity kind, and canonical unit so values from any domain land on
one comparable scale.

| Namespace | Meaning | Examples |
|---|---|---|
| `urn:xopt:identity:*` | Stable entity identifiers | `asset-id`, `task-id`, `observation-id` |
| `urn:xopt:attribute:*` | Descriptive attributes | `operation-type`, `mobility`, `latitude`, `priority-class` |
| `urn:xopt:capability:*` | Measurable or categorical abilities | `rated-power` (kW), `working-width` (m), `work-rates` (unit -> qty/h), `compatible-operations` |
| `urn:xopt:availability:*` | Working-time windows | `shift-start`, `shift-end` (s) |
| `urn:xopt:maintenance:*` | Maintenance master data | `last-service-at`, `service-interval` (d) |
| `urn:xopt:commitment:*` | Obligations and their economics | `deadline`, `lateness-penalty` (EUR), `hardness` |
| `urn:xopt:relationship:*` | Cross-entity references | `home-depot`, `location`, `contract`, `entity-ref` |
| `urn:xopt:inventory:*` | Material positions at locations | `fuel` (L), `fertilizer` (kg) |
| `urn:xopt:forecast:*` | Predicted environmental values | `wind-speed` (m/s), `precipitation-rate` (mm/h) |
| `urn:xopt:observation:*` | Measured values | `metric`, `value`, `state`, `unit` |
| `urn:xopt:time:*` | Timestamps and intervals | `observed-at`, `forecast-from`, `valid-to` |
| `urn:xopt:travel:*` | Travel-network edge measures | `travel-time` (s), `distance` (km) |
| `urn:xopt:cost:*` | Priced resource rates | `rate-type`, `unit-price` (EUR), `per-unit` |
| `urn:xopt:restriction:*` | Location restrictions | `prohibited-operations`, `restricted-windows` |
| `urn:xopt:plan:*` | Plan output qualifiers | `planning-mode`, `status`, `reason-code` |

Observation `metric` values are canonical metric codes the engine interprets
(`battery-level`, `health-status`). Raw source vocabularies are normalized to
these codes by the mapping document's `metricCodes` table; unmapped metrics
(`soil-moisture`, ...) pass through and are retained but not acted on by the
monitoring policy.

## Optimization use cases covered

| Use case | Ontology elements used | Status |
|---|---|---|
| Heterogeneous fleet routing with deadlines (HFVRPTW) | asset capabilities/availability, task deadline + location, location geometry | Implemented (OR-Tools chain) |
| Multi-resource assignment (prime mover + equipment + operator bundles) | asset roles, `compatible-operations`, `rated-power` vs `required-power` | Implemented |
| Profit-maximizing order selection | task `revenue-value`, `lateness-penalty`, `priority-class` | Implemented |
| Rolling / dynamic dispatch with plan stability | execution-event, task status freeze, plan revisions | Implemented |
| Condition-based maintenance of stationary equipment (sensor fields, fixed road/field gear) | asset `mobility` + `state.*`, observation metrics, derived `EQUIPMENT_SERVICE` tasks | Implemented (monitoring policy) |
| Streamed telemetry driving replans | `observation.recorded` events appended to observation sources | Implemented |
| Environment-windowed operations (weather) | forecast values per location/interval, profile `weatherPolicy` sensitivity | Implemented (in-model scheduling into compliant windows; pre-filter excludes tasks with none) |
| Material/inventory feasibility | location `inventory.*`, profile `materialDemand` rates | Implemented (cumulative depot charge, penalty-priority; charges published as plan MaterialReservation records) |
| Operator qualification | `operator-certification` capability vs task operation types | Implemented (cluster operator coverage + per-task backup pairing) |
| Multi-stage work sequences | task `depends-on` relation | Implemented (chain-aware clustering, in-model precedence, cascade exclusion) |
| Multiple workable time windows | task `workable-windows` | Implemented (pre-filter + in-model start intervals) |
| Network-based travel times | travel-link entity | Implemented (shortest-path closure over the link graph; reverse/haversine fallback) |
| Capacity-constrained delivery (CVRP-style) | asset `load-capacity`/`load-capacities` vs task `load-demand`/`load-material` | Implemented (per-material dimensions, depot reload stops for multi-trip) |
| Pickup-and-delivery pairing | task `pickup-location` | Implemented (paired nodes: same vehicle, pickup first, dropped together) |
| Unit-uniform duration estimation | task `work-quantity`/`work-quantity-unit` vs asset `work-rates` | Implemented (rate wins; coverage model is the area fallback) |
| Restricted zones / time-restricted areas | location `prohibited-operations`, `restricted-windows` | Implemented (zone exclusion + occupancy-aware interval blocking) |
| Data-driven cost rates | cost-rate entity | Implemented (price resolution with constant fallback; fuel-priced routing arcs, net dispatch margins) |
| Governed plan outputs | plan output contract | Implemented (publication-time binding validation) |
| Standalone contractual commitments | commitment entity | Declared; engine consumes task-embedded deadline/penalty today |

## Domain coverage

The ontology is domain-agnostic; a domain is just a mapping pack:

| Domain | Pack | What maps onto what |
|---|---|---|
| Agricultural custom services | `contracts/domains/agricultural/` (full: data generator + solver wiring) | vehicles/implements/operators -> asset roles; depots/fields -> location; orders -> task; weather -> forecast; sensors -> stationary asset; sensor readings -> observation; routes -> travel-link; prices -> cost-rate |
| Construction earthworks | `contracts/domains/construction/` (full: data generator + solver wiring; run with `ACTIVE_DOMAIN=construction`) | machines/attachments/operators -> asset roles; yards/sites -> location; jobs -> task |
| Roadside infrastructure | `contracts/domains/roadside/` (validation-level example pack) | signage/sensors -> stationary asset (`mobility: stationary`); road segments -> location with closure-curfew restriction windows; maintenance depots -> location; inspection rounds -> observation (condition ratings normalized to canonical metric codes) |
| Utilities, marine, logistics | not yet authored | same entities; the roadside pack is the template for monitoring-driven domains |

## Known ontology gaps

Deliberately not yet modeled (tracked in
[future-improvements.md](../future-improvements.md)):

- Travel links are consumed as direct pair lookups; multi-hop shortest paths
  over a road graph are not composed, and clustering / greedy repositioning
  stay haversine-based.
- One aggregate load dimension per route; per-material compartments, depot
  reloads (multi-trip), and true pickup-and-delivery pairing are absent.
- Restriction windows block execution *start*, not occupancy, and restricted
  zones are per-location operation lists, not geometric polygons.
- Cost rates feed greedy scoring and KPIs; routing arc costs remain
  time-based and dispatch margins do not yet subtract resolved costs.
- Work-rate capabilities exist only for area-like quantities; non-area work
  units fall back to a nominal effort.

## Algorithms

The solver chain (see [02-solver-pipeline.md](../algorithms/02-solver-pipeline.md)
for the full walkthrough and [01-problem-formulation.md](../algorithms/01-problem-formulation.md)
for the mathematical model):

1. **Profile-constraint enforcement** (`solver/enforcement.py`): weather
   windows (per-operation sensitivity from the profile), operator
   qualification per cluster, and cumulative material availability per depot;
   every exclusion is an explicit reason-coded record. Structural data
   semantics are filtered alongside (`solver/task_relations.py`,
   `solver/restrictions.py`): unmeetable workable windows, restricted zones
   and fully-blocking restriction windows, and dependents of excluded
   predecessors.
2. **Compatibility matrix** (`solver/feasibility.py`): vectorised power-margin
   feasibility between prime movers and related equipment.
3. **Operation-type filter** (`solver/preprocessing.py`): per-task candidate
   pairs restricted by `compatible-operations`.
4. **Geographic clustering** (`solver/preprocessing.py`): haversine BallTree
   depot-affinity clusters, split to a target size.
5. **Resource pre-allocation** (`solver/cluster_solver.py`): penalty-priority
   assignment of scarce prime movers, equipment, and operators to clusters;
   operator qualification and material limits are enforced on the allocated
   clusters.
6. **Greedy warm start** (`solver/greedy.py`): margin-minus-repositioning-cost
   scored construction heuristic.
7. **OR-Tools routing per cluster** (`solver/routing_model.py`,
   `solver/cluster_pool.py`): each cluster solved as a routing problem in a
   spawned process pool, warm-started from the greedy solution. Arc times
   come from travel-link lookups with haversine fallback; admissible start
   intervals encode workable windows minus restriction windows; a load
   dimension bounds route mass by vehicle `load-capacity` when tasks demand
   loads.
8. **Aggregation** (`solver/aggregator.py`): dispatch packages, canonical
   reason codes, KPIs.

Around the chain:

- **Observation assessment** (`snapshot/assessment.py`): statistical screening
  before any monitoring decision -- observed-time ordering with regression and
  clock-skew findings, retention windows and time-window series aggregation,
  source quality flags folded into confidence, MAD-based outlier exclusion per
  series, sensor-fault discrimination (battery rising without service, frozen
  values) flooring series confidence, drift detection on non-trending metrics,
  per-source watermarks stamped on the snapshot, per-source error rates on the
  snapshot quality summary, and cross-run error-rate trending
  (`snapshot/quality_trend.py`). See
  [model-world-divergence.md](model-world-divergence.md) for the full effect
  catalog these mechanisms cover.
- **Monitoring policy** (`snapshot/monitoring.py`): assessed observation
  series per (entity, metric) + maintenance state -> derived service tasks for
  stationary assets. Rules: battery at/below threshold, battery drain trend
  projected below threshold within the forecast horizon, unhealthy state,
  service interval exceeded, drifting metric (calibration), and a composite
  health score combining sub-critical signals. Readings below the policy's
  minimum confidence are ignored. Thresholds come from the profile's
  `monitoring` section (constant-backed defaults) with per-asset-type
  overrides.
- **Rolling dispatch** (`solver/reschedule.py`, `stream/driver.py`): freeze
  started/imminent tasks, carry unaffected assignments, re-solve the affected
  remainder with a plan-instability penalty; one immutable revision per
  converged event batch. Event application is idempotent by event id and
  upserts corrected rows/readings by their key columns.
- **Corrective rescheduling** (`adapters/rolling/corrective.py`,
  `stream/prognosis.py`): assignments that lose assets mid-plan are released
  and re-solved; service prognoses contradicted by newer readings are
  withdrawn, ones overtaken by reality (critical battery, failed health) are
  escalated and forced out of carry-forward; every self-repair is a
  CorrectiveAction on the revision, and accumulated false-positive /
  false-negative rates drive logged threshold-tuning recommendations.
- **Reproducibility** (`snapshot/hashing.py`): snapshots hash their canonical
  content (excluding per-run identifiers and finding wall-clocks), so identical
  inputs yield identical plans.

## Further reading

Ontology and contracts:

- Open Data Contract Standard (ODCS): https://bitol-io.github.io/open-data-contract-standard/
- Apache Avro specification: https://avro.apache.org/docs/

Routing and scheduling:

- Toth, P., Vigo, D. (eds.), "Vehicle Routing: Problems, Methods, and
  Applications", 2nd ed., SIAM, 2014. The standard VRP reference, including
  heterogeneous fleets and profits variants.
- Pinedo, M., "Scheduling: Theory, Algorithms, and Systems", Springer. Covers
  the machine/resource scheduling side of the bundle-assignment problem.
- Pillac, V. et al., "A review of dynamic vehicle routing problems", EJOR
  225(1), 2013. Background for the rolling-dispatch mode.
- Voudouris, C., Tsang, E., "Guided Local Search", in Handbook of
  Metaheuristics. The metaheuristic OR-Tools uses after the first solution.
- OR-Tools routing documentation: https://developers.google.com/optimization/routing

Condition-based maintenance:

- Jardine, A.K.S., Lin, D., Banjevic, D., "A review on machinery diagnostics
  and prognostics implementing condition-based maintenance", Mechanical Systems
  and Signal Processing 20(7), 2006. Background for observation-driven service
  derivation.

The staged tutorial in [03-learning-path.md](../algorithms/03-learning-path.md)
sequences these topics from integer programming through OR-Tools internals.
