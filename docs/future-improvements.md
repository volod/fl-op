# Future Improvements

These are forward-looking improvements for the current implementation. They are
not compatibility work.

## Solver Quality

- Replace greedy cluster pre-allocation with a small global assignment model for
  scarce vehicles, implements, and operators.
- Add optional Large Neighbourhood Search for high-value clusters after the
  OR-Tools first solution.
- Track held rolling assignments as vehicle time-window constraints so
  incremental replans can safely reuse a held vehicle when there is a real
  non-overlapping gap.

## Snapshot Scale

- Replace the capped materialized bundle list with a lazy bundle index or a
  compact feasibility summary.
- Store bundle-generation diagnostics in the snapshot so downstream consumers
  can tell whether the explanation artifact is complete.

## Data Contracts

- Generate Parquet descriptors and Avro schemas in CI before validation so stale
  generated files cannot pass unnoticed.
- Add contract-level checks that every source file declared in the registry is
  present in generated demo data for each supported physical format.

## Rolling Operations

- Add richer event effects, including partial task completion, operator
  unavailability, depot inventory changes, and weather-window invalidation.
- Add a revision comparison command that explains exactly why every changed
  assignment moved.

## Performance

- Cache compatibility matrices by dataset hash.
- Add process-pool sizing based on measured per-cluster memory instead of CPU
  count alone.
- Record per-cluster solve quality and timeout diagnostics in machine-readable
  artifacts.
