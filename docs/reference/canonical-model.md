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
  `execution-event`), each pointing at an ODCS contract under
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
| `asset` | `asset.assetId` | Prime movers, related equipment, and operators are one entity with distinct roles; capabilities are optional (role-dependent). |
| `location` | `location.locationId`, `location.lat`, `location.lon` | Work sites and depots; depot inventory is canonical `location.inventory.*`. |
| `task` | `task.taskId`, `task.locationRef`, `task.operationType` | Units of work; revenue/penalty are domain-neutral EUR money. |
| `forecast` | `forecast.forecastId` | Environmental forecast windows. |
| `execution-event` | `event.eventId`, `event.eventType`, `event.observedAt`, `event.entityRef` | Rolling-dispatch replanning triggers. |

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
this model, using the agricultural and construction packs as worked examples.
