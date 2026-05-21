# ADR-002: Compatibility matrix as numpy ndarray

Date: 2026-05-21
Status: Accepted
Deciders: Volodymyr Lazurenko, gstack /plan-eng-review

## Context

Each of 3000 vehicles must be checked for compatibility with each of 20000
implements before any order can be assigned. Compatibility is determined by:
- Power margin: vehicle rated_power_kw vs. implement required_power_kw
- Hitch type, PTO, hydraulics, ISOBUS (grouped into a single bool per V-I pair)

At full scale this is 3000 x 20000 = 60 million pairs.

Two storage approaches were considered:

- **Python dict**: `dict[(vehicle_id, implement_id), bool]`. At 60M entries with
  string keys (avg 20 chars), memory is approximately 12 GB. Lookup is O(1) but
  the constant factor is large for hash computation on string keys.
- **numpy bool ndarray**: shape (N_vehicles, N_implements). At dtype=bool each
  element is 1 byte; 60M entries = ~60 MB. Power margin stored in a companion
  float32 array of the same shape = ~240 MB. Total ~300 MB, well within RAM.
  Vectorised lookup via row/column indexing in C; no Python object overhead.

## Decision

Store the compatibility matrix as a **numpy.ndarray(dtype=bool)** of shape
(N_vehicles, N_implements), with a companion **float32** ndarray of the same
shape storing the power margin percentage. Serialise both as `.npy` files;
worker processes load via `np.load(mmap_mode='r')` for zero-copy read access.

## Rationale

A Python dict of 60M (str, str) -> bool pairs exceeds 12 GB before Python object
overhead is counted. numpy bool at the same dimensions is ~60 MB. The ratio is
~200:1 in favour of numpy.

Memory-mapped loading (`mmap_mode='r'`) means workers read the same physical
pages without each worker pickling and re-transmitting the matrix via the
multiprocessing pipe. At 100+ worker processes this saves hundreds of GB of
inter-process data transfer.

Vectorised numpy indexing (`compat[v_indices, i_indices]`) evaluates millions
of pair checks in a single C kernel call, which is 10-100x faster than a Python
loop over a dict.

## Consequences

- Vehicle and implement lists must be assigned stable integer indices before
  matrix construction. Index maps (`vehicle_index`, `implement_index`) must be
  passed alongside the matrix.
- The matrix is computed once at the start of each `solve` run and invalidated
  if the fleet definition changes. Workers reload it from disk on each spawn.
- If `mmap_mode='r'` is unavailable (e.g. unusual filesystem), fall back to
  `np.load()` without mmap; correctness is preserved at the cost of higher
  per-worker memory.
- Power margin and compat arrays must be kept in sync; they are always written
  and loaded together.
