# Domain mapping packs

A domain mapping pack projects a concrete physical schema (agricultural,
construction, marine, ...) onto the [canonical optimization model](canonical-model.md).
Each pack lives under `contracts/domains/<domain>/`:

```
contracts/domains/agricultural/
  odcs/        vehicles.odcs.yaml  implements.odcs.yaml  ...   # pure physical schema
  mappings/    vehicles.mapping.yaml  implements.mapping.yaml  ...   # physical -> canonical
  profile.yaml                                                 # OptimizationProfile
```

The split is deliberate:

- **Physical ODCS** (`odcs/`) describes only the raw schema (field names, types,
  Avro/Proto/ES/Parquet generation hints). It carries **no** canonical bindings,
  so the physical contract and its schema generation stay independent of
  optimization semantics.
- **Mapping document** (`mappings/<contract>.mapping.yaml`, `kind: CanonicalMapping`)
  declares how each physical field projects onto a canonical binding. This is the
  authority for all semantic bindings.

## Mapping document shape

```yaml
apiVersion: x-optimization/v0.1.0
kind: CanonicalMapping
metadata:
  domain: agricultural
  sourceContract: vehicles          # physical ODCS contract id
  canonicalEntity: asset            # target canonical entity
  assetRole: mobile-prime-mover     # role within the entity (assets only)
  canonicalModelRef: urn:xopt:model:canonical:0.1.0
  mappingVersion: 1.0.0
fieldMappings:
  - sourceField: rated_power_kw     # physical column
    binding: asset.capabilities.ratedPower   # canonical binding (must be declared)
    semanticTerm: urn:xopt:capability:rated-power   # must be in the vocabulary
    canonicalUnit: kW
    quantityKind: power
    planningUse: [capacity, compatibility-filter]
    missingValuePolicy: reject-for-planning
```

The loader `fl_op/contracts/mapping_loader.py` (`load_mapping`) parses these into
the same `FieldBinding` shape the mapping engine consumes. The registry exposes
`FileRegistry.get_mapping(contract_id)`; `fl_op/mapping/bindings.py`
(`load_binding_table`) sources its bindings from the mapping document.

Use `missingValuePolicy: accept-optional` for fields that are optional by
design (for example an observation row carries either a numeric `value` or a
categorical `state_value`): the field is skipped silently, without a quality
finding and without dropping the row.

Observation mappings may declare a `metricCodes` table in their metadata to
normalize raw source metric vocabularies onto the canonical metric codes the
engine's monitoring policy interprets; unmapped codes pass through unchanged
(retained for analysis, not interpreted):

```yaml
metadata:
  canonicalEntity: observation
  metricCodes:
    battery_pct: battery-level
    health_state: health-status
```

## Adaptive dataset discovery

Which datasets feed a snapshot is derived from the registry, not hardcoded: the
snapshot builder maps every active-domain contract whose mapping targets a
snapshot-input canonical entity (`asset`, `location`, `task`, `forecast`,
`observation`, `commitment`, `travel-link`, `cost-rate`), in registry
declaration order. Adding a dataset to
a domain therefore means adding the ODCS + mapping + registry entry; the engine
picks it up automatically. The same holds in the stream layer: execution events
resolve their target collection and key column from the mapping documents
(canonical entity + identity binding), so `task.started`, `asset.unavailable`,
or `observation.recorded` work for any domain without column-name knowledge.

## Extra (analytical) fields

A physical ODCS schema may declare **more fields than the optimizer needs**.
Anything not bound by the mapping is retained in the physical schema and the
generated formats - persisted, read, and available for analysis - but ignored by
the engine. This lets a domain contract describe real datasets faithfully
(registration numbers, telematics ids, manufacture years, ...) without coupling
the optimizer to them. Example: the agricultural `vehicles` schema declares
`manufacture_year` and `telematics_unit_id`, which are not in
`vehicles.mapping.yaml`.

`fl-op contracts validate-domain --domain <d>` reports, per contract, how many
physical fields are **optimization-mapped** vs **extra (analytical)**, and lists
the extra ones:

```
contract           entity      optimization / extra (analytical) physical fields
vehicles           asset       9 optimization, 2 extra: ['manufacture_year', 'telematics_unit_id']
```

So you can both check that a domain covers the canonical optimization contract
and see which additional fields it carries for further analysis.

## Fingerprints

`registry.yaml` stores two fingerprints per contract:

- `avroParsingFingerprint` - structural, from the generated Avro schema;
- `optimizationMetadataHash` - semantic, computed from the **mapping document**
  (record-level metadata + field mappings) via
  `fl_op/contracts/fingerprint.py:mapping_metadata_hash`.

The metadata-loss guard (`FileRegistry.verify_no_metadata_loss`) fails the suite
if a stored `optimizationMetadataHash` diverges from the recomputed one; re-stamp
with `fl-op contracts validate --write`.

## Adding a new domain (worked example: construction)

`contracts/domains/construction/` maps a different physical schema onto the
**same** canonical model with no engine changes -- and is fully runnable:
`fl-op generate-data --domain construction` produces a conforming dataset and
`ACTIVE_DOMAIN=construction fl-op plan periodic --data latest` plans it
through the identical pipeline:

| Construction physical | Canonical entity / role | Reuses agricultural binding |
|---|---|---|
| `machines` (excavators, loaders) | `asset` / `mobile-prime-mover` | `asset.capabilities.ratedPower`, `...travelSpeed`, ... |
| `attachments` (buckets, breakers) | `asset` / `implement` | `asset.capabilities.requiredPower`, `...workingWidth`, ... |
| `operators` | `asset` / `operator` | `asset.availability.shift*`, `...certifiedOperations` |
| `yards` | `location` / `depot` | `location.inventory.fuel` |
| `sites` | `location` | `location.lat/lon/areaHa/soilType` |
| `jobs` | `task` | `task.operationType`, `task.revenueValue`, ... |

To add a domain:

1. Author the physical ODCS schema under `contracts/domains/<domain>/odcs/`.
2. Author one `*.mapping.yaml` per contract under `mappings/`, binding each
   physical field to a declared canonical binding + known semantic term. Add a
   new vocabulary entry to `contracts/canonical/model.yaml` only if a genuinely
   new meaning is needed.
3. Register the domain in `contracts/registry.yaml` under `domains:` (with its
   `mappings:` list for a validation-level pack) and add a `profile.yaml`.
4. Validate: `fl-op contracts validate-domain --domain <domain>` (for the
   construction pack: `make validate-construction`). This asserts the pack maps
   completely onto the canonical model.
5. To make the pack runnable, register its contracts under `contracts:`
   (contract ids are a global namespace -- construction registers its
   operator master as `construction-operators`), register the profile under
   `profiles:`, provide source data (or a generator), and select the domain
   at run time with `ACTIVE_DOMAIN=<domain>`. The engine needs no change:
   solver inputs resolve binding tables by canonical entity and asset role.

`contracts/domains/roadside/` is the validation-level example of a
monitoring-driven domain: stationary signage/sensor assets along road
segments, lane-closure curfews as restriction windows, and inspection rounds
as the observation source feeding derived service visits.
