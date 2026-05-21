# ADR-010: One search worker per cluster solver process

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

OR-Tools routing library (and its CP-SAT sub-solver) supports internal
multi-threading for parallel search. By default it uses all available CPU cores.

When the cluster pool runs one Python process per CPU core (e.g. 8 processes on
an 8-core machine), and each process spawns 8 OR-Tools search threads, the
system runs 64 logical threads on 8 physical cores — 8x over-subscribed. Context
switching overhead degrades all 8 workers simultaneously.

## Decision

Set `search_params.sat_parameters.num_workers = 1` in every cluster solver call.

(In OR-Tools <= 9.9, the field was `search_params.num_search_workers`. In
OR-Tools 9.15+ the routing model uses the CP-SAT sub-solver; the field moved to
`search_params.sat_parameters.num_workers`. The implementation must target the
installed version; see ADR-015 for the version detection pattern.)

## Rationale

With 50 clusters and 8 CPU cores, the optimal parallel strategy is 8 processes
solving different clusters simultaneously, each using 1 core for its OR-Tools
search. This saturates all 8 cores with useful work.

The alternative — 8 OR-Tools threads per process — would cause all 8 workers to
compete for the same 8 cores. Each thread gets 1/8 of a core on average; OR-Tools
convergence is largely single-threaded in its LNS phase anyway, so the extra
threads add synchronisation overhead without adding search throughput.

## Consequences

- Each cluster worker uses exactly 1 CPU core for its OR-Tools search. The
  process itself uses slightly more (Python runtime, numpy, pickling) but the
  search thread count is bounded.
- On machines with many cores (32+), it may be worth running more Pool workers
  than CPU count and accepting slight over-subscription for OR-Tools. This is
  a tunable parameter; the current default is `cpu_count()`.
- The `sat_parameters.num_workers` field only affects the CP-SAT sub-solver.
  If the routing library is configured to use a pure routing search strategy
  (not CP-SAT), this field has no effect and a different threading parameter
  may be needed. Monitor OR-Tools release notes on major version upgrades.
