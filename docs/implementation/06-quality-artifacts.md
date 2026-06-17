[Implementation guide](../current-implementation.md) > Quality and completeness artifacts

# Quality and completeness artifacts

- The snapshot carries a compact, exact bundle feasibility summary
  (`snapshot.bundle_summary`): feasible pair counts over the full
  prime-mover x related-equipment cross product, per-operation pair counts,
  and unmatched-resource counts, computed vectorised so the artifact stays
  constant-size at any fleet scale. It also carries the demand side: task
  counts per demanded operation type (including derived service tasks) and
  `scarce_operations`, the demanded operations whose feasible-pair supply is
  below the task count. Concrete bundles are enumerated lazily
  on demand (`snapshot/bundles.py:iter_bundles`), never materialized into
  the snapshot. The solver does its own compatibility filtering, so both are
  explanation artifacts, not assignment inputs.
- A mapped contract whose declared source file is absent from the data
  directory yields a `dq://dataset/source-file-missing` warning finding on the
  snapshot, so an incomplete entity set is visible instead of silent.
- Observation assessment emits `dq://observation/outlier`,
  `dq://observation/sensor-fault`, `dq://observation/metric-drift`,
  `dq://observation/source-flagged`, `dq://observation/future-timestamp`, and
  `dq://observation/timestamp-regression` findings; surviving readings carry a
  confidence and `quality_summary.observation_error_rates` records the share
  of bad readings per source contract.
- `snapshot.source_watermarks` records the newest trusted observed time per
  source contract: what arrived later belongs to the next revision, and
  consumers can tell stale visibility from a quiet world. Observation
  watermarks come from the assessed readings; task/asset/location/forecast
  sources mutated by execution events get theirs from the event applicator
  (the newest applied event's observed time per contract), merged at
  snapshot build with the newest time winning.
- Dataset builds append their error rates to
  `$DATA_DIR/quality/observation-error-rates.jsonl`; a source whose rate
  strictly increases over the last recorded runs is reported as degrading.
  The trend file itself is retained: past QUALITY_TREND_MAX_RECORDS records
  it is compacted in place to the newest records (atomic replace).
</content>
