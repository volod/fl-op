# Problem Formulation

## 1. Problem Overview

Agricultural fleet optimization is a joint **order selection** and **vehicle routing**
problem. A fleet manager receives a set of candidate orders (contracts to spray, till,
seed, harvest, or fertilize specific fields). Not every order can or should be accepted:
accepting a low-margin order that blocks a high-margin one is a business loss. The goal
is to choose which orders to fulfill, assign a vehicle-implement-operator triple to each,
and compute a dispatch schedule that maximizes total net margin subject to physical,
temporal, and contractual constraints.

The formal problem class is:

    Heterogeneous Fleet VRP with Time Windows (HFVRPTW)
    + Multi-resource Scheduling (vehicles, implements, operators must all match)
    + Profit-Maximizing Order Selection (not all orders need to be served)

---

## 2. Sets and Indices

| Symbol | Description |
|--------|-------------|
| V      | set of vehicles, |V| up to 3000 |
| I      | set of implements, |I| up to 20000 |
| P      | set of operators (pilots), |P| up to 3000 |
| D      | set of depots, |D| up to 50 |
| O      | set of candidate orders, |O| up to 10000 |
| F      | set of fields |

---

## 3. Input Data

### 3.1 Vehicle

Each vehicle v in V is characterised by:

- `type_v` in {TRACTOR, SELF_PROPELLED, TRUCK}: determines which implement types can attach
- `rated_power_kw_v`: engine output; constrains implement compatibility
- `fuel_consumption_l_per_h_v`: fuel burn at rated power
- `current_location_v` = (lat_v, lon_v): GPS coordinates at planning time
- `depot_id_v`: home depot
- `available_from_v`: earliest dispatch time (UTC ISO-8601)

### 3.2 Implement

Each implement i in I is characterised by:

- `type_i` in {SPRAYER, PLOW, DISK_HARROW, SEEDER, COMBINE_HEADER, FERTILIZER_SPREADER}
- `required_power_kw_i`: minimum engine power to drive this implement
- `working_width_m_i`: swath width, determines pass count per field
- `compatible_operations_i` subset of {SPRAYING, TILLAGE, SEEDING, HARVESTING, FERTILIZING}
- `depot_id_i`: storage location

### 3.3 Order

Each order o in O is characterised by:

- `operation_type_o` in {SPRAYING, TILLAGE, SEEDING, HARVESTING, FERTILIZING}
- `field_id_o`: target field with centroid coordinates
- `area_ha_o`: field area to be worked
- `deadline_o`: hard deadline (UTC ISO-8601); penalty accrues after this
- `penalty_per_day_eur_o`: contractual late-delivery penalty rate
- `estimated_revenue_eur_o`: gross contract value if delivered on time

### 3.4 Field

Each field f in F has:

- centroid coordinates `(lat_f, lon_f)`
- area `area_ha_f`
- depot affinity derived from nearest-depot BallTree query

---

## 4. Decision Variables

Let x_{v,i,o} in {0, 1} indicate that vehicle v is paired with implement i to serve order o.

Additionally:

- `start_{v,i,o}` in R: scheduled start time for assignment (v, i, o)
- `selected_o` in {0, 1}: 1 if order o is included in the schedule

Feasibility requires:

    selected_o = 1  <=>  exists exactly one (v, i) such that x_{v,i,o} = 1

---

## 5. Compatibility Constraints

### 5.1 Power compatibility

Vehicle v can pull implement i only if:

    rated_power_kw_v >= required_power_kw_i * (1 - POWER_MARGIN_PCT / 100)

`POWER_MARGIN_PCT = 10.0` (defined in `core/constants.py`). A vehicle can pull an implement
that requires slightly more power than its rated output, within this tolerance.

### 5.2 Operation-type compatibility

Implement i can serve order o only if:

    operation_type_o in compatible_operations_i

Both the power constraint and the operation-type constraint are encoded as a boolean
compatibility matrix C of shape (|V|, |I|) computed once at solve time.

### 5.3 Assignment uniqueness

Each vehicle can be assigned to at most one order at a time:

    sum_{i in I, o in O} x_{v,i,o}  <=  1    for all v in V

Each implement can be assigned to at most one order at a time:

    sum_{v in V, o in O} x_{v,i,o}  <=  1    for all i in I

Each order is served by at most one vehicle-implement pair:

    sum_{v in V, i in I} x_{v,i,o}  <=  1    for all o in O

### 5.4 Time windows

For each selected order o, the scheduled start must satisfy:

    available_from_v  <=  start_{v,i,o}                    (vehicle available)
    start_{v,i,o} + duration_{v,i,o}  <=  deadline_o       (completes before deadline)

where:

    duration_{v,i,o} = area_ha_o / effective_rate_{v,i}
    effective_rate_{v,i} = working_width_m_i * field_speed_kmh / 10  [ha/h]

---

## 6. Objective Function

Maximize total net margin across selected orders:

    maximize  sum_{o in O} selected_o * margin_o(v, i)

where the margin for order o served by vehicle v with implement i is:

    margin_o(v, i) = estimated_revenue_eur_o
                   - fuel_cost_o(v, i)
                   - reposition_cost_o(v)
                   - material_cost_o(i)

**Fuel cost:**

    fuel_cost_o(v, i) = duration_{v,i,o} * fuel_consumption_l_per_h_v * FUEL_COST_EUR_PER_L

where `FUEL_COST_EUR_PER_L = 1.45`.

**Repositioning cost** (driving from vehicle's current location to field centroid):

    distance_km = haversine(current_location_v, centroid_field_o)
    travel_h = distance_km / field_speed_kmh
    reposition_cost = travel_h * fuel_consumption_l_per_h_v * FUEL_COST_EUR_PER_L

**Material cost** (for fertilizer and spraying operations):

    material_cost_o(i) = area_ha_o * material_kg_per_ha * FERTILIZER_COST_EUR_PER_KG

where `FERTILIZER_COST_EUR_PER_KG = 0.55`. Zero for non-material operations.

---

## 7. Why This Is Hard

### 7.1 Size

At production scale:

- |V| = 3000, |I| = 20000, |O| = 2500
- Number of (v, i, o) triples: 3000 * 20000 * 2500 = 150 billion
- Even after compatibility filtering, hundreds of millions of feasible triples remain

Exact MIP solvers cannot enumerate this space in operational time.

### 7.2 Coupled constraints

Vehicles, implements, and operators form a three-way assignment. Assigning vehicle v to
order o blocks v from all other orders, regardless of which implement is used. A solver
must reason about three interacting resources simultaneously.

### 7.3 Time windows and sequencing

A vehicle completing order o1 before moving to order o2 must satisfy:

    start_{v,i2,o2}  >=  start_{v,i1,o1} + duration_{v,i1,o1} + travel_time(field_o1, field_o2)

This creates sequencing dependencies that interact with the time-window constraints.

### 7.4 Selective serving (VRP with profits)

The fleet cannot serve all orders. Choosing which orders to drop (infeasible) and which
to serve is itself an NP-hard selection problem (akin to the Orienteering Problem or the
selective TSP). The solver must jointly optimize order selection and routing.

---

## 8. Problem Class in the Literature

This problem is a special case of the **Vehicle Routing Problem with Time Windows and
Multiple Resource Constraints** (VRPTW-MR), extended with profit-maximizing order
selection. Key references from the academic literature:

- **HFVRPTW base**: Gendreau, M., Laporte, G., Musaraganyi, C., & Taillard, E.D. (1999).
  "A Tabu Search Heuristic for the Heterogeneous Fleet Vehicle Routing Problem."
  Computers & Operations Research, 26(12), 1153-1173.

- **Selective VRP (profits)**: Feillet, D., Dejax, P., & Gendreau, M. (2005).
  "Traveling Salesman Problems with Profits." Transportation Science, 39(2), 188-205.

- **Multi-resource VRP**: Ribeiro, G.M., & Laporte, G. (2012). "An adaptive large
  neighbourhood search heuristic for the cumulative capacitated vehicle routing problem."
  Computers & Operations Research, 39(3), 728-735.

The fl-op problem adds agricultural-specific structure (implement compatibility,
operation-type matching, field area rates) that allows the hierarchical decomposition
described in `02-solver-pipeline.md`.

---

## 9. Relation to Implemented Code

| Mathematical object | Code location |
|---------------------|---------------|
| V, I, P, D, O, F    | `src/fl_op/models/` Pydantic models |
| Compatibility matrix C | `src/fl_op/models/compat_matrix.py` |
| x_{v,i,o} variables | OR-Tools routing model in `solver/cluster_solver.py` |
| margin_o(v, i)      | `solver/greedy.py` vectorized scorer |
| Order selection     | `solver/resource_allocator.py` + `solver/aggregator.py` |
| Infeasible orders   | TypedDict `InfeasibleOrder` in `models/types.py` |
| Time window check   | `solver/preprocessing.py` filter_feasible_vehicle_implement_pairs() |

See `02-solver-pipeline.md` for how the solver maps this formulation onto tractable
subproblems.
