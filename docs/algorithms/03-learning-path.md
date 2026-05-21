# Learning Path

This document is a structured reading list for readers who want to understand or extend
the fl-op solver. It assumes a working knowledge of linear algebra, probability, and
basic combinatorial optimization (e.g., you have seen a TSP before). It does not assume
familiarity with OR-Tools or fleet routing in particular.

---

## Concept Map

```
Combinatorial Optimization Foundations
    |-- Integer Programming (IP / MIP)
    |-- Graph theory: Hamiltonian path, TSP
    `-- Complexity theory: NP-hardness, approximation

Vehicle Routing Problems (VRP)
    |-- CVRP (Capacitated VRP) -- classical base
    |-- VRPTW (VRP with Time Windows)
    |-- HFVRP (Heterogeneous Fleet VRP)
    `-- VRP with Profits / Selective VRP
        |-- Orienteering Problem
        `-- Profit-maximizing order selection

Multi-Resource Scheduling
    |-- Job shop scheduling
    `-- Resource-constrained project scheduling (RCPS)

Geographic/Spatial Methods
    |-- Haversine distance on the sphere
    |-- BallTree / k-d tree for nearest-neighbour queries
    `-- Geographic clustering (k-means on lat/lon)

Solver Techniques
    |-- Construction heuristics (greedy, savings algorithm)
    |-- Local search (2-opt, 3-opt, Lin-Kernighan)
    |-- Metaheuristics (Guided Local Search, Simulated Annealing, Tabu Search)
    `-- Exact methods (branch and bound, column generation)

OR-Tools Routing Library
    |-- Routing model construction
    |-- Transit callbacks and dimensions
    |-- Time window constraints
    `-- Search parameters and warm-start

NumPy Vectorization
    |-- Broadcasting semantics
    |-- Avoiding Python loops in hot paths
    `-- Memory-mapped arrays for large matrices

Parallel Computing
    |-- multiprocessing.Pool (spawn vs fork)
    `-- Shared memory via mmap (numpy memmap)
```

---

## Stage 1 -- Foundations (prerequisite)

### Integer Programming

If you have not encountered MIP before, start here.

**Book**: Wolsey, L.A. (1998). *Integer Programming*. Wiley.
- Chapter 1: formulation of combinatorial problems as IP
- Chapter 3: LP relaxation and why integrality makes things hard
- Chapter 8: branch and bound

**Free alternative**: Schrijver, A. (2003). *Combinatorial Optimization* (Chapters 1-5).
Available from many university libraries.

**Practical**: Understand what a binary variable x_{ij} in {0,1} means, what a relaxation
is, and why NP-hardness means we need heuristics at scale.

### Graph Theory for Routing

**Reading**: Diestel, R. (2017). *Graph Theory* (5th ed.), Springer. Free PDF at
the author's website.
- Chapter 1: basic definitions
- Chapter 10: Hamiltonian cycles (the TSP connection)

**Key concepts to know before continuing**:
- Directed and undirected graphs
- Hamiltonian paths and cycles
- Why TSP on general graphs is NP-complete (Cook, 1971)

---

## Stage 2 -- Vehicle Routing Problems

### Classical VRP

**Survey**: Toth, P., & Vigo, D. (Eds.) (2014). *Vehicle Routing: Problems, Methods,
and Applications* (2nd ed.). MOS-SIAM Series on Optimization.
- Chapter 1: problem statement, variants, applications
- Chapter 4: CVRP exact methods (branch and cut)
- Chapter 6: VRPTW (time window variant)

This book is the standard reference. Read Chapters 1 and 6 before continuing to OR-Tools.

**Shorter introduction**: Cordeau, J.F., Gendreau, M., Laporte, G., Potvin, J.Y., &
Semet, F. (2002). "A guide to vehicle routing heuristics." *Journal of the Operational
Research Society*, 53(5), 512-522.

### HFVRPTW (the fl-op base problem)

**Paper**: Gendreau, M., Laporte, G., Musaraganyi, C., & Taillard, E.D. (1999).
"A Tabu Search Heuristic for the Heterogeneous Fleet Vehicle Routing Problem."
*Computers & Operations Research*, 26(12), 1153-1173.

This paper introduces the heterogeneous fleet extension. The key idea: vehicles differ
in cost per km and capacity, so the optimal assignment of vehicle type to route is part
of the problem, not given in advance. In fl-op, the analogous choice is which (vehicle,
implement) pair to send to which order.

### VRP with Profits

**Survey**: Feillet, D., Dejax, P., & Gendreau, M. (2005). "Traveling Salesman Problems
with Profits." *Transportation Science*, 39(2), 188-205.

VRP with profits adds the decision of which customers to serve (not all customers need to
be visited). The Orienteering Problem is the simplest variant: maximize total collected
profit subject to a travel time budget. fl-op's order selection problem is this variant
extended with multi-resource constraints.

---

## Stage 3 -- Metaheuristics and Heuristics

### Construction Heuristics

The fl-op greedy warm-start is a construction heuristic: it builds an initial solution
by greedily assigning the highest-margin (vehicle, implement, order) triple at each step.

**Reading**: Christofides, N. (1976). *Worst-Case Analysis of a New Heuristic for the
Travelling Salesman Problem*. Carnegie Mellon University report.

The savings algorithm (Clarke and Wright, 1964) is the classical construction heuristic
for VRP. Understanding it gives intuition for why greedy warm-starts help exact/
metaheuristic solvers.

### Local Search

OR-Tools uses local search to improve the solution found by the construction heuristic.

**Book**: Aarts, E., & Lenstra, J.K. (Eds.) (1997). *Local Search in Combinatorial
Optimization*. Wiley.
- Chapter 1: 2-opt and 3-opt for TSP
- Chapter 5: simulated annealing
- Chapter 6: tabu search

**Key concepts**:
- Neighbourhood: the set of solutions reachable from the current solution by a single
  local move (e.g., 2-opt swaps two edges)
- Local optimum: a solution with no improving neighbour
- Metaheuristic: a strategy (tabu list, temperature schedule, penalties) for escaping
  local optima

### Guided Local Search (GLS)

OR-Tools' default metaheuristic is Guided Local Search, which augments the objective
function with penalty terms that discourage revisiting previously-explored regions.

**Paper**: Voudouris, C., & Tsang, E.P.K. (1999). "Guided local search." *European
Journal of Operational Research*, 113(2), 469-499.

GLS is practical because it requires no problem-specific tuning of penalty weights; the
penalties are computed from the solution structure automatically.

---

## Stage 4 -- OR-Tools Routing Library

After reading the VRP foundations and metaheuristic background, OR-Tools internals become
much clearer.

### OR-Tools Documentation

Official guide: https://developers.google.com/optimization/routing

Work through the routing tutorials in order:
1. Travelling Salesman Problem (TSP) -- model basics
2. Vehicle Routing Problem (VRP) -- multi-vehicle extension
3. VRP with Time Windows (VRPTW) -- time dimension
4. VRP with pickups and deliveries -- constraint types

**Code example alignment**: the fl-op cluster_solver.py closely follows the VRPTW
example. Reading the official tutorial first makes the fl-op code straightforward.

### OR-Tools API Details

**RoutingIndexManager**: maps between node indices (internal) and node numbers (external).
Required because OR-Tools adds dummy start/end nodes internally.

**Transit callbacks**: registered via `routing.RegisterTransitCallback()`. The callback
computes the cost of moving from one node to another (e.g., travel time in seconds).

**Dimensions**: a dimension tracks a quantity that accumulates along a route (time, load).
Time windows are enforced via a time dimension with `CumulVar` bounds.

**SearchParameters**: controls solver behaviour. The most important settings for fl-op:

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.seconds = CLUSTER_SOLVE_TIME_LIMIT_S
    search_params.sat_parameters.num_workers = 1  # see ADR-010

**Why num_workers=1?**: OR-Tools 9.15+ uses CP-SAT as the internal sub-solver for
routing. The parallelism knob moved from `search_params.num_search_workers` to
`search_params.sat_parameters.num_workers`. Setting it to 1 prevents thread contention
when many cluster workers run in parallel across CPU cores. See ADR-010.

---

## Stage 5 -- NumPy Vectorization

The fl-op greedy warm-start and compat matrix build rely on NumPy's ability to express
O(n^2) and O(n^3) computations without Python loops.

### Broadcasting

**Reading**: NumPy documentation, "Broadcasting" chapter.
https://numpy.org/doc/stable/user/basics.broadcasting.html

**Key insight**: when two arrays of shapes (m, 1) and (1, n) are used in a binary
operation, NumPy produces an (m, n) output without allocating intermediate arrays of
size m or n. This is how the power margin matrix is computed:

    rated[:, np.newaxis] - required[np.newaxis, :]  -> shape (3000, 20000)

**Book**: VanderPlas, J. (2016). *Python Data Science Handbook*, Chapter 2. Free at
https://jakevdp.github.io/PythonDataScienceHandbook/

### Memory-Mapped Arrays

Large arrays (300 MB compat matrix at production scale) cannot be cheaply copied to
worker processes. NumPy's `mmap_mode="r"` opens the `.npy` file as a read-only memory
map shared by all processes through the OS page cache.

**Reading**: NumPy documentation, `numpy.load` with `mmap_mode` parameter.
**OS concept**: understand that `mmap()` allows the OS to page-fault on first access,
bringing only the pages actually needed into RAM. This avoids a full 300 MB transfer
per worker process.

---

## Stage 6 -- Parallel Computing in Python

### Why multiprocessing, not threading

Python's Global Interpreter Lock (GIL) prevents true parallel execution of Python
bytecode across threads. `multiprocessing.Pool` bypasses the GIL by running worker
code in separate processes, each with its own interpreter.

**Reading**: Python documentation, `multiprocessing` module.
https://docs.python.org/3/library/multiprocessing.html

**Fork vs spawn**: `fork` copies the parent process, including all C++ state allocated
by OR-Tools. `spawn` starts a clean interpreter. For OR-Tools specifically, `fork` can
cause silent crashes or deadlocks because C++ global state (thread handles, mutexes)
is duplicated in an inconsistent state. See ADR-009.

### Task isolation with maxtasksperchild=1

OR-Tools' C++ routing model allocates heap memory that may not be freed when the Python
`RoutingModel` object is garbage collected. Over many clusters, a long-lived worker
process accumulates memory. Setting `maxtasksperchild=1` recycles the worker process
after each cluster task, bounding memory growth.

---

## Stage 7 -- Geographic Computations

### Haversine Distance

The haversine formula computes the great-circle distance between two points on a sphere
given their latitude and longitude in radians:

    a = sin^2((lat2 - lat1) / 2) + cos(lat1) * cos(lat2) * sin^2((lon2 - lon1) / 2)
    d = 2 * R * arcsin(sqrt(a))

where R = 6371.0 km (mean Earth radius, defined in `core/constants.py`).

This approximates the Earth as a sphere. The true Earth is an oblate spheroid; the
Vincenty formula accounts for this and is accurate to ~0.5 mm. For agricultural
logistics at distances under 200 km, haversine error is under 0.3% -- negligible.

**Reading**: Sinnott, R.W. (1984). "Virtues of the Haversine." *Sky and Telescope*, 68(2), 159.
(The original paper introducing the haversine formula to modern computing.)

### BallTree for Nearest-Neighbour Queries

A BallTree partitions a metric space into nested balls. For a query point q, it prunes
entire subtrees whose balls are farther from q than the current best candidate, achieving
O(log n) average query time.

**Reading**: Omohundro, S.M. (1989). *Five Balltree Construction Algorithms*. ICSI
Technical Report TR-89-063. Free PDF via ICSI.

For practical usage, the scikit-learn documentation on `BallTree` is the most useful
reference:
https://scikit-learn.org/stable/modules/generated/sklearn.neighbors.BallTree.html

**Note on coordinate convention**: sklearn's haversine kernel expects coordinates as
(latitude, longitude) in **radians**, not degrees. Always apply `np.radians()` before
passing coordinates.

---

## Extensions and Research Directions

### If you want to improve solution quality

- **Large Neighbourhood Search (LNS)**: randomly destroy part of the solution and
  re-optimise. More powerful than 2-opt for large instances.
  Paper: Shaw, P. (1998). "Using Constraint Programming and Local Search Methods to
  Solve Vehicle Routing Problems." *CP'98*, 417-431.

- **Column generation / branch-and-price**: exact method for VRP; practical for
  instances up to ~100 orders. Unlikely to scale to fl-op's 2500 orders but useful as
  an academic reference.
  Book: Desaulniers, G., Desrosiers, J., & Solomon, M.M. (Eds.) (2005).
  *Column Generation*. Springer.

### If you want to add new constraint types

- **Driver shift constraints**: operators have maximum working hours per day. Model as
  a second dimension in the OR-Tools routing model with a capacity constraint.

- **Implement transport logistics**: implements are towed, not self-propelled. Moving an
  implement from depot A to depot B requires a transport vehicle. This is a vehicle-
  implement pairing problem on top of the routing problem -- a richer but much harder
  formulation.

- **Weather uncertainty**: current model uses deterministic weather windows. A stochastic
  extension would model weather as a random variable and optimize expected margin. See
  stochastic VRP literature (Gendreau & Potvin, 2007).

### If you want to scale further

- **Dantzig-Wolfe decomposition**: instead of geographic clustering, decompose by time
  horizon. Each subproblem covers one day; linking constraints enforce vehicle continuity.
- **Parallel OR-Tools on GPU**: OR-Tools does not have GPU support as of 2025, but
  the greedy warm-start is GPU-amenable (haversine on CUDA).

---

## Recommended Reading Order

For a reader with a math background who wants to understand fl-op end-to-end in about
40 hours of study:

| Week | Topic | Material |
|------|-------|----------|
| 1 | IP foundations | Wolsey Ch. 1, 3, 8 |
| 1 | Graph theory basics | Diestel Ch. 1, 10 |
| 2 | VRP survey | Toth & Vigo Ch. 1, 6 |
| 2 | VRP with profits | Feillet et al. (2005) |
| 3 | Metaheuristics | Aarts & Lenstra Ch. 1, 5, 6 |
| 3 | Guided local search | Voudouris & Tsang (1999) |
| 4 | OR-Tools tutorials | developers.google.com/optimization/routing |
| 4 | NumPy broadcasting | VanderPlas Ch. 2 |
| 4 | fl-op source code | src/fl_op/solver/ top-to-bottom |

After completing this path, the ADRs in `docs/adr/` will read as engineering records
of specific decisions within a well-understood design space, rather than as opaque
choices.
