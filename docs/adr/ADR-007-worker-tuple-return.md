# ADR-007: Worker always returns (dispatch_packages, infeasible_orders) tuple

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

A cluster solver worker can produce two categories of output:
- Orders it successfully scheduled: `dispatch_packages`.
- Orders it could not schedule within the time limit or due to constraint
  violations: `infeasible_orders`.

The simplest API is to return only `dispatch_packages` and rely on the
aggregator to infer infeasible orders by subtraction (orders in the cluster
that are not in any dispatch package).

## Decision

The worker function **always returns a 2-tuple**:
```python
tuple[list[dict], list[dict]]  # (dispatch_packages, infeasible_orders)
```

The aggregator asserts `len(result) == 2` on every worker result before merging.

## Rationale

Returning only dispatch packages means infeasible orders are discovered by
set subtraction at the aggregator: `cluster.order_ids - {dp.order_id for dp in dispatch_packages}`.
This works when the worker completes normally but fails silently in two cases:

1. **Worker crash (WorkerLostError)**: the aggregator receives no result. It
   cannot distinguish "all orders served" from "worker died". The cluster's
   infeasible orders are silently lost from `infeasible_orders.json`, and KPIs
   (orders_rejected_count, total_penalty_EUR) are under-reported.

2. **Partial result**: if OR-Tools returns a solution that does not cover all
   cluster orders (prize-collecting behaviour), the aggregator must know which
   orders were explicitly rejected by the solver, not just absent from the
   solution. The reason tag ("time_window_breach", "no_compatible_vehicle") is
   solver-internal knowledge; only the worker can populate it.

Requiring the worker to always return a 2-tuple makes infeasibility explicit.
The assertion `assert len(result) == 2` catches any worker code that returns
the wrong structure immediately, rather than silently corrupting KPIs downstream.

## Consequences

- Every worker code path — including early-exit guards (empty cluster, zero
  feasible pairs, solver exception) — must return `([], infeasible_list)`.
- The `_mark_all_infeasible()` helper in `cluster_solver.py` centralises the
  construction of the infeasible list for all early-exit cases.
- If the aggregator receives a `WorkerLostError` or `TimeoutError` exception
  (not a result tuple), it synthesises an infeasible list for the entire cluster
  with reason "solver_timeout". This is handled in the except block, not in the
  worker.
- Tests must verify that infeasible orders appear in the final output, not only
  that dispatch packages are present (test T24).
