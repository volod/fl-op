[Implementation guide](../current-implementation.md) > Planning pipeline

# Planning pipeline

1. Validate contracts (`fl-op contracts validate`).
2. Map source rows into canonical assets, locations, tasks, forecasts,
   observations, commitments, travel links, cost rates, and operational
   bundles. Which datasets are mapped is derived from the registry (selected
   domains + mapping entity); domain-local contract aliases are resolved by
   the registry, and entity dispatch is a registered emitter table
   (`mapping/builders.py:ENTITY_EMITTERS`), so new datasets and entities plug
   in without engine changes. Source values are normalized to the canonical
   unit declared in each binding through a controlled unit vocabulary with
   conversions (`mapping/units.py:convert_to_canonical`, e.g. W<->kW, g<->kg,
   mL<->L, m2<->ha), so compatible units are reconciled rather than matched by
   exact unit-code string equality; an undeclared conversion fails loudly
   (`UnitConversionError`).
3. Statistically assess observation series (`snapshot/assessment.py`):
   order each series by observed time (never arrival order), flag
   arrival-order timestamp regressions (arrival order is the explicit
   `ingested-at` timestamps when the whole series carries them -- exact
   across restarts -- with source row order as the legacy fallback),
   exclude readings claiming times beyond
   the clock-skew tolerance ahead of planning time, bound the series by the
   retention window and aggregate over-long histories into time windows
   (endpoints preserved; each window representative carries min/mean/max and
   reading-count aggregates so spikes survive downsampling), exclude
   readings flagged bad by their source and
   outliers (MAD-based modified z-score), floor the confidence of
   fault-suspected series (battery rising without service, frozen non-zero
   values), detect metric drift on non-trending metrics, and aggregate
   per-source error rates into the quality summary. Source quality flags fold
   into per-reading confidence. Per-source watermarks (the newest trusted
   observed time per contract) are stamped onto the snapshot
   (`source_watermarks`). Degraded sources are reported per build and trended
   across runs (`snapshot/quality_trend.py`).
4. Apply the equipment monitoring policy
   (`snapshot/monitoring.py`): assets with low battery, a battery drain trend
   projected below threshold within the forecast horizon, degraded health, an
   overdue service interval, a drifting metric (calibration), or a low composite
   health score (weighted battery/health/service-due/drift signals; the
   weights and headrooms are profile-tunable next to the thresholds) yield
   canonical service tasks anchored at their home location. Stationary equipment
   (sensor stations, fixed road/field equipment) is always covered; mobile
   assets (prime movers, drones) are covered when the effective policy sets
   `monitorMobileAssets` (globally or per asset type), so predictive maintenance
   can extend to the fleet without disturbing domains that only monitor fixed
   equipment. Readings below the policy's minimum confidence are ignored.
   Thresholds and task attributes come from the profile's `monitoring` section,
   with constant-backed defaults, per-asset-type overrides
   (`assetTypeOverrides`), and instance-level overrides by asset id
   (`assetOverrides`, a single critical station) layered on top; the
   guarded auto-tuning overlay (see corrective rescheduling) layers above
   the reviewed profile.
   Observation metric codes are normalized from raw
   source vocabularies via the mapping document's `metricCodes` table.
5. Build an immutable, reproducibly-hashed `PlanningSnapshot` (purely canonical).
6. An adapter projects the snapshot into canonical solver rows
   (`solver/inputs.py`) and runs the OR-Tools solver chain; derived service
   tasks are dispatched alongside ordered work. Projection is demand-driven
   over the selected domain set: each section unions binding tables by
   canonical entity and asset role, skips missing optional values so solver
   row defaults survive, and still emits the same domain-neutral row types.
7. Validate every published plan against the canonical plan output contract
   (`contracts/canonical/odcs/plan.odcs.yaml`, enforced by
   `contracts/plan_contract.py`): a plan whose required bindings do not
   resolve fails publication instead of writing a non-conforming artifact.
8. Synthesize execution events and run rolling-dispatch revisions.
</content>
