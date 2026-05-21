# ADR-004: Hierarchical depot-cluster decomposition

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

The full HFVRPTW problem at 3000-5000 vehicles and 2500-10000 orders is
computationally intractable as a single global solve. Three approaches were
considered:

- **Monolithic OR-Tools + LNS**: one global CP-SAT model with Large Neighborhood
  Search. The search space is 3000^10000 in the worst case; OR-Tools times out
  within seconds with no feasible solution at this scale.
- **Greedy + local search only**: fast assignment followed by swap-move
  improvement. No time-window or inventory feasibility guarantees; solution
  quality degrades as constraint density increases.
- **Hierarchical decomposition**: cluster orders by geographic depot affinity;
  solve each cluster independently in parallel with OR-Tools; aggregate results.

## Decision

Use **hierarchical depot-cluster decomposition**:
1. Pre-filter by compatibility matrix.
2. Cluster orders by nearest depot (haversine BallTree).
3. Split large depot groups into sub-clusters of CLUSTER_TARGET_SIZE (default 50).
4. Solve each cluster independently in a Pool worker.
5. Aggregate dispatch packages.

## Rationale

A cluster of 50 orders with ~500 feasible V-I pairs is a tractable OR-Tools
subproblem (solved in under 60 seconds on commodity hardware). Fifty such clusters
solved in parallel on a 50-core machine produce a full schedule in ~60 seconds
wall time. On an 8-16 core developer machine the same run takes 5-10 minutes —
acceptable for a POC.

The decomposition trades global optimality for tractability. Orders at the
boundary between two depot regions may be assigned suboptimally (inter-depot
repositioning is not globally optimised). This is an accepted approximation:
in the agricultural domain, depots are geographically dispersed and inter-depot
vehicle movement is rare; the clustering error is small relative to total profit.

Greedy assignment is retained as a warm-start hint (see ADR-012), preserving
the speed benefit of Approach C without sacrificing the constraint-satisfaction
guarantees of OR-Tools.

## Consequences

- Solution quality is bounded by cluster boundary quality. Poorly configured
  depot locations produce imbalanced clusters and degraded solutions.
- Cross-depot vehicle repositioning is not optimised globally. If a vehicle
  close to a field belongs to a distant depot, it may not be assigned to that
  field. This is deferred to post-POC.
- Cluster target size (CLUSTER_TARGET_SIZE) is a tunable constant. Too small
  causes unnecessary cluster splits and sub-optimal intra-depot routing; too
  large causes solver timeout within the per-cluster time limit.
- The aggregation step must detect and log cross-cluster resource conflicts
  (same vehicle scheduled in two clusters); the global pre-allocation pass
  (ADR-005) prevents this for implements and operators but not for vehicles
  in all edge cases.
