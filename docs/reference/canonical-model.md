# Canonical optimization model

The canonical model is the **single source of truth for what the optimization
engine consumes**. It is domain-agnostic: the solver never reads a physical
domain schema (agricultural, construction, ...) directly - it only sees canonical
entities built according to the contracts described here.

## Three-layer architecture

```
  Canonical optimization model        contracts/canonical/
    (the minimal contract the engine consumes; domain-neutral)
            ^
            | projected onto by
            |
  Domain mapping packs                contracts/domains/<domain>/
    (physical ODCS schema  +  *.mapping.yaml projections  +  profile.yaml)
            ^
            | reads / generated from
            |
  Physical source data                vehicles.csv, machines.csv, ...
```

1. **Canonical model** (`contracts/canonical/`) declares the entities, fields,
   and semantic-term vocabulary the engine requires.
2. **Domain mapping packs** (`contracts/domains/<domain>/`) hold a *pure* physical
   ODCS schema plus separate mapping documents that project each physical field
   onto a canonical binding. The optimization profile lives here too.
3. The **engine** (`src/fl_op/solver`, `adapters`, `snapshot`) consumes canonical
   `Asset` / `Task` / `Location` / `OperationalBundle` objects and canonical
   solver rows; it has no dependency on any domain model layer.

## What the canonical model declares

`contracts/canonical/model.yaml`:

- `canonicalModelRef: urn:xopt:model:canonical:0.1.0`
- the **entity registry** (`asset`, `location`, `task`, `forecast`,
  `observation`, `commitment`, `execution-event`, `travel-link`, `cost-rate`,
  and the output entity `plan`), each pointing at an ODCS contract under
  `contracts/canonical/odcs/`;
- the **semantic-term vocabulary**: the controlled set of meanings a mapping may
  bind to, each fixing `valueType`, `quantityKind`, and `canonicalUnit`
  (e.g. `urn:xopt:capability:rated-power -> {power, kW}`).

`contracts/canonical/odcs/<entity>.odcs.yaml` are ODCS `DataContract`s. Each
canonical field carries a `canonicalBinding` custom property:

```yaml
- name: ratedPower
  required: false
  customProperties:
    - property: canonicalBinding
      value:
        binding: asset.capabilities.ratedPower
        semanticTerm: urn:xopt:capability:rated-power
        canonicalUnit: kW
        quantityKind: power
        planningUse: [capacity, compatibility-filter]
```

The Python loader `fl_op/contracts/canonical_model.py` (`load_canonical_model`)
flattens these into a `CanonicalModel` exposing `allowed_bindings(entity)`,
`required_bindings(entity)`, and the term vocabulary.

## Canonical entities

| Entity | Required canonical fields | Notes |
|---|---|---|
| `asset` | `asset.assetId` | Prime movers, related equipment, operators, and stationary equipment are one entity with distinct roles. `asset.mobility` separates movable resources from stationary ones (sensors, fixed road/field equipment); `asset.state.*` carries maintenance master data (last service, service interval). Dynamic condition (battery, health) comes exclusively from observations. |
| `location` | `location.locationId`, `location.lat`, `location.lon` | Work sites and depots; depot inventory is canonical `location.inventory.*`. |
| `task` | `task.taskId`, `task.locationRef`, `task.operationType` | Units of work; revenue/penalty are domain-neutral EUR money. |
| `forecast` | `forecast.forecastId` | Environmental forecast windows. |
| `observation` | `observation.observationId`, `observation.entityRef`, `observation.metric`, `observation.observedAt` | A measured value about an entity: sensor reading, telemetry sample, inspection result. One shape covers historical batches and realtime streamed readings; numeric readings bind `observation.value`, categorical readings bind `observation.stateValue`. The `metric` column carries canonical metric codes (`battery-level`, `health-status`, ...). |
| `commitment` | `commitment.commitmentId` | Contractual obligations (deadline, lateness penalty, hardness) for domains that keep them separate from order rows. |
| `execution-event` | `event.eventId`, `event.eventType`, `event.observedAt`, `event.entityRef` | Rolling-dispatch replanning triggers, including `task.progress` (partial completion), `task.completed`, `inventory.adjusted`, `observation.recorded` for streamed readings and telemetry-derived progress, and `entity.corrected` for corrected source rows. |
| `travel-link` | `travelLink.linkId`, `travelLink.fromLocationRef`, `travelLink.toLocationRef`, `travelLink.travelTimeS` | Directed travel-network edges (distance-matrix entries / road-graph arcs); pairs without a link fall back to haversine estimates. |
| `cost-rate` | `costRate.costRateId`, `costRate.rateType`, `costRate.unitPrice`, `costRate.perUnit` | Priced resource rates (fuel, materials) with optional validity windows; engine cost constants are the fallback. |
| `plan` (output) | `plan.planId`, `plan.revisionId`, `plan.snapshotRef`, ... | The plan output contract, mirroring the input contracts; produced plans are validated against it at publication (`fl_op/contracts/plan_contract.py`). |

## Stationary-equipment monitoring

Observation series are first statistically assessed
(`fl_op/snapshot/assessment.py`): series are bounded by a retention window and
downsampled, source-flagged-bad readings and outliers are excluded,
fault-suspected series (battery rising without service, frozen values) are
floored to zero confidence, and drifting metrics are flagged for calibration.
Source quality flags fold into per-reading confidence. The monitoring policy
(`fl_op/snapshot/monitoring.py`) then consumes the assessed series plus the
`asset.state.*` maintenance master data and derives service tasks for
stationary assets that need attention: battery at or below threshold, battery
drain trend projected below threshold within the forecast horizon,
degraded/failed health, an exceeded service interval, a drifting metric, or a
low composite health score combining sub-critical signals. Readings below the
policy's minimum confidence are ignored, and policies resolve per asset type
via the profile's `assetTypeOverrides`. Derived tasks are anchored at the asset's home
location reference and are scheduled by the same solver chain as ordered work,
so a domain only has to declare service-capable assets (for example an
implement whose `compatible_operations` includes the service operation type).
Thresholds and task attributes come from the optimization profile's
`monitoring` section (`MonitoringPolicySpec`), with constant-backed defaults
in `fl_op/core/constants.py`.

## Validation

`fl-op contracts validate` (and `make contracts`) checks:

- the canonical model parses, every declared canonical field references a known
  semantic term, and bindings are unique per entity;
- **mapping completeness**: every domain mapping binds only to declared canonical
  fields + known terms, and the union of a domain's mappings covers every
  *required* canonical binding for each entity it targets.

`fl-op contracts canonical-validate` (`make canonical-validate`) validates only
the canonical model in isolation.

See [domain-mapping.md](domain-mapping.md) for how a physical domain projects onto
this model, using the agricultural and construction packs as worked examples, and
[optimization-ontology.md](optimization-ontology.md) for the full entity
ontology, use-case coverage, and further reading.
