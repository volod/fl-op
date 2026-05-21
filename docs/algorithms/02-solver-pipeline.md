# Solver Pipeline

## 1. Pipeline Overview

The 150-billion-triple HFVRPTW cannot be solved as a monolithic MIP. The fl-op solver
uses a **four-stage hierarchical decomposition** that reduces the search space at each
stage before handing a tractable sub-problem to OR-Tools:

```
Stage 1: Compatibility filter         (150B triples -> ~5M feasible pairs)
Stage 2: Geographic clustering        (global problem -> 50 depot subproblems)
Stage 3: Global pre-allocation        (shared resources claimed before solving)
Stage 4: OR-Tools routing per cluster (small independent VRPs, solved in parallel)
```

Each stage is described in detail below.

---

## 2. Stage 1 -- Compatibility Matrix

### 2.1 What it does

Reduces the set of candidate (vehicle, implement) pairs from |V| x |I| = 60 million
to those that are physically compatible.

### 2.2 Power compatibility

For each pair (v, i), compute:

    power_margin_pct(v, i) = (rated_power_kw_v - required_power_kw_i)
                             / rated_power_kw_v * 100.0

The pair is compatible iff:

    power_margin_pct(v, i) >= -POWER_MARGIN_PCT    (= -10.0)

This allows a vehicle to pull an implement that needs up to 10% more power than rated.
The threshold models real-world overload tolerance common in agricultural machinery.

### 2.3 NumPy vectorization

At 3000x20000 scale, a Python loop over pairs is infeasible. The matrix is computed
as a single NumPy broadcast:

    rated   = np.array([v.rated_power_kw for v in vehicles], dtype=np.float32)  # (3000,)
    required = np.array([i.required_power_kw for i in implements], dtype=np.float32)  # (20000,)
    power_margin = (rated[:, np.newaxis] - required[np.newaxis, :]) / rated[:, np.newaxis] * 100.0
    # shape: (3000, 20000), float32

    compat = power_margin >= -POWER_MARGIN_PCT
    # shape: (3000, 20000), bool

The two arrays (compat, power_margin) are stored as `.npy` files and loaded with
`mmap_mode="r"` in worker processes -- no data copy, shared OS page cache.

### 2.4 Operation-type filter

After the power matrix is built, `preprocessing.py` further filters pairs per order
by checking:

    operation_type_o in implement_i.compatible_operations

This filtering happens at scheduling time, not at matrix build time, because the same
(v, i) pair may be compatible for SPRAYING orders but not TILLAGE orders.

### 2.5 Memory layout

At production scale, the bool compat matrix is:

    3000 * 20000 * 1 byte = 60 MB

The float32 power_margin companion is:

    3000 * 20000 * 4 bytes = 240 MB

Both fit in RAM. The memmap approach allows worker processes to read the matrix without
paying the cost of pickling and transferring 300 MB over the multiprocessing pipe.

---

## 3. Stage 2 -- Geographic Clustering

### 3.1 Motivation

A single VRP over 10000 orders and 3000 vehicles is intractable for exact solvers in
under 10 minutes. However, in agricultural logistics, most vehicle-field assignments are
geographically local: a tractor does not drive 500 km to work a field when an equivalent
tractor is 5 km away. This locality allows decomposition by depot.

### 3.2 BallTree nearest-depot assignment

Each order's field has a centroid (lat, lon). Each depot has a centroid. The nearest
depot is found using a BallTree over depot coordinates with the haversine metric:

    tree = BallTree(np.radians(depot_coords), metric='haversine')
    _, idx = tree.query(np.radians(field_coords), k=1)

BallTree achieves O(log |D|) per query. At |D| = 50 depots, this is trivial, but the
BallTree abstraction generalises cleanly if depot count grows.

The haversine distance on the unit sphere surface is:

    d = 2 * R * arcsin( sqrt( sin^2((lat2-lat1)/2)
                             + cos(lat1)*cos(lat2)*sin^2((lon2-lon1)/2) ) )

Coordinates are passed in radians (as required by sklearn's haversine kernel).

**Why sklearn, not scipy?** scipy 1.14+ removed `scipy.spatial.BallTree` from the
public API. See ADR-015. `sklearn.neighbors.BallTree` with `metric='haversine'` is the
supported replacement and preserves identical behaviour.

### 3.3 Cluster construction

Orders assigned to the same depot form a cluster. Each cluster is described by a
`ClusterSpec` TypedDict:

```python
class ClusterSpec(TypedDict, total=False):
    cluster_id: Required[str]
    depot_id: Required[str]
    order_ids: Required[list[str]]
    allocated_vehicle_implements: Required[dict[str, list[str]]]
    total_penalty_per_day: Required[float]
```

`allocated_vehicle_implements` maps vehicle_id -> [implement_id] and is populated in
Stage 3. `total_penalty_per_day` is pre-summed here and used as the priority key in
Stage 3.

### 3.4 Target cluster size

If a single depot's orders exceed `CLUSTER_TARGET_SIZE = 50`, orders are further split
into sub-clusters by geographic proximity within the depot region (k-means on field
centroids). This keeps each OR-Tools subproblem tractable.

---

## 4. Stage 3 -- Global Pre-Allocation

### 4.1 The shared-resource problem

Implements and vehicles are shared across clusters. If cluster A and cluster B both need
the same sprayer implement (the only one compatible with their orders), one cluster must
lose it. A per-cluster solver cannot see this conflict; it must be resolved globally
before solvers run.

### 4.2 Priority ordering

Clusters are sorted by:

    key = (-total_penalty_per_day, cluster_id)

`total_penalty_per_day = sum(order.penalty_per_day_eur for order in cluster.orders)`

This is **penalty-weighted priority**: the cluster that causes the highest financial
damage if its orders miss their deadline gets first pick of shared resources. A
lexicographic tiebreak on `cluster_id` ensures deterministic output across runs.

**Counter-example for naive count-based priority**: a cluster of 51 low-urgency orders
(penalty 10 EUR/day each, total 510 EUR) would outrank a cluster of 50 high-urgency
spraying orders (penalty 500 EUR/day each, total 25,000 EUR) if the key were `len(orders)`.
The sprayers lose their implement; accumulated daily penalties reach 25,000 EUR/day.
See ADR-014 for the full analysis.

### 4.3 Allocation algorithm

```
claimed_implements = {}
claimed_vehicles   = {}
claimed_operators  = {}

for cluster in sorted_clusters:
    for order in cluster.orders:
        for (v, i) in feasible_pairs(order):
            if i not in claimed_implements and v not in claimed_vehicles:
                operator = best_available_operator(v)
                if operator is not None:
                    allocate(cluster, v, i, operator)
                    claim(v, i, operator)
                    break
```

This greedy global allocation runs in O(|clusters| * MAX_PAIRS_PER_ORDER) time. It is
an approximation -- a globally optimal assignment would require solving a sub-MIP. For a
POC at this scale, greedy allocation with penalty-weighted priority is adequate.

### 4.4 Separation of concerns

Pre-allocation decouples resource exclusivity from routing. Each cluster solver receives
a `ClusterSpec` with `allocated_vehicle_implements` pre-filled. The solver only needs to
sequence orders and compute start times; it does not need to resolve inter-cluster
resource conflicts.

---

## 5. Stage 4 -- OR-Tools Routing Per Cluster

### 5.1 OR-Tools routing library

OR-Tools provides a **routing library** (`ortools.constraint_solver.routing_enums_pb2`)
built on top of its CP-SAT constraint solver. The routing library accepts:

- A time/distance matrix between locations
- Time window constraints per node
- Capacity constraints per vehicle
- A fleet of vehicles with start/end depots

The solver applies a combination of local-search metaheuristics (guided local search,
simulated annealing, tabu search) seeded with a construction heuristic warm-start.

### 5.2 Model construction per cluster

For each cluster, a routing model is built with:

    manager = pywrapcp.RoutingIndexManager(
        n_nodes,       # orders + depot
        n_vehicles,    # vehicles allocated to this cluster
        depot_node,    # all vehicles start and end at cluster's depot
    )
    routing = pywrapcp.RoutingModel(manager)

**Transit callback**: haversine travel time between field centroids:

    def transit_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(travel_time_h[from_node][to_node] * 3600)  # seconds

**Time dimension**: enforces time window [earliest_start, deadline] per order node.

**Objective**: minimize total travel time (the margin maximization is handled by
the greedy warm-start; OR-Tools refines the routing to reduce reposition cost).

### 5.3 Greedy warm-start

Before calling the OR-Tools solver, a vectorized greedy scorer ranks all (v, i, o)
triples by estimated margin. This warm-start is passed to OR-Tools via
`routing.CloseModelWithParameters(search_params)` and the initial solution builder.

The greedy scorer computes for each candidate triple (v, i, o):

    gross_margin = estimated_revenue_eur_o - fuel_cost(v, i, o) - material_cost(i, o)
    reposition_cost = haversine(v.location, o.field.centroid) * fuel_burn_rate

    score = SCORE_WEIGHT_MARGIN * gross_margin - SCORE_WEIGHT_REPOSITION * reposition_cost

The haversine computation is vectorized over all candidates in a single NumPy call:

    lat1, lon1 = vehicle positions  (broadcast over orders)
    lat2, lon2 = field centroids     (broadcast over vehicles)

    dphi = lat2 - lat1          # (n_vehicles, n_orders)
    dlambda = lon2 - lon1
    a = sin(dphi/2)**2 + cos(lat1) * cos(lat2) * sin(dlambda/2)**2
    dist_km = 2 * R * arcsin(sqrt(a.clip(0, 1)))

No Python loops. The full 5-million-pair evaluation runs in a single C kernel call.
See ADR-012 for the rationale.

### 5.4 Parallelism

Clusters are solved in parallel using `multiprocessing.Pool`:

```python
pool = Pool(
    processes=cpu_count(),
    context=mp.get_context("spawn"),
    maxtasksperchild=1,
)
```

**Why spawn?** The `fork` start method inherits the parent's OR-Tools C++ objects,
which contain internal state that is not safe to share across processes. `spawn` starts
a clean Python interpreter. See ADR-009.

**Why maxtasksperchild=1?** OR-Tools allocates C++ heap memory for routing models.
This memory is not always released when the Python object goes out of scope. Recycling
the worker process after each cluster task prevents memory accumulation over many clusters.

**Why num_workers=1?** OR-Tools 9.15 uses CP-SAT as the internal sub-solver. The
threading knob for the routing library is `sat_parameters.num_workers`, not the older
`search_params.num_search_workers`:

```python
search_params.sat_parameters.num_workers = 1
```

Setting this to 1 prevents OR-Tools from spawning additional threads inside an already-
parallel process pool, avoiding thread contention. See ADR-010.

### 5.5 Worker return contract

Every cluster worker returns a 2-tuple:

    (list[DispatchPackage], list[InfeasibleOrder])

The aggregator asserts `len(result) == 2` before unpacking. If the worker raises an
exception (OR-Tools timeout, memory error, unexpected failure), the aggregator catches
it and records all orders in that cluster as infeasible with reason `solver_error`.
See ADR-007.

---

## 6. Output Aggregation

After all cluster workers complete, the aggregator:

1. Merges all `DispatchPackage` lists into `schedule.json`
2. Merges all `InfeasibleOrder` lists into `infeasible_orders.json`
3. Computes a **greedy baseline** by running the greedy scorer alone (no OR-Tools)
   to estimate what naive nearest-vehicle assignment would earn
4. Emits `schedule_kpis.json` with:
   - `n_dispatched`, `n_infeasible`
   - `total_estimated_margin_eur`
   - `greedy_baseline_margin_eur`
   - `solver_improvement_eur` = total margin - greedy baseline

The KPI comparison quantifies OR-Tools' contribution over the greedy warm-start.

---

## 7. Rolling Horizon Reschedule

After orders start in the field, the system receives events:

    {"type": "mark_started", "order_id": "..."}

Started orders are **frozen**: their vehicle, implement, and start time cannot change.
The remaining (unstarted, undispatched) orders are re-optimised with the current fleet
state, which has changed because started orders have consumed some vehicles.

The reschedule runs the same 4-stage pipeline with the frozen set excluded from Stage 2
onwards. Output includes a `plan_diff.json` that records which dispatch packages changed,
were added, or were dropped relative to the previous schedule.

---

## 8. Fast Contract Query

Before accepting a new contract, a fleet manager can query feasibility and margin
estimates without running the full solver:

1. Load the current schedule to build a time index:
   `{vehicle_id: [TimeWindow(start, end)]}` for all dispatched vehicles
2. For the candidate order, scan all (v, i) pairs that match operation type and power
   compatibility, sorted by estimated margin descending
3. For each candidate pair, check whether the vehicle's schedule has a gap long enough
   to serve the new order before its deadline
4. Classify conflict risk:
   - "low"    -- vehicle has no other dispatched orders
   - "medium" -- vehicle has 1 conflict or near-conflict
   - "high"   -- vehicle is already heavily committed

Return the top-3 candidates by margin. No OR-Tools call; response in under 5 seconds.

---

## 9. Data Flow Summary

```
generate-data
    -> depots.csv, vehicles.csv, implements.csv, operators.csv
    -> fields.csv, orders.csv, contracts.json, weather.json, metadata.json

solve
    -> load CSVs into Pydantic models
    -> build compat matrix (Stage 1)
    -> BallTree clustering (Stage 2)
    -> pre-allocation (Stage 3)
    -> Pool.map(cluster_solver, clusters) (Stage 4)
    -> aggregate results
    -> schedule.json, schedule_report.txt, schedule_kpis.json, infeasible_orders.json

reschedule
    -> load previous schedule
    -> apply events (freeze started orders)
    -> re-run stages 2-4 on unfrozen orders
    -> plan_diff.json, plan_diff.txt

query-contract
    -> load schedule
    -> build vehicle time index
    -> scan compatible pairs
    -> return top-3 with conflict risk
```

---

## 10. Complexity Analysis

| Stage | Time complexity | Dominant cost |
|-------|----------------|---------------|
| Compat matrix build | O(\|V\| * \|I\|) | NumPy broadcast, ~100ms at 3000x20000 |
| BallTree cluster | O(\|O\| * log \|D\|) | log-factor on depot count |
| Pre-allocation | O(\|clusters\| * MAX_PAIRS) | linear, sub-second |
| Greedy warm-start | O(\|V\| * \|I\| * \|O\|) vectorized | single C kernel, ~500ms |
| OR-Tools per cluster | heuristic, ~O(n^2 * time_limit) | dominant; 60s limit per cluster |
| Aggregation | O(\|O\|) | negligible |

The OR-Tools stage is the wall-clock bottleneck. Parallelism across clusters gives an
effective speedup proportional to CPU core count: 8 cores reduce a 50-cluster solve
from 50 minutes to ~6 minutes.

---

## 11. Relation to Source Code

| Concept | File |
|---------|------|
| Compat matrix build + save/load | `src/fl_op/models/compat_matrix.py` |
| Feasibility filter per order | `src/fl_op/solver/preprocessing.py` |
| BallTree clustering | `src/fl_op/solver/preprocessing.py` |
| Pre-allocator | `src/fl_op/solver/resource_allocator.py` |
| Greedy warm-start scorer | `src/fl_op/solver/greedy.py` |
| OR-Tools routing worker | `src/fl_op/solver/cluster_solver.py` |
| Parallel pool + aggregation | `src/fl_op/solver/aggregator.py` |
| Reschedule | `src/fl_op/solver/reschedule.py` |
| Contract query | `src/fl_op/solver/query.py` |
| All numeric constants | `src/fl_op/core/constants.py` |
