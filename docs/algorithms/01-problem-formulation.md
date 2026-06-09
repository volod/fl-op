# Problem Formulation

## 1. Problem overview

The engine plans over **canonical entities** and is domain-agnostic: it selects
which **tasks** to fulfil, assigns a **prime mover + related equipment + operator**
bundle to each, and computes a dispatch schedule that maximizes total net margin
subject to physical, temporal, and contractual constraints. Not every task can or
should be served: accepting a low-margin task that blocks a high-margin one is a
business loss, so task selection is part of the problem.

The formal problem class is:

    Heterogeneous Fleet VRP with Time Windows (HFVRPTW)
    + Multi-resource scheduling (prime mover, related equipment, operator must all match)
    + Profit-maximizing task selection (not all tasks need to be served)

> "Vehicle Routing Problem" (VRP) and "vehicle" in the academic/OR-Tools sense
> below refer to a generic routed resource; they are the literature's names, not
> the agricultural domain.

## 2. Terminology: canonical model vs domain

These docs use the **agricultural** reference domain for concrete examples. The
engine itself only sees canonical entities; each physical domain maps onto them
(see [domain-mapping.md](../reference/domain-mapping.md)).

| Canonical (engine) | Agricultural example | Construction example |
|---|---|---|
| asset, role `mobile-prime-mover` | tractor, self-propelled sprayer | excavator, wheel loader |
| asset, role `implement` (related equipment) | plow, sprayer, seeder | bucket, breaker |
| asset, role `operator` | machine operator | machine operator |
| location, type `depot` | depot | yard |
| location, type `field` (work site) | field parcel | work site |
| task | service order | earthworks job |
| capability `rated-power` / `required-power` | engine kW / implement draw kW | machine kW / attachment kW |
| capability `working-width` | implement swath | attachment cut width |
| `operation-type` | SPRAYING, TILLAGE, ... | EXCAVATION, GRADING, ... |

The rest of this document states the math over the canonical entities, using
agricultural terms only as illustration.

## 3. Sets and indices

| Symbol | Description |
|--------|-------------|
| M      | set of prime movers, \|M\| up to 3000 |
| R      | set of related-equipment assets, \|R\| up to 20000 |
| P      | set of operators, \|P\| up to 3000 |
| D      | set of depot locations, \|D\| up to 50 |
| T      | set of candidate tasks, \|T\| up to 10000 |
| S      | set of work-site locations |

## 4. Input data (canonical fields)

### 4.1 Prime mover (asset)

- `asset_type`: source category (illustration: TRACTOR, SELF_PROPELLED, TRUCK)
- `rated_power`: power the asset can deliver; constrains related-equipment compatibility
- `fuel_consumption_rate`: energy burn at working load
- `lat` / `lon`: current location at planning time
- `home_depot_ref`: home depot
- availability: earliest dispatch time

### 4.2 Related equipment (asset)

- `asset_type` (illustration: SPRAYER, PLOW, SEEDER, ...)
- `required_power`: minimum prime-mover power to drive it
- `working_width`: swath width, determines pass count per site
- `compatible_operations`: operation types it can perform
- `home_depot_ref`

### 4.3 Task

- `operation_type`: operation required; matched against asset capabilities
- `location_ref`: target work site (with centroid coordinates)
- `area`: work-site area to be serviced
- `deadline`: hard deadline; penalty accrues after it
- `penalty_per_day`: contractual late-delivery penalty rate
- `revenue`: expected gross value if delivered on time

### 4.4 Location (work site / depot)

- centroid coordinates `lat`, `lon`
- `area` (sites); depot affinity from a nearest-depot BallTree query

## 5. Decision variables

Let x_{m,r,t} in {0, 1} indicate that prime mover m is paired with related
equipment r to serve task t. Additionally:

- `start_{m,r,t}` in R: scheduled start time for the assignment
- `selected_t` in {0, 1}: 1 if task t is included in the schedule

with `selected_t = 1  <=>  exists exactly one (m, r) such that x_{m,r,t} = 1`.

## 6. Compatibility constraints

### 6.1 Power compatibility

Prime mover m can drive related equipment r only if:

    rated_power_m >= required_power_r * (1 - POWER_MARGIN_PCT / 100)

`POWER_MARGIN_PCT = 10.0` (`core/constants.py`): a prime mover may drive equipment
needing slightly more power than its rated output, within tolerance.

### 6.2 Operation-type compatibility

Related equipment r can serve task t only if `operation_type_t in compatible_operations_r`.
Power and operation-type compatibility are encoded as a boolean matrix C of shape
(\|M\|, \|R\|) computed once at solve time.

### 6.3 Assignment uniqueness

    sum_{r,t} x_{m,r,t} <= 1   for all m   (each prime mover serves one task at a time)
    sum_{m,t} x_{m,r,t} <= 1   for all r   (each related-equipment asset used once at a time)
    sum_{m,r} x_{m,r,t} <= 1   for all t   (each task served by one bundle)

### 6.4 Time windows

For each selected task t:

    available_from_m <= start_{m,r,t}
    start_{m,r,t} + duration_{m,r,t} <= deadline_t

where `duration_{m,r,t} = area_t / effective_rate_{m,r}` and
`effective_rate_{m,r} = working_width_r * field_speed / 10` [area/h].

## 7. Objective function

Maximize total net margin across selected tasks:

    maximize  sum_t selected_t * margin_t(m, r)

    margin_t(m, r) = revenue_t - fuel_cost_t(m, r) - reposition_cost_t(m) - material_cost_t(r)

- `fuel_cost = duration * fuel_consumption_rate_m * FUEL_COST_EUR_PER_L` (1.45)
- `reposition_cost = haversine(location_m, site_t) / field_speed * fuel_consumption_rate_m * FUEL_COST_EUR_PER_L`
- `material_cost = area_t * material_kg_per_area * FERTILIZER_COST_EUR_PER_KG` (0.55); zero for non-material operations

## 8. Why this is hard

- **Size**: at \|M\|=3000, \|R\|=20000, \|T\|=2500 there are ~150 billion (m, r, t)
  triples; even after compatibility filtering, hundreds of millions remain. Exact
  MIP cannot enumerate this in operational time.
- **Coupled resources**: prime mover, related equipment, and operator form a
  three-way assignment; assigning m to t blocks m from all other tasks.
- **Sequencing**: `start_{m,r2,t2} >= start_{m,r1,t1} + duration_{m,r1,t1} + travel(site_t1, site_t2)`
  couples with the time windows.
- **Selective serving (VRP with profits)**: choosing which tasks to drop is itself
  NP-hard (akin to the Orienteering Problem / selective TSP).

## 9. Problem class in the literature

A special case of the **Vehicle Routing Problem with Time Windows and Multiple
Resource Constraints** (VRPTW-MR), extended with profit-maximizing selection:

- **HFVRPTW base**: Gendreau, Laporte, Musaraganyi & Taillard (1999), *Computers &
  Operations Research* 26(12), 1153-1173.
- **Selective VRP (profits)**: Feillet, Dejax & Gendreau (2005), *Transportation
  Science* 39(2), 188-205.
- **Multi-resource VRP**: Ribeiro & Laporte (2012), *Computers & Operations
  Research* 39(3), 728-735.

The capability/requirement structure (power, operation-type, area rates) is what
enables the hierarchical decomposition in `02-solver-pipeline.md`.

## 10. Relation to implemented code

| Mathematical object | Code location |
|---------------------|---------------|
| Canonical entities (M, R, P, D, T, S) | `src/fl_op/canonical/` |
| Solver working rows (projected from a snapshot) | `src/fl_op/solver/inputs.py` |
| Compatibility matrix C | `src/fl_op/solver/feasibility.py` |
| x_{m,r,t} variables | OR-Tools routing model in `solver/cluster_solver.py` |
| margin_t(m, r) | `solver/greedy.py` vectorized scorer |
| Task selection | `solver/allocation/` + `solver/aggregator.py` |
| Infeasible tasks | TypedDict `InfeasibleOrder` in `solver/types.py` |
| Time-window / op-type filter | `solver/preprocessing.py` |

See `02-solver-pipeline.md` for how this formulation maps onto tractable subproblems.
