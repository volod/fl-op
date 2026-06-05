# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for fl-op.

Each ADR documents one significant technical decision: the context that forced a
choice, the options considered, the decision made, and the consequences that follow.

ADRs are numbered sequentially and never deleted. Superseded records are marked
with a status of "superseded by ADR-NNN".

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](ADR-001-solver-library.md) | OR-Tools routing library as sole solver | Accepted |
| [ADR-002](ADR-002-compat-matrix-numpy.md) | Compatibility matrix as numpy ndarray | Accepted |
| [ADR-003](ADR-003-haversine-balltree.md) | Haversine BallTree for geographic clustering | Accepted |
| [ADR-004](ADR-004-hierarchical-decomposition.md) | Hierarchical depot-cluster decomposition | Accepted |
| [ADR-005](ADR-005-global-pre-allocation.md) | Global pre-allocation pass before cluster solve | Accepted |
| [ADR-006](ADR-006-worker-plain-dicts.md) | Plain Python dicts across process boundary | Accepted |
| [ADR-007](ADR-007-worker-tuple-return.md) | Worker always returns (dispatch, infeasible) tuple | Accepted |
| [ADR-008](ADR-008-operation-enums.md) | Python Enum for OperationType and ImplementType | Accepted |
| [ADR-009](ADR-009-spawn-pool.md) | multiprocessing spawn pool with maxtasksperchild=1 | Accepted |
| [ADR-010](ADR-010-num-search-workers.md) | num_search_workers=1 per cluster worker | Accepted |
| [ADR-011](ADR-011-pydantic-circular-refs.md) | TYPE_CHECKING + model_rebuild() for circular refs | Accepted |
| [ADR-012](ADR-012-greedy-warm-start.md) | NumPy-vectorized greedy warm-start for OR-Tools | Accepted |
| [ADR-013](ADR-013-typed-dict-pipeline-contracts.md) | TypedDict layer for pipeline contracts | Accepted |
| [ADR-014](ADR-014-penalty-weighted-allocation.md) | Penalty-weighted cluster priority in pre-allocator | Accepted |
| [ADR-015](ADR-015-sklearn-balltree.md) | sklearn.neighbors.BallTree instead of scipy | Accepted |
| [ADR-016](ADR-016-canonical-model-layer.md) | Solver-neutral canonical model with a snapshot seam | Accepted |
| [ADR-017](ADR-017-avro-odcs-contracts-fastavro.md) | Real Avro + ODCS contracts via fastavro, dual fingerprints | Accepted |
| [ADR-018](ADR-018-snapshot-immutability-reproducibility.md) | Immutable, reproducibly-hashed planning snapshots | Accepted |
| [ADR-019](ADR-019-python-native-rolling-ortools.md) | Python-native OR-Tools rolling adapter instead of Timefold | Accepted |
| [ADR-020](ADR-020-snapshot-solver-payload-bridge.md) | Snapshot solver-payload bridge driven by contract bindings | Accepted |
