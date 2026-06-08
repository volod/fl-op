# Current Implementation

This document describes the implementation as it exists now. It intentionally
omits release history and decision-record narrative.

## Data and Contracts

`fl-op generate-data` writes one timestamped dataset under
`$DATA_DIR/generate-data/<timestamp>/`. Tabular datasets are written as Avro by
default, or as CSV/Parquet when `--format` is supplied. `metadata.json` records
the selected `run_metadata.data_format`, and downstream commands require that
metadata so the physical codec is explicit.

ODCS contracts in `contracts/odcs/` are the source of semantic truth. They carry
`xOptimization` bindings, canonical units, planning uses, missing-value policy,
and schema-generation hints. Avro, Protobuf, Elasticsearch, and Parquet
descriptors are generated from ODCS into `contracts/generated/`; generated
schemas are structural and do not embed optimization metadata.

## Planning Pipeline

The end-to-end demo executes this sequence:

1. Validate contracts and fingerprints with `fl-op contracts validate`.
2. Map source rows into canonical assets, locations, tasks, forecasts, and
   operational bundles.
3. Build an immutable planning snapshot with a reproducible hash and a
   `solver_payload` projection.
4. Run the periodic OR-Tools adapter against the snapshot payload.
5. Synthesize execution events and run rolling dispatch revisions.

The canonical snapshot materializes operational bundles for inspection and
explanation, capped by `BUNDLE_GENERATION_CAP`. Current OR-Tools adapters solve
from the validated `solver_payload`, so the bundle cap does not change current
assignment results. It is still a snapshot artifact limitation.

## Solver Algorithm

The solver chain is shared by CLI batch solving and canonical adapters:

1. Build a NumPy vehicle-implement compatibility matrix from power feasibility.
2. Filter candidates per order by operation type.
3. Assign orders to nearest depots and split large depot groups into clusters.
4. Pre-allocate vehicles, implements, and operators to clusters by penalty
   priority.
5. Build a greedy margin-based warm start.
6. Solve each cluster as an OR-Tools routing problem in a spawned process pool.
7. Aggregate dispatch packages, canonical infeasibility reason codes, KPIs, and
   reports.

## Rolling Dispatch

Rolling dispatch reuses the same solver chain on a filtered payload:

- Started tasks and tasks inside the freeze window are frozen.
- Assignments whose task and assets still exist are carried forward unchanged.
- Tasks affected by new or unavailable assets are re-solved.
- Resources held by frozen/carried assignments are excluded from the incremental
  re-solve to avoid double-booking.
- Each event produces an immutable plan revision with churn and plan-instability
  metrics.

## Run-Log Assessment

For the supplied `make demo FORMAT=parquet` run on June 8, 2026, the batch path
was correct and internally consistent:

- Contract validation passed for all 8 contracts with generated Avro structural
  fingerprints and ODCS metadata hashes.
- Snapshot building mapped all source rows with zero exclusions.
- Periodic planning assigned all 250 tasks and reported a solver margin
  improvement of `143414.76 EUR` over the greedy baseline.
- Rolling dispatch produced 3 revisions: baseline, `task.started`, and
  `asset.unavailable`.
- The supplied log showed the final rolling revision preserving 8 frozen
  assignments, carrying 240 forward, and re-solving the 2 assignments directly
  affected by the unavailable asset.

During review, the rolling compiler was tightened: resources held by frozen or
carried-forward work are now excluded from the incremental re-solve. The
verified current run still assigns all 250 tasks, and its final rolling revision
preserves 8 frozen assignments, carries 239 forward, and re-solves 3 tasks. That
extra re-solved task is the conservative cost of preventing a filtered re-solve
from double-booking a held vehicle.

The other correctness note from the log is the capped `2000` bundle artifact.
The current solver result is not affected because adapters consume
`solver_payload`, but a future adapter that consumes canonical bundles directly
must remove or replace that cap.
