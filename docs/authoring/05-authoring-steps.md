[Authoring manual](../authoring-domain-contracts.md) > 5. Phase C: step-by-step authoring

# 5. Phase C: step-by-step authoring

Each step below produces a concrete artifact and ends with the command that
checks it. Work top to bottom; the validation ladder catches mistakes early.

## Step 1: scaffold the pack

```
contracts/domains/utilities/
  odcs/        # one <name>.odcs.yaml per source dataset (physical schema)
  mappings/    # one <name>.mapping.yaml per dataset (physical -> canonical)
  profile.yaml # the OptimizationProfile
```

Tip: copy the structurally closest shipped pack and edit. For a
dispatch-plus-monitoring domain like `utilities`, the `agricultural` pack (which
has vehicles/implements/operators/depots/fields/orders + sensors + readings +
weather + prices) is the best template.

## Step 2: author the physical ODCS schemas

One file per dataset. Pure schema, no canonical bindings. Declare field names,
types, `required`, and `schemaGeneration` hints (Avro/Proto/ES naming + per-field
generation hints such as Avro defaults and Proto field numbers). You may declare
**more fields than the optimizer needs**: anything not bound by the mapping is
persisted and available for analysis but ignored by the engine (the agricultural
`vehicles` schema does this with `manufacture_year` and `telematics_unit_id`).

Example, `odcs/service-trucks.odcs.yaml` (abridged):

```yaml
apiVersion: v3.0.0
kind: DataContract
id: service-trucks
version: 1.0.0
status: active
name: Service Truck Asset Master
domain: utilities
tenant: fleet-ops
description:
  purpose: Stable characteristics of mobile prime-mover service trucks.
  usage: Bundle generation, compatibility filtering, repositioning cost.
servers:
  - server: batch
    type: local
    format: csv
    path: service-trucks.csv
schema:
  - name: service_trucks
    logicalType: object
    physicalType: csv
    customProperties:
      - property: schemaGeneration
        value:
          avro: {namespace: org.example.util.assets, recordName: ServiceTruck}
          proto: {package: org.example.util.assets, messageName: ServiceTruck, syntax: proto3}
          es: {indexName: service_trucks, dynamic: strict}
    properties:
      - name: truck_id
        logicalType: string
        physicalType: string
        required: true
        description: Stable identifier of the truck.
        customProperties:
          - property: fieldGeneration
            value: {proto: {fieldNumber: 1}}
      - name: truck_type
        logicalType: string
        physicalType: string
        required: true
        description: Source truck category.
        customProperties:
          - property: fieldGeneration
            value: {proto: {fieldNumber: 2}}
      - name: rated_power_kw
        logicalType: number
        physicalType: double
        required: true
        description: Nominal rated continuous engine power.
        customProperties:
          - property: fieldGeneration
            value: {proto: {fieldNumber: 3}}
      - name: fuel_consumption_l_per_h
        logicalType: number
        physicalType: double
        required: true
        description: Average fuel consumption at working load.
        customProperties:
          - property: fieldGeneration
            value: {proto: {fieldNumber: 4}}
      - name: current_lat
        logicalType: number
        physicalType: double
        required: true
        customProperties: [{property: fieldGeneration, value: {proto: {fieldNumber: 5}}}]
      - name: current_lon
        logicalType: number
        physicalType: double
        required: true
        customProperties: [{property: fieldGeneration, value: {proto: {fieldNumber: 6}}}]
      - name: depot_id
        logicalType: string
        physicalType: string
        required: true
        customProperties: [{property: fieldGeneration, value: {proto: {fieldNumber: 7}}}]
```

Repeat for `cutter-heads`, `crews`, `yards`, `spans`, `clearing-jobs`, and any
optional datasets. Use the canonical ODCS contracts under
`contracts/canonical/odcs/` to see exactly which canonical fields exist per
entity (field names, units, required flags).

## Step 3: author the canonical mappings

One `mappings/<name>.mapping.yaml` per ODCS contract. This is the semantic core.
Shape:

```yaml
apiVersion: x-optimization/v0.1.0
kind: CanonicalMapping
metadata:
  domain: utilities
  sourceContract: service-trucks       # must equal the ODCS contract id
  canonicalEntity: asset               # the target canonical entity
  assetRole: mobile-prime-mover        # role within the entity (assets only)
  canonicalModelRef: urn:xopt:model:canonical:0.1.0
  mappingVersion: 1.0.0
  dataProductRole: assetMaster         # free-form label
  permittedPlanningUses: [periodic-planning, rolling-dispatch]
fieldMappings:
  - sourceField: truck_id
    binding: asset.assetId
    semanticTerm: urn:xopt:identity:asset-id
    planningUse: [identity]
  - sourceField: truck_type
    binding: asset.assetType
    semanticTerm: urn:xopt:attribute:asset-type
    planningUse: [classification]
  - sourceField: rated_power_kw
    binding: asset.capabilities.ratedPower
    semanticTerm: urn:xopt:capability:rated-power
    canonicalUnit: kW
    quantityKind: power
    planningUse: [capacity, compatibility-filter]
    missingValuePolicy: reject-for-planning
  - sourceField: fuel_consumption_l_per_h
    binding: asset.capabilities.fuelConsumptionRate
    semanticTerm: urn:xopt:capability:fuel-consumption-rate
    canonicalUnit: L/h
    quantityKind: flow-rate
    planningUse: [cost]
    missingValuePolicy: accept-with-warning
  - sourceField: current_lat
    binding: asset.location.lat
    semanticTerm: urn:xopt:attribute:latitude
    canonicalUnit: deg
    quantityKind: angle
    planningUse: [geospatial]
    missingValuePolicy: reject-for-planning
  - sourceField: current_lon
    binding: asset.location.lon
    semanticTerm: urn:xopt:attribute:longitude
    canonicalUnit: deg
    quantityKind: angle
    planningUse: [geospatial]
    missingValuePolicy: reject-for-planning
  - sourceField: depot_id
    binding: asset.homeDepotRef
    semanticTerm: urn:xopt:relationship:home-depot
    planningUse: [assignment]
```

Authoring rules:

- **`binding` must be a declared canonical field.** Cross-check against the
  entity's canonical ODCS contract; an undeclared binding fails validation.
- **`semanticTerm` must be in the vocabulary** (`contracts/canonical/model.yaml`).
- **`canonicalUnit` must match the term's canonical unit.** Convert at the
  mapping boundary if your source unit differs; the engine assumes canonical
  units downstream.
- **Cover every required binding** for the entity (see [4.1](04-feasibility-study.md#41-ontology-fit-checklist)).
- **Choose a `missingValuePolicy`** per field (see [9.1](09-reference-tables.md#91-missingvaluepolicy-values)).
  Use `accept-optional` for genuinely-optional fields (it skips silently with no
  finding) - this is also how an observation row carries *either* a numeric
  `value` *or* a categorical `stateValue`.
- **Observation mappings** may declare a `metricCodes` table in `metadata` to
  normalize raw metric names onto the canonical codes the monitoring policy
  interprets (`battery-level`, `health-status`); unmapped codes pass through
  unchanged.

For a stationary monitored asset (`pole-sensors`), set `mobility` to a stationary
value via the `asset.mobility` binding and map `asset.state.lastServiceAt` /
`asset.state.serviceInterval` (maintenance master data) plus the home location.
Dynamic condition (battery, health) is **not** an asset field; it arrives only
through `observation` rows.

## Step 4: author the optimization profile

`profile.yaml` (`kind: OptimizationProfile`) declares the per-domain policy. The
key blocks, using the agricultural profile as the reference shape:

```yaml
apiVersion: x-optimization/v0.1.0
kind: OptimizationProfile
metadata:
  id: utility-vegetation
  version: 0.1.0
  extensionVersion: 0.1.0
  semanticModelRef: urn:xopt:model:utilities:0.1.0
  canonicalModelRef: urn:xopt:model:canonical:0.1.0

# Which registered contracts feed a planning snapshot.
inputContracts: [service-trucks, cutter-heads, crews, yards, spans, clearing-jobs, weather, pole-sensors, pole-readings, prices]

planningModes:
  - {id: periodic, adapter: ortools-periodic}
  - {id: rolling, adapter: ortools-rolling}

# Map your asset roles onto the three bundle slots.
bundleGeneration:
  roles:
    primaryAsset: [mobile-prime-mover]
    relatedEquipment: [implement]
    operator: [equipment-operator]

# Hard/medium/soft constraints; enforced:true ones are wired into the solver.
constraints:
  - {id: compatible-equipment, severity: hard, enforced: true}
  - {id: sufficient-power, severity: hard, enforced: true}
  - {id: operator-qualified, severity: hard, enforced: true}
  - {id: asset-available, severity: hard, enforced: true}
  - {id: no-double-booking, severity: hard, enforced: true}
  - {id: respect-contract-time-window, severity: hard, enforced: true}
  - {id: respect-weather-window, severity: hard, enforced: true}
  - {id: protect-frozen-tasks, severity: hard, enforced: true}

# Weather limits + which operations care about which dimension.
weatherPolicy:
  maxWindMs: 12.0
  maxRainMmPerH: 4.0
  sensitivity:
    CLEARING: [wind, rain]

# Lexicographic objective priorities.
objectives:
  mode: lexicographic
  priorities:
    - maximize-mandatory-contract-fulfillment
    - minimize-contractual-penalties
    - maximize-expected-contribution-margin
    - minimize-plan-instability
    - minimize-repositioning-time

planningDefaults:
  periodicHorizonDays: 7
  rollingHorizonHours: 48
  freezeWindowMinutes: 60
  maxAssignmentRoutingIterations: 3

# Condition-based maintenance from pole-sensor observations.
monitoring:
  batteryLowThresholdPct: 20.0
  batteryCriticalThresholdPct: 5.0
  minObservationConfidence: 0.5
  compositeHealthThreshold: 0.35
  serviceOperationType: EQUIPMENT_SERVICE
  servicePriorityClass: 2
  serviceDeadlineDays: 3
  servicePenaltyPerDayEur: 150.0
  assetTypeOverrides:
    POLE_SENSOR: {batteryLowThresholdPct: 25.0, serviceDeadlineDays: 2}

outputContracts: [dispatch-plans]
```

Notes:

- `bundleGeneration.roles` is where your `assetRole` strings are grouped into the
  three solver slots. If your operators are `line-crew`, list `line-crew` under
  `operator`.
- Constraints with `enforced: true` are active in the solver; declared-but-not-
  enforced ones are validated for adapter coverage only.
- The `monitoring` block is only meaningful if you have stationary assets +
  observations. Omit it otherwise. Defaults are constant-backed
  (`fl_op/core/constants.py`); per-asset-type overrides layer on top.
- `materialDemand` (per-operation consumable draw against depot inventory) is
  optional; include it only if your operations consume a tracked material.

## Step 5: register the pack

Add three things to `contracts/registry.yaml`:

1. A `domains:` entry:

```yaml
domains:
  utilities:
    root: domains/utilities
    profile: utility-vegetation
    semanticModelRef: urn:xopt:model:utilities:0.1.0
    generator: fl_op.data.utilities_entities:generate_utilities_domain  # optional
```

2. A `profiles:` entry:

```yaml
profiles:
  utility-vegetation:
    path: domains/utilities/profile.yaml
    version: 0.1.0
```

3. One `contracts:` entry per dataset. The map **key** must be globally unique
   across all domains; an optional `id` is the domain-local name the profile and
   events resolve against:

```yaml
contracts:
  utilities-service-trucks:
    id: service-trucks
    domain: utilities
    odcs: domains/utilities/odcs/service-trucks.odcs.yaml
    mapping: domains/utilities/mappings/service-trucks.mapping.yaml
    sourceFile: service-trucks.csv
    sourceFormat: csv
    fingerprints: {}        # filled by validate --write (see Step 9)
```

The registry exposes each contract as a versioned artifact ref
(`domain/local-id@odcs:<version>+mapping:<version>`) while still accepting the
compatibility key. Dataset discovery is automatic: the snapshot builder maps
every selected-domain contract whose mapping targets a snapshot-input entity, in
registry declaration order. So adding a dataset = add ODCS + mapping + registry
entry; the engine picks it up with no code change.

## Step 6: provide data (generator or real files)

Two options:

- **Real data:** drop the source files (named by each contract's `sourceFile`)
  into a directory and point commands at it with `--data /path/to/dir`.
- **Synthetic generator:** implement the callable named in the domain's
  `generator:` key (a function in `src/fl_op/data/...`) and you can run
  `fl-op generate-data --domain utilities --seed 42`. Model it on
  `fl_op/data/generator.py` (agricultural) or `fl_op/data/roadside_entities.py`
  (monitoring-driven). A generator is the fastest way to get a feasibility-grade
  dataset for smoke testing before real data lands; real CSVs take priority and
  missing fields fill from synthetic distributions.

## Step 7: validate (the command ladder)

Run these in order; each is a tighter gate. (`make` targets are convenience
aliases.)

```bash
# 1. Canonical model parses in isolation.
fl-op contracts canonical-validate          # make canonical-validate

# 2. Your pack maps completely onto the canonical model:
#    declared bindings only, known terms, required-binding coverage.
fl-op contracts validate-domain --domain utilities

# 3. Full suite: generated schemas, mappings, fingerprints, profiles,
#    metadata-loss guard. --write re-stamps fingerprints in the registry.
fl-op contracts validate                     # make contracts
fl-op contracts validate --write

# 4. Schema-generation hints are complete for each format you target.
fl-op contracts check-generation --format avro   # make check-gen
```

`validate-domain` also prints, per contract, how many physical fields are
optimization-mapped vs extra (analytical), so you can confirm coverage and see
which fields you are carrying for analysis only.

## Step 8: generate physical schemas and run a smoke plan

```bash
# Generate Avro/Proto/ES/Parquet schemas from the physical ODCS contracts.
fl-op contracts generate --format avro       # make contracts-gen (all four)

# End-to-end smoke run.
fl-op generate-data --domain utilities --seed 42
ACTIVE_DOMAIN=utilities fl-op snapshot build --data latest --mode periodic
ACTIVE_DOMAIN=utilities fl-op plan periodic --data latest
ACTIVE_DOMAIN=utilities fl-op plan periodic --data latest --objective time
```

`ACTIVE_DOMAIN=utilities` selects your pack for planning (the registry default is
`drone_logistics`). A clean run prints dispatched/infeasible counts and KPIs.
Infeasibility reason codes (for example `NO_COMPATIBLE_BUNDLE`) tell you whether
the problem is data (no compatible prime mover + implement for an operation) or
modeling (a missing capability binding).

## Step 9: freeze the evolution baseline and fingerprints

```bash
fl-op contracts validate --write     # stamp optimization/avro fingerprints
fl-op contracts evolution-check      # classify changes vs reviewed history
fl-op contracts evolution-freeze     # record the new reviewed snapshot
```

This records the reviewed baseline so later changes are gated (see
[Section 8](08-evolution-and-hygiene.md)). Your pack is
now runnable, shared-fleet selectable (`ACTIVE_DOMAINS=utilities,agricultural`),
and servable (`fl-op serve`).

## Checklist: minimal runnable pack

```
[ ] contracts/domains/<domain>/odcs/      one ODCS per dataset (physical only)
[ ] contracts/domains/<domain>/mappings/  one CanonicalMapping per ODCS
[ ] contracts/domains/<domain>/profile.yaml  OptimizationProfile
[ ] registry.yaml: domains: entry (root, profile, semanticModelRef, generator?)
[ ] registry.yaml: profiles: entry (path, version)
[ ] registry.yaml: contracts: entry per dataset (unique key, id?, odcs, mapping, sourceFile)
[ ] minimum entities present: asset (prime mover + implement + operator), location (depot + sites), task
[ ] every required binding covered (asset/location/task at minimum)
[ ] fl-op contracts validate-domain --domain <domain>   -> passes
[ ] fl-op contracts validate                            -> passes
[ ] fl-op contracts validate --write                    -> fingerprints stamped
[ ] ACTIVE_DOMAIN=<domain> fl-op plan periodic --data latest  -> dispatches tasks
[ ] fl-op contracts evolution-freeze                    -> baseline recorded
```

---

Previous: [4. Phase B: feasibility study](04-feasibility-study.md) | Next: [6. Costing methods](06-costing.md)
</content>
