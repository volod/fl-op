# ADR-005: Global pre-allocation pass before cluster solve

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review (T2 cross-model tension)

## Context

Because clusters are solved independently (ADR-004), a physical implement or
operator can appear in the feasible set of two different clusters. If both
cluster solvers assign it, the resulting schedule dispatches the same sprayer
to two fields simultaneously — physically impossible.

Two solutions were considered:

- **Post-hoc conflict resolution**: let solvers run, then detect and re-assign
  conflicting resources. Requires a second solve pass or heuristic patching,
  and the patched solution may violate time windows.
- **Global pre-allocation before cluster solve**: before any cluster solver
  starts, iterate clusters in priority order and exclusively reserve each
  implement and operator to the first cluster that claims it. Remove reserved
  resources from subsequent clusters' feasible sets.

## Decision

Implement a **global pre-allocation pass** in `src/fl_op/solver/allocation/`
that runs before `multiprocessing.Pool` is opened.

Clusters are processed in descending order of `sum(order.penalty_per_day)` —
penalty-weighted urgency — with `cluster_id` as a deterministic tiebreak.
`implement_id`, `vehicle_id`, and `operator_id` are claimed exclusively to the
first cluster that needs them.

## Rationale

Post-hoc conflict resolution requires knowing which solution each solver found
before fixing conflicts, meaning at least one full solve pass must complete
before conflicts can be detected. In the worst case every cluster conflicts and
the entire pool must be re-run with reduced feasible sets — effectively two pool
passes.

Pre-allocation runs in O(N_implements) before any solver starts. It guarantees
that the feasible set delivered to each cluster worker is already conflict-free;
workers never see implements or operators claimed by a higher-priority cluster.
This is a one-pass O(N) operation with no re-scheduling.

Priority ordering by `sum(penalty_per_day)` ensures that deadline-critical,
high-penalty clusters get first choice of the shared resource pool. A cluster
of 51 low-value orders must not starve a cluster of 50 high-penalty orders.
Raw order count as a sort key was explicitly rejected for this reason.

## Consequences

- `solver/allocation` must be called once, synchronously, before the Pool
  is opened. It must not be called inside a worker.
- If pre-allocation exhausts all feasible implements for a cluster, that cluster
  is passed to the solver with an empty allocated_vehicle_implements dict. The
  solver short-circuits and marks all orders infeasible with reason
  "no_allocated_vehicles".
- The priority ordering (penalty-weighted) is a greedy approximation, not a
  globally optimal resource assignment. A cluster that loses an implement due to
  pre-allocation may have been able to serve more total profit than the winner.
  This is accepted: the approximation is conservative (high-penalty clusters
  always win) and avoids a combinatorial resource-allocation sub-problem.
- `cluster_id` as tiebreak for equal-penalty clusters ensures identical inputs
  produce identical outputs across runs (reproducibility requirement).
