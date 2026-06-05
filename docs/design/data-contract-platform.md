# Data-Contract + Solver-Neutral Planning Platform

This document describes the declarative data-contract layer added on top of the
original fl-op solver. It implements a focused vertical slice of
`docs/specs/shema.md`: a declarative contract + optimization definition drives a
source-to-canonical mapping into an immutable planning snapshot, which is then
optimized in both batch (periodic) and stream (rolling) mode.

## Motivation

Building an optimization model is the easy part; the hard, valuable part is the
data and workflow: mapping messy source semantics into a stable mathematical
abstraction, governing schema/quality/lineage/versioning, and running the same
canonical state in batch and near-real-time. The platform makes the source
vocabulary irrelevant - `tractor`, `sprayer`, and `operator` all become `Asset`
with `roles` and `Capability` values - so the solver reasons about semantics,
not field names.

## Layered architecture

```text
            source CSV / weather.json / events.jsonl
                              |
   [contracts]  Avro (.avsc) + ODCS (.yaml) + OptimizationProfile
                 x-optimization bindings, dual fingerprints
                              |
   [mapping]    MappingEngine: bindings -> canonical objects
                 unit normalization, missing-value policy -> QualityFindings
                              |
   [canonical]  Asset / Capability / Location / Task / Bundle /
                 Forecast / Commitment / InventoryPosition
                              |
   [snapshot]   SnapshotBuilder: immutable PlanningSnapshot
                 reproducible hash + solver_payload bridge
                              |
        +---------------------+----------------------+
        |                                            |
   [adapter: periodic]                        [adapter: rolling]
   OrToolsPeriodicAdapter                     OrToolsRollingAdapter
   (wraps solve chain)                        (freeze + revisions)
        |                                            |
        +---------------------+----------------------+
                              |
   [plan]       canonical Plan / PlanRevision
                 assignments, unassigned (reason codes), score, risk
```

Each layer is a package under `src/fl_op/`: `contracts/`, `mapping/`,
`canonical/`, `snapshot/`, `adapters/`, `stream/`, and `planning/`
(CLI orchestration).

## Key invariants

- **No raw-data optimization** (spec 4.3). Adapters consume only the snapshot.
  The `solver_payload` bridge is reconstructed from canonical objects via the
  same contract bindings (ADR-020), so the solver sees the validated, normalized
  projection of the snapshot.
- **Dual fingerprints** (ADR-017). `avroParsingFingerprint` tracks serialization
  structure; `optimizationMetadataHash` tracks optimization semantics. A unit or
  binding change moves only the metadata hash. Registration fails on undetected
  metadata loss.
- **Reproducible snapshots** (ADR-018). Same source + effective timestamp +
  version dimensions => same `snapshot_hash`. Per-run identifiers and the bridge
  payload are excluded from the hash.
- **Immutable plan revisions** (ADR-019). Rolling replanning freezes started and
  imminent tasks (preserved byte-identically) and emits a new revision linked to
  its parent, with a plan-instability penalty on post-freeze changes.

## CLI surface

```text
fl-op contracts validate [--write]      # round-trip + dual fingerprints + ODCS/Avro check
fl-op snapshot build --data latest --mode periodic|rolling
fl-op plan periodic --data latest       # batch OR-Tools plan
fl-op plan rolling  --data latest --events events.jsonl
fl-op demo --data latest                # full contract -> batch + stream story
```

The legacy commands (`generate-data`, `solve`, `analyse`, `reschedule`,
`query-contract`) are retained; `solve`/`reschedule` now delegate to the shared
`solver/chain.py` helper, so the solver internals have a single call site used by
both the legacy pipelines and the new adapters.

## What is intentionally out of scope for the slice

asset-relationship contracts (compatibility is computed by the existing
`compat_matrix`), operator-shift/cost-norm/manual-override contracts, an external
schema-registry service, a Timefold adapter (declared in the profile, satisfied
by the OR-Tools rolling adapter), and the full 16-component service tree. These
are declared or stubbed and can be added behind the same SPI without disturbing
the layers above.
