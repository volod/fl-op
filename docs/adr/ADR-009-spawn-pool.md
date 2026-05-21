# ADR-009: multiprocessing spawn pool with maxtasksperchild=1

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /autoplan

## Context

Cluster solver workers are long-running OR-Tools processes that allocate large
C++ objects (RoutingModel, RoutingIndexManager) and release them only when the
function returns. The Pool must handle:

1. Worker start method: `fork` vs `spawn` vs `forkserver`
2. Memory accumulation across tasks in the same worker process
3. Worker crash isolation (WorkerLostError)
4. Per-task time limit enforcement

## Decision

Use `multiprocessing.get_context("spawn")` with `maxtasksperchild=1`:

```python
ctx = multiprocessing.get_context("spawn")
with ctx.Pool(processes=n_workers, maxtasksperchild=1) as pool:
    ...
```

Per-task timeout is enforced via `AsyncResult.get(timeout=CLUSTER_SOLVE_TIME_LIMIT_S + 30)`.

## Rationale

**spawn vs fork**: `fork` copies the parent process's memory space into the
child. On Linux this includes the OR-Tools shared library state, any numpy
memmap file descriptors, and the full Pydantic model registry. If the parent
holds a lock (e.g. a numpy mmap lock), forked children inherit a deadlocked
state. `spawn` starts a clean Python interpreter, imports only the worker
module, and receives data via pickle — no inherited state.

**maxtasksperchild=1**: OR-Tools allocates C++ heap memory that Python's garbage
collector cannot track. A worker that solves 10 clusters in sequence accumulates
~1 GB of unreleased C++ memory. With `maxtasksperchild=1`, the OS reclaims the
entire worker process address space after each cluster, guaranteeing memory
returns to baseline regardless of OR-Tools internal allocation patterns.

**AsyncResult.get(timeout)**: the routing library can enter an infinite search
loop on pathological inputs (e.g. a cluster with contradictory time windows). A
per-task timeout kills the stalled worker; the aggregator marks the cluster
infeasible with reason "solver_timeout". Without a timeout, one bad cluster
blocks the entire pool indefinitely.

## Consequences

- Worker startup cost (Python interpreter init + module imports) is paid once
  per cluster. At 50 clusters this adds ~5 seconds of overhead. Acceptable for
  a POC where solver runtime dominates.
- All worker arguments must be picklable (see ADR-006). `spawn` enforces this
  more strictly than `fork` — a pickling failure that works by accident under
  `fork` will fail immediately under `spawn`.
- `maxtasksperchild=1` effectively means each cluster spawns a fresh process.
  If cluster count greatly exceeds CPU count, startup overhead accumulates. The
  current target (50 clusters, 8-16 CPUs) is well within acceptable range.
- Workers that exceed the timeout are killed by SIGKILL, not SIGTERM. Cleanup
  callbacks inside the worker do not run. The routing model is not destroyed
  gracefully — this is acceptable since the process is discarded entirely.
