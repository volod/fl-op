# ADR-014: Penalty-weighted cluster priority in resource pre-allocator

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /autoplan (both engineer voices)

## Context

The global pre-allocator (ADR-005) iterates clusters and reserves implements in
priority order. A sort key is needed. Two candidates were considered:

- **Raw order count** (`len(cluster.order_ids)`, descending): larger clusters
  get first pick. Simple to compute; intuitively "bigger cluster = more important".
- **Sum of `order.penalty_per_day_eur`** (`sum(o.penalty_per_day for o in cluster)`):
  clusters with higher total financial exposure get first pick.

## Decision

Sort clusters by **`sum(order.penalty_per_day_eur)` descending**, with
`cluster_id` as a lexicographic tiebreak for determinism.

## Rationale

Raw order count produces a correctness failure in a realistic scenario: a cluster
of 51 low-value grain monitoring orders (penalty: 10 EUR/day each, total: 510 EUR)
would beat a cluster of 50 high-urgency pesticide spraying orders (penalty:
500 EUR/day each, total: 25,000 EUR) and claim the shared sprayer implement.

The result: the pesticide cluster loses its sprayer. All 50 spraying orders miss
their deadline. Accumulated penalties reach 25,000 EUR/day. The 51-order cluster
that "won" the implement generates only 510 EUR/day of avoided penalty.

Penalty-weighted priority aligns pre-allocation with the business objective
(profit maximisation). The cluster that causes the highest financial damage if
delayed gets first choice of shared resources. This is equivalent to a greedy
approximation of the resource assignment sub-problem where penalty exposure is
the value function.

Both engineer voices in the /autoplan review confirmed this independently. Raw
order count was not raised as a viable alternative once the counter-example was
stated.

## Consequences

- `total_penalty_per_day` must be pre-computed per cluster and stored in
  `ClusterSpec` before the pre-allocator runs. The preprocessing step computes
  this from `order.penalty_per_day_eur` at cluster construction time.
- If all clusters have equal penalty sums (e.g. perfectly balanced synthetic
  data), the tiebreak by `cluster_id` ensures a deterministic, reproducible
  sort order across runs. Non-deterministic sort is a silent reproducibility bug.
- The pre-allocator's ordering is a greedy approximation, not an optimal resource
  assignment. A globally optimal assignment would require solving a sub-problem
  before the main problem — combinatorially expensive and out of scope for the POC.
