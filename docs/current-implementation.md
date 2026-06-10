# Current Implementation

How the system works today. For the contract layer see
[canonical-model.md](reference/canonical-model.md) and
[domain-mapping.md](reference/domain-mapping.md); for the entity ontology, use
cases, and algorithm overview see
[optimization-ontology.md](reference/optimization-ontology.md); for why and how
the system survives the gap between its entity model and the physical world see
[model-world-divergence.md](reference/model-world-divergence.md).

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
2. Map source rows into canonical assets, locations, tasks, forecasts,
   observations, commitments, and operational bundles. Which datasets are
   mapped is derived from the registry (active domain + mapping entity), and
   entity dispatch is a registered emitter table
   (`mapping/builders.py:ENTITY_EMITTERS`), so new datasets and entities plug
   in without engine changes.
3. Statistically assess observation series (`snapshot/assessment.py`):
   order each series by observed time (never arrival order), flag
   arrival-order timestamp regressions, exclude readings claiming times beyond
   the clock-skew tolerance ahead of planning time, bound the series by the
   retention window and aggregate over-long histories into time windows
   (endpoints preserved), exclude readings flagged bad by their source and
   outliers (MAD-based modified z-score), floor the confidence of
   fault-suspected series (battery rising without service, frozen non-zero
   values), detect metric drift on non-trending metrics, and aggregate
   per-source error rates into the quality summary. Source quality flags fold
   into per-reading confidence. Per-source watermarks (the newest trusted
   observed time per contract) are stamped onto the snapshot
   (`source_watermarks`). Degraded sources are reported per build and trended
   across runs (`snapshot/quality_trend.py`).
4. Apply the stationary-equipment monitoring policy
   (`snapshot/monitoring.py`): stationary assets (sensor stations, fixed
   road/field equipment) with low battery, a battery drain trend projected
   below threshold within the forecast horizon, degraded health, an overdue
   service interval, a drifting metric (calibration), or a low composite
   health score (weighted battery/health/service-due/drift signals) yield
   canonical service tasks anchored at their home location. Readings below
   the policy's minimum confidence are ignored. Thresholds and task
   attributes come from the profile's `monitoring` section, with
   constant-backed defaults and per-asset-type overrides
   (`assetTypeOverrides`). Observation metric codes are normalized from raw
   source vocabularies via the mapping document's `metricCodes` table.
5. Build an immutable, reproducibly-hashed `PlanningSnapshot` (purely canonical).
6. An adapter projects the snapshot into canonical solver rows
   (`solver/inputs.py`) and runs the OR-Tools solver chain; derived service
   tasks are dispatched alongside ordered work.
7. Synthesize execution events and run rolling-dispatch revisions.

## Solver chain

Shared by batch `solve` and the canonical adapters; it operates on canonical
solver rows (keyed by `asset_id`, `rated_power`, `task_id`, ...):

1. Enforce the profile's weather-window constraint (`solver/enforcement.py`):
   a weather-sensitive task with no compliant forecast window at its nearest
   forecast location is excluded with `NO_VALID_WEATHER_WINDOW`. Sensitivity
   per operation type and limits come from the profile's `weatherPolicy`.
2. Build a prime-mover / related-equipment compatibility matrix from power
   capabilities (`solver/feasibility.py`).
3. Filter candidates per task by operation type.
4. Cluster tasks by nearest depot; split large groups.
5. Pre-allocate prime movers, related equipment, and operators by penalty
   priority; each cluster gets the operator certified for the most of its
   operation types.
6. Enforce operator qualification (tasks the cluster operator is not
   certified for -> `NO_AVAILABLE_OPERATOR`) and material availability
   (cumulative per-operation demand from the profile's `materialDemand`
   charged against depot inventory, highest penalty first ->
   `INSUFFICIENT_MATERIAL`).
7. Build a greedy margin-based warm start.
8. Solve each cluster as an OR-Tools routing problem in a spawned process pool.
9. Aggregate dispatch packages, canonical reason codes, KPIs, and reports.

Enforcement activates only through the adapters (an `EnforcementPolicy` built
from the profile's enforced constraints); the raw batch `solve` pipeline is
unchanged.

## Rolling dispatch

Event application is binding-driven (`stream/apply.py`): the target source
collection and its key column are resolved from the active domain's mapping
documents (canonical entity + identity binding), so the driver knows no
domain-specific column names. Supported triggers:

- `task.started` / `task.progress`: lifecycle and partial completion; progress
  scales the remaining work quantity down (a fully completed task leaves
  planning), so re-solves dispatch only the remaining effort;
- `order.created` / `order.cancelled`;
- `asset.unavailable`: removes any asset by id -- vehicles, implements,
  operators, and stationary equipment share one path;
- `inventory.adjusted`: partial merge into a location row (depot fuel and
  material balances) without touching its other fields;
- `forecast.updated`: with a payload, upserts the forecast window (weather
  invalidation by data); without one, a pure replan trigger;
- `observation.recorded`: streamed sensor readings upserted by reading id, so
  a re-sent corrected reading replaces the earlier one;
- `entity.corrected`: a corrected source row upserted by its key column, so
  quality-rejected or wrongly-valued entities re-enter planning.

Event application is idempotent by `event-id`: at-least-once delivery may
replay an event, and a replay mutates nothing and produces no revision.
Events whose observed times fall within the convergence window
(`STREAM_CONVERGENCE_WINDOW_S`, default off) coalesce into one rebuild and one
revision, so a partition flushing its backlog converges before replanning.

Reuses the solver chain on a filtered canonical payload:

- Started tasks and tasks inside the freeze window are frozen.
- Assignments whose task and assets still exist are carried forward unchanged.
- Tasks affected by new or unavailable assets are re-solved; resources held by
  frozen/carried assignments are excluded from the re-solve to avoid
  double-booking.
- Each event yields an immutable plan revision with churn and plan-instability
  metrics.
- `fl-op plan diff-revisions` compares consecutive revisions of a rolling run
  and explains why every changed assignment moved (corrective action, trigger,
  freeze, feasibility change, or optimization tradeoff), writing
  `revision_diff.json`/`.txt` under `.data/revision-diff/<ts>/`.

### Corrective rescheduling

Plans survive being wrong (`adapters/rolling/corrective.py`); every self-repair
is recorded as a `CorrectiveAction` on the revision and counted in its score:

- **Asset loss mid-plan**: a frozen (started) or carried assignment whose asset
  disappeared is released and its task re-solved
  (`reassigned-after-asset-loss`), instead of staying bound to a dead bundle.
- **False positive prognosis**: a derived service task no longer justified by
  newer readings is withdrawn (`service-withdrawn`), recording why it was
  derived (previous revision's monitoring reasons) and the contradicting
  current readings.
- **False negative prognosis**: critical battery or failed health derives an
  escalated service task (top priority, one-day deadline); a previously
  non-escalated assignment is forced out of carry-forward and re-solved
  (`service-escalated`).
- **Prognosis accuracy feedback** (`stream/prognosis.py`): every revision
  appends its service-task outcomes to
  `$DATA_DIR/quality/service-prognosis.jsonl`; accumulated false-positive /
  false-negative rates above thresholds log monitoring-policy tuning
  recommendations (recommendations only, never auto-applied).

## Quality and completeness artifacts

- The snapshot materializes operational bundles for inspection/explanation,
  capped by `BUNDLE_GENERATION_CAP`. `snapshot.bundle_diagnostics` records the
  resource counts, the cap, and whether the list was truncated. The solver
  does its own compatibility filtering, so the cap bounds only the snapshot
  artifact, not assignment results.
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
  consumers can tell stale visibility from a quiet world.
- Dataset builds append their error rates to
  `$DATA_DIR/quality/observation-error-rates.jsonl`; a source whose rate
  strictly increases over the last recorded runs is reported as degrading.
