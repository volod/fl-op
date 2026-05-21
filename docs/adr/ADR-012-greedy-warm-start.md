# ADR-012: NumPy-vectorized greedy warm-start for OR-Tools

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

OR-Tools routing library finds better solutions within a fixed time limit when
given a good initial solution (warm-start). Without a warm-start, the first
solution strategy (PATH_CHEAPEST_ARC) may start from a suboptimal point and
spend the time limit on local improvements that don't converge to a high-quality
schedule.

The warm-start must be computed quickly — faster than the OR-Tools time limit
(60 seconds per cluster). At full scale (3000 vehicles x 20000 implements x
2500 orders), a Python-level loop over all feasible pairs evaluates 5M+
comparisons and takes minutes. This is too slow.

## Decision

Compute warm-start scores using **NumPy broadcasting** over all feasible
vehicle-implement pairs for all orders in a single vectorized operation:

```python
score = SCORE_WEIGHT_MARGIN * gross_margin - SCORE_WEIGHT_REPOSITION * reposition_cost
```

Both terms are computed as numpy arrays shaped `(n_pairs,)` using vectorized
haversine distance and broadcasting. No Python-level loop over pairs.

The result — top-1 V-I pair per order by score — is passed to the routing model
via `routing.ReadAssignmentFromRoutes()`.

## Rationale

NumPy operates on the full pair array in a single C kernel call. At 5M pairs
the vectorized scorer completes in under 1 second; the equivalent Python loop
takes 30-60 seconds — more than the solver time limit. The warm-start would
arrive after the solver had already exhausted its budget.

The warm-start is a separate concern from the OR-Tools solver (Approach C in
the design doc). Retaining it as a warm-start hint rather than discarding it
preserves the computational investment and improves OR-Tools solution quality
without complicating the solver model.

## Consequences

- All pair-level computation (repositioning cost, margin estimate) must be
  expressible as numpy array operations. Per-pair Python functions are forbidden
  in this code path.
- The warm-start hint is advisory. If OR-Tools cannot integrate the greedy routes
  (e.g. because they violate a time window not checked in the greedy step), the
  solver falls back to `SolveWithParameters()` without the initial hint. This
  fallback is wrapped in a try/except and must not raise.
- The greedy assignment also serves as a standalone baseline KPI
  (`greedy_baseline_margin_EUR` in `schedule_kpis.json`), allowing operators
  to see how much the full OR-Tools solve improved over naive assignment.
- The vectorized scorer is tested for correctness against known optimal
  assignments (test T15) and for the absence of Python loops (test T14).
