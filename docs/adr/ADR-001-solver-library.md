# ADR-001: OR-Tools routing library as sole solver

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

fl-op must solve a Heterogeneous Fleet VRP with Time Windows (HFVRPTW) combined
with multi-resource scheduling (vehicle-implement-operator triplets) and
profit-maximizing order selection (prize-collecting variant). The problem class
is NP-hard; no exact solver is tractable at 3000+ vehicles and 2500+ orders.

Three solver paths were evaluated:

- **OR-Tools routing library**: native Python (pip), handles time windows and
  route sequencing in a single model, C++ internals with Python bindings, no
  JVM dependency.
- **CP-SAT + routing hybrid**: use OR-Tools CP-SAT for resource allocation and
  a separate routing library for route sequencing. Two separate models with a
  hand-coded "seam" between them.
- **Timefold (formerly OptaPlanner)**: mature constraint satisfaction framework
  with excellent VRP support. Requires JVM; Python bindings exist but add
  deployment complexity.

## Decision

Use the **OR-Tools routing library** as the sole solver. No CP-SAT sub-problem.
No Timefold. Pyomo is available as a fallback only for constraints the routing
library cannot express.

## Rationale

The routing library's native time-window dimensions and multi-vehicle routing
model cover the entire problem structure without requiring a hand-coded seam
between two solvers. A CP-SAT + routing hybrid would require synchronising
decision variables across two solvers that operate on different data structures
— the integration complexity exceeds the benefit for a POC.

Timefold would require the JVM on every deployment target. The project explicitly
targets pip-installable Python tooling; JVM adds a hard dependency that breaks
the "run anywhere Python runs" promise.

## Consequences

- All time-window, route-sequencing, and resource-uniqueness constraints must be
  expressible in the routing library's API. Constraints that cannot be expressed
  this way require workarounds (e.g. penalty soft constraints or pre-filtering).
- Inventory is modelled as arc-capacity dimensions on the routing graph, not as
  a separate LP.
- The routing library's internal threading model must be controlled explicitly
  (see ADR-010) to avoid CPU over-subscription in the parallel pool.
- Pyomo remains available as a safety valve for exotic constraints; it was not
  needed in the POC.
