# Current Implementation

How the system works today. For the contract layer see
[canonical-model.md](reference/canonical-model.md) and
[domain-mapping.md](reference/domain-mapping.md).

## Three layers

1. **Canonical optimization model** (`contracts/canonical/`) - the domain-neutral
   entity / capability / semantic-term contract the engine consumes.
2. **Domain mapping packs** (`contracts/domains/<domain>/`) - a pure physical ODCS
   schema, separate `*.mapping.yaml` projections onto the canonical model, and an
   optimization profile. Physical schemas may carry extra real-data fields beyond
   what the optimizer needs; those are retained for analysis and ignored by the
   engine.
3. **Engine** (`src/fl_op/{snapshot,solver,adapters}`) - consumes canonical
   entities only; no dependency on any domain model layer.

## Data and contracts

`fl-op generate-data` writes one timestamped dataset under
`$DATA_DIR/generate-data/<timestamp>/` (Avro by default; CSV/Parquet via
`--format`). `metadata.json` records the chosen format so downstream commands use
the right codec.

Physical schemas (Avro/Protobuf/Elasticsearch/Parquet) are generated from the
physical ODCS contracts into `contracts/generated/` (gitignored). Generated
schemas are structural only - they carry no optimization metadata.

`fl-op contracts validate` checks: generated-schema structural fingerprints, the
canonical model, and per-domain **mapping completeness** (every mapping binds only
to declared canonical fields + known terms, and covers every required canonical
binding). `fl-op contracts validate-domain --domain <d>` additionally reports each
contract's optimization-mapped vs extra (analytical) physical fields.

## Planning pipeline

1. Validate contracts (`fl-op contracts validate`).
2. Map source rows into canonical assets, locations, tasks, forecasts, and
   operational bundles.
3. Build an immutable, reproducibly-hashed `PlanningSnapshot` (purely canonical).
4. An adapter projects the snapshot into canonical solver rows
   (`solver/inputs.py`) and runs the OR-Tools solver chain.
5. Synthesize execution events and run rolling-dispatch revisions.

## Solver chain

Shared by batch `solve` and the canonical adapters; it operates on canonical
solver rows (keyed by `asset_id`, `rated_power`, `task_id`, ...):

1. Build a prime-mover / related-equipment compatibility matrix from power
   capabilities (`solver/feasibility.py`).
2. Filter candidates per task by operation type.
3. Cluster tasks by nearest depot; split large groups.
4. Pre-allocate prime movers, related equipment, and operators by penalty
   priority.
5. Build a greedy margin-based warm start.
6. Solve each cluster as an OR-Tools routing problem in a spawned process pool.
7. Aggregate dispatch packages, canonical reason codes, KPIs, and reports.

## Rolling dispatch

Reuses the solver chain on a filtered canonical payload:

- Started tasks and tasks inside the freeze window are frozen.
- Assignments whose task and assets still exist are carried forward unchanged.
- Tasks affected by new or unavailable assets are re-solved; resources held by
  frozen/carried assignments are excluded from the re-solve to avoid
  double-booking.
- Each event yields an immutable plan revision with churn and plan-instability
  metrics.

## Known artifact limitation

The snapshot materializes operational bundles for inspection/explanation, capped
by `BUNDLE_GENERATION_CAP`. The solver does its own compatibility filtering, so
the cap bounds only the snapshot artifact, not assignment results.
