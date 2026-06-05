# Optimization Data Contract and Planning Engine

## Initial Implementation Specification

**Document version:** 0.1.0
**Status:** Draft for implementation
**Primary domain:** Agricultural field-service operations
**Target reuse domains:** Construction, logistics, field maintenance, mining, utility operations, and other geotemporal asset-dispatch applications

---

# 1. Purpose

This specification defines an initial implementation of a solver-neutral optimization data platform for complex geotemporal asset usage.

The platform SHALL:

1. ingest batch datasets and event streams from heterogeneous operational systems;
2. describe their planning semantics using Avro metadata and ODCS contracts;
3. validate schema quality, semantic quality, and temporal consistency;
4. construct immutable optimization-ready planning snapshots;
5. execute:

   * rolling dispatch optimization through a Timefold adapter;
   * periodic planning optimization through an OR-Tools adapter;
6. publish explainable, versioned dispatch plans and plan revisions;
7. preserve lineage between source records, transformations, planning snapshots, solver inputs, and solver outputs.

The implementation SHALL initially support an agricultural enterprise that provides machinery services to farmers.

---

# 2. Normative terminology

The keywords **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** define implementation requirements.

---

# 3. Scope

## 3.1 Included in the initial implementation

The initial implementation SHALL support:

* mobile assets such as tractors, trucks, self-propelled sprayers, and mobile tankers;
* related equipment such as seeders, ploughs, cultivators, sprayers, spreaders, and trailers;
* operators and work shifts;
* asset compatibility and attachment relationships;
* customer contracts and field-service orders;
* field polygons and field-entry points;
* depots and material-loading locations;
* fuel, fertilizer, seed, chemical, and consumable inventory;
* cost norms and productivity norms;
* weather forecasts and weather-dependent restrictions;
* equipment location and availability;
* task prioritization;
* periodic planning;
* near-real-time rolling dispatch;
* manual overrides;
* plan versioning and auditability;
* quality-dependent solver behavior.

## 3.2 Excluded from the initial implementation

The following SHALL remain outside the MVP:

* automated invoice generation;
* full accounting;
* predictive maintenance models;
* route generation from raw road-network data;
* dynamic pricing;
* advanced probabilistic stochastic optimization;
* arbitrary user-defined executable code inside optimization contracts;
* automated negotiation of contract amendments;
* dynamic implement attachment changes inside the Timefold rolling-dispatch adapter.

These MAY be added in later releases.

---

# 4. Architectural principles

## 4.1 Solver-neutral canonical model

Source-system vocabulary SHALL NOT define the optimization model.

The platform SHALL map source records such as:

* `tractor`,
* `truck`,
* `car`,
* `sprayer`,
* `machine`,
* `equipment`,
* `implement`

into stable optimization abstractions.

The minimum canonical abstractions SHALL be:

* `Asset`
* `Capability`
* `AssetRelationship`
* `OperationalBundle`
* `Task`
* `TaskRequirement`
* `Material`
* `InventoryPosition`
* `Location`
* `Forecast`
* `Commitment`
* `CostRule`
* `QualityFinding`
* `PlanningSnapshot`
* `Plan`
* `PlanRevision`
* `ExecutionEvent`

## 4.2 Separation of concerns

The platform SHALL separate:

1. **physical schema** — payload shape and serialization;
2. **semantic mapping** — meaning of fields and relationships;
3. **quality policy** — rules for accepting, rejecting, imputing, or penalizing data;
4. **planning profile** — constraints, objectives, and solver bindings;
5. **solver adapter** — compilation into a specific solver input;
6. **execution output** — versioned dispatch plan and explanations.

## 4.3 No direct optimization on raw source data

Solvers SHALL NOT consume source tables, CSV files, ERP exports, or telemetry streams directly.

Solvers SHALL consume immutable `PlanningSnapshot` objects produced by the snapshot builder.

---

# 5. High-level system architecture

```text
Batch datasets              Operational streams
ERP / CSV / APIs             GPS / weather / breakdowns / order updates
        │                                  │
        └──────────────┬───────────────────┘
                       │
              Ingestion and validation
                       │
         Avro schema registry and ODCS registry
                       │
             Semantic mapping and migration
                       │
          Temporal operational-state projector
                       │
             Data-quality policy execution
                       │
              Immutable planning snapshots
                       │
        ┌──────────────┴───────────────────┐
        │                                  │
Periodic planning                    Rolling dispatch
OR-Tools adapter                     Timefold adapter
        │                                  │
        └──────────────┬───────────────────┘
                       │
           Plan normalization and explanation
                       │
               Versioned plan publication
                       │
         Operator UI / dispatcher UI / integrations
```

---

# 6. Required implementation components

The initial implementation SHALL contain the following services or deployable modules.

| Component                   | Responsibility                                                |
| --------------------------- | ------------------------------------------------------------- |
| `schema-registry`           | Store and version Avro schemas                                |
| `contract-registry`         | Store and version ODCS contracts and optimization profiles    |
| `ingestion-gateway`         | Receive batch imports and event streams                       |
| `schema-validator`          | Validate payloads against Avro schemas                        |
| `semantic-validator`        | Validate `x-optimization` bindings                            |
| `migration-engine`          | Apply versioned physical and semantic transformations         |
| `state-projector`           | Build current and historical temporal operational state       |
| `quality-engine`            | Execute quality policies and produce quality findings         |
| `snapshot-builder`          | Produce immutable planning snapshots                          |
| `optimization-orchestrator` | Select adapter, submit planning jobs, and track state         |
| `timefold-adapter`          | Perform rolling dispatch replanning                           |
| `ortools-adapter`           | Perform periodic assignment, scheduling, and route refinement |
| `plan-normalizer`           | Convert solver-specific outputs into canonical plans          |
| `plan-publisher`            | Publish approved plans and revisions                          |
| `audit-store`               | Persist lineage, decisions, overrides, and solver metadata    |
| `explanation-service`       | Explain assignments, unassigned tasks, and quality impacts    |

A modular monolith MAY be used initially, provided that module boundaries and APIs remain explicit.

---

# 7. Repository structure

The source repository SHALL use the following structure.

```text
optimization-platform/
├── contracts/
│   ├── odcs/
│   │   ├── assets/
│   │   ├── asset-state/
│   │   ├── relationships/
│   │   ├── orders/
│   │   ├── operators/
│   │   ├── inventory/
│   │   ├── weather/
│   │   ├── costs/
│   │   ├── execution/
│   │   └── plans/
│   └── profiles/
│       └── agricultural-custom-services/
├── schemas/
│   └── avro/
│       ├── assets/
│       ├── asset-state/
│       ├── relationships/
│       ├── orders/
│       ├── inventory/
│       ├── weather/
│       ├── execution/
│       └── plans/
├── migrations/
│   ├── physical/
│   ├── semantic/
│   └── fixtures/
├── services/
│   ├── ingestion/
│   ├── state-projector/
│   ├── quality-engine/
│   ├── snapshot-builder/
│   ├── orchestrator/
│   ├── plan-publisher/
│   └── explanation-service/
├── adapters/
│   ├── timefold/
│   └── ortools/
├── tests/
│   ├── contract/
│   ├── schema-roundtrip/
│   ├── migration/
│   ├── quality/
│   ├── snapshot/
│   ├── timefold/
│   ├── ortools/
│   └── end-to-end/
└── docs/
```

---

# 8. Extension namespace

## 8.1 Canonical identifier

The optimization extension namespace SHALL be:

```text
x-optimization
```

## 8.2 Avro expression

In Avro schemas, the extension SHALL be represented as:

```json
"x-optimization": { ... }
```

## 8.3 ODCS expression

In ODCS contracts, the extension SHALL be represented using `customProperties`:

```yaml
customProperties:
  - id: xopt_profile
    property: xOptimization
    value:
      ...
```

The ODCS property SHALL use `xOptimization` in camel case for consistency with ODCS custom-property conventions.

## 8.4 Extension version

Every optimization-extension object SHALL declare:

```yaml
extensionVersion: 0.1.0
```

The extension version SHALL be independent of:

* Avro schema version;
* ODCS contract version;
* semantic mapping version;
* quality-policy version;
* optimization-profile version;
* solver-adapter version.

---

# 9. Avro schema requirements

## 9.1 Avro responsibilities

Avro schemas SHALL define:

* physical event payload structure;
* field names;
* data types;
* nullable fields;
* default values;
* aliases for compatible renaming;
* record-level and field-level `x-optimization` metadata.

Avro schemas SHALL NOT define:

* complete cross-dataset business rules;
* complete optimization objective functions;
* complete migration workflows;
* solver-specific configuration;
* quality remediation procedures.

These SHALL be defined in ODCS contracts and optimization profiles.

## 9.2 Field-level metadata

A field MAY contain the following metadata:

```json
{
  "name": "rated_power_kw",
  "type": ["null", "double"],
  "default": null,
  "doc": "Nominal rated engine power expressed in kilowatts.",
  "x-optimization": {
    "extensionVersion": "0.1.0",
    "semanticTerm": "urn:xopt:capability:rated-power",
    "binding": "asset.capabilities.ratedPower",
    "canonicalUnit": "kW",
    "quantityKind": "power",
    "planningUse": ["capacity", "compatibility-filter"],
    "qualityPolicyRef": "dq://assets/rated-power/v1",
    "missingValuePolicy": "reject-for-planning"
  }
}
```

## 9.3 Record-level metadata

A record MAY contain:

```json
{
  "type": "record",
  "name": "MobileAssetState",
  "namespace": "org.example.agri.assets",
  "x-optimization": {
    "extensionVersion": "0.1.0",
    "semanticEntity": "urn:xopt:entity:mobile-asset-state",
    "entityKeyField": "asset_id",
    "eventTimeField": "observed_at",
    "validFromField": "valid_from",
    "validToField": "valid_to"
  },
  "fields": []
}
```

## 9.4 Metadata preservation requirement

The implementation SHALL provide a schema round-trip conformance test.

For every registered Avro schema:

1. parse schema JSON;
2. serialize the parsed schema back to JSON;
3. parse the serialized schema;
4. verify preservation of every `x-optimization` object;
5. register and retrieve the schema from the selected schema registry;
6. verify preservation again.

Schema registration SHALL fail if metadata is lost.

## 9.5 Schema fingerprints

The platform SHALL maintain two independent hashes:

```text
avroParsingFingerprint
optimizationMetadataHash
```

`avroParsingFingerprint` SHALL identify serialization-relevant structure.

`optimizationMetadataHash` SHALL identify the normalized `x-optimization` metadata.

Changing optimization semantics SHALL change `optimizationMetadataHash`, even when the Avro parsing fingerprint remains unchanged.

## 9.6 Field renaming

Compatible field renaming SHALL use Avro aliases.

Example:

```json
{
  "name": "rated_power_kw",
  "aliases": ["engine_power_kw"],
  "type": ["null", "double"],
  "default": null
}
```

Aliases SHALL NOT be used to claim semantic equivalence when units, meaning, temporal interpretation, or business scope change.

---

# 10. ODCS contract requirements

## 10.1 ODCS responsibilities

Each governed data product SHALL have an ODCS contract.

The ODCS contract SHALL define:

* contract identity;
* owner;
* status;
* source system;
* physical schema reference;
* schema elements;
* business names and descriptions;
* data classifications;
* quality rules;
* SLA properties;
* authoritative definitions;
* `xOptimization` mappings;
* migration references;
* permitted planning uses.

## 10.2 Root-level extension

Each ODCS contract used for planning SHALL include:

```yaml
customProperties:
  - id: xopt_contract_profile
    property: xOptimization
    value:
      extensionVersion: 0.1.0
      semanticModelRef: urn:xopt:model:agri-custom-services:0.1.0
      dataProductRole: assetMaster
      mappingVersion: 1.0.0
      permittedPlanningUses:
        - periodic-planning
        - rolling-dispatch
      migrationPolicyRef: migration://assets/master/v1
      defaultQualityPolicyRef: dq://assets/master/v1
```

## 10.3 Property-level extension

ODCS schema properties used by the optimizer SHALL include field bindings:

```yaml
schema:
  - id: mobile_asset
    name: mobile_asset
    logicalType: object
    physicalType: topic
    properties:
      - id: rated_power_kw
        name: rated_power_kw
        logicalType: number
        physicalType: double
        required: false
        description: Nominal rated engine power in kilowatts.
        customProperties:
          - id: xopt_binding_rated_power
            property: xOptimization
            value:
              extensionVersion: 0.1.0
              semanticTerm: urn:xopt:capability:rated-power
              binding: asset.capabilities.ratedPower
              canonicalUnit: kW
              quantityKind: power
              planningUse:
                - capacity
                - compatibility-filter
              qualityPolicyRef: dq://assets/rated-power/v1
```

## 10.4 Cross-dataset planning profile

Cross-dataset constraints and objective definitions SHALL be placed in a dedicated ODCS-compatible planning-profile document.

The planning profile SHALL use:

```yaml
kind: OptimizationProfile
apiVersion: x-optimization/v0.1.0
```

The profile SHALL be stored in the contract registry and versioned independently.

---

# 11. Canonical semantic model

## 11.1 Asset

An `Asset` is a physical or logical resource that may participate in task execution.

Required fields:

```yaml
assetId: string
assetType: string
roles: string[]
status: string
capabilities: Capability[]
locationRef: string?
availability: TimeInterval[]
sourceRef: string
validFrom: timestamp
validTo: timestamp?
```

Example roles:

```text
mobile-prime-mover
self-propelled-application-asset
implement
mobile-material-carrier
operator
depot
loading-station
```

## 11.2 Capability

A `Capability` SHALL describe a measurable or categorical ability.

```yaml
capabilityId: string
semanticTerm: string
value: scalar | object
canonicalUnit: string?
validFrom: timestamp
validTo: timestamp?
confidence: number?
sourceRef: string
```

Examples:

* rated power;
* PTO speed;
* hydraulic-flow capacity;
* hitch category;
* working width;
* tank volume;
* maximum payload;
* application-rate range;
* operator certification.

## 11.3 AssetRelationship

Relationships SHALL be modeled as first-class versioned records.

```yaml
relationshipId: string
predicate: string
fromAssetId: string
toAssetId: string
properties: object
validFrom: timestamp
validTo: timestamp?
sourceRef: string
```

Required predicates:

```text
can-operate
is-attached-to
can-be-attached-to
is-located-at
assigned-to-operator
requires-support-asset
can-load-at
can-refuel-at
```

Example:

```yaml
relationshipId: compatibility-tractor-014-sprayer-008-v3
predicate: urn:xopt:relationship:can-operate
fromAssetId: tractor-014
toAssetId: sprayer-008
validFrom: 2026-03-01T00:00:00Z
validTo: null
properties:
  minVehiclePowerKw: 145
  hitchType: three-point-category-3
  requiredPtoRpm: 1000
  requiredHydraulicFlowLpm: 80
  isobusRequired: true
  couplingDurationMinutes: 35
```

## 11.4 OperationalBundle

An `OperationalBundle` is a schedulable combination of resources.

Examples:

```text
tractor + cultivator + operator
tractor + fertilizer spreader + operator
self-propelled sprayer + operator
truck + tanker trailer + operator
```

Required fields:

```yaml
bundleId: string
bundleType: string
assetIds: string[]
operatorIds: string[]
capabilities: Capability[]
currentLocationRef: string
availability: TimeInterval[]
bundleStatus: string
configurationDurationMinutes: integer
sourceSnapshotId: string
```

Bundles SHALL be generated from relationships, capabilities, availability, and current attachment state.

## 11.5 Task

A `Task` is a unit of work requested by a farmer, generated by a contract, or introduced manually.

```yaml
taskId: string
orderId: string
taskType: string
operationType: string
locationRef: string
areaHa: number?
serviceDurationMinutes: integer
timeWindows: TimeInterval[]
priorityClass: string
mandatory: boolean
requirements: TaskRequirement[]
materialRequirements: MaterialRequirement[]
commitmentRefs: string[]
revenueRuleRef: string?
costRuleRefs: string[]
status: string
```

## 11.6 Forecast

```yaml
forecastId: string
forecastType: string
locationRef: string
issuedAt: timestamp
forecastFor: TimeInterval
value: object
confidence: number?
sourceRef: string
```

Examples:

* wind speed;
* precipitation;
* soil trafficability;
* temperature;
* new-order volume forecast.

## 11.7 Commitment

```yaml
commitmentId: string
contractId: string
taskId: string?
commitmentType: string
hardness: hard | medium | soft
value: object
penaltyRuleRef: string?
validFrom: timestamp
validTo: timestamp?
```

Examples:

* deadline;
* guaranteed completion;
* farmer-priority class;
* maximum acceptable delay;
* penalty per hour;
* minimum quality class.

---

# 12. Required data contracts

The initial implementation SHALL create the following ODCS contracts and corresponding Avro schemas.

| Contract ID           | Mode                                     | Purpose                                              |
| --------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `assets-master`       | batch + change stream                    | Stable asset characteristics                         |
| `assets-state`        | stream + snapshot                        | Availability, location, operating state              |
| `asset-relationships` | batch + change stream                    | Compatibility, attachment, and support relationships |
| `operators-master`    | batch + change stream                    | Skills, licenses, and identity                       |
| `operator-shifts`     | batch + change stream                    | Availability and assignments                         |
| `field-parcels`       | batch + change stream                    | Field polygons, field-entry points, restrictions     |
| `service-orders`      | batch + change stream                    | Farmer requests and task definitions                 |
| `contracts-master`    | batch + change stream                    | Deadlines, penalties, prices, obligations            |
| `inventory-positions` | batch + stream                           | Available and reserved materials                     |
| `cost-norms`          | batch + change stream                    | Fuel, labor, material, and equipment norms           |
| `weather-forecasts`   | stream + snapshot                        | Forecast values by location and time interval        |
| `manual-overrides`    | stream                                   | Dispatcher decisions and locked assignments          |
| `execution-events`    | stream                                   | Start, stop, delay, completion, and exception events |
| `planning-snapshots`  | immutable batch object                   | Solver-ready state                                   |
| `dispatch-plans`      | immutable batch object + revision stream | Normalized solver output                             |

---

# 13. Temporal model

## 13.1 Required timestamps

Operational records SHALL distinguish:

| Field              | Meaning                                  |
| ------------------ | ---------------------------------------- |
| `observedAt`       | When the physical condition was observed |
| `receivedAt`       | When the platform received the event     |
| `recordedAt`       | When the record was persisted            |
| `validFrom`        | Beginning of real-world validity         |
| `validTo`          | End of real-world validity               |
| `forecastIssuedAt` | When a forecast was produced             |
| `forecastForFrom`  | Beginning of forecast interval           |
| `forecastForTo`    | End of forecast interval                 |
| `plannedForFrom`   | Planned activity start                   |
| `plannedForTo`     | Planned activity finish                  |
| `executedAt`       | Actual execution timestamp               |

## 13.2 Bitemporal storage

The operational-state projector SHALL preserve:

* real-world validity time;
* system-recording time.

Corrections SHALL NOT overwrite historical values without preserving the previous version.

## 13.3 Late-arriving data

Each stream SHALL define:

```yaml
latenessPolicy:
  acceptableDelaySeconds: integer
  quarantineAfterSeconds: integer
  reconciliationMode: apply | quarantine | manual-review
```

Late-arriving events SHALL trigger snapshot invalidation only when their semantic impact intersects an active or future planning horizon.

---

# 14. Data-quality model

## 14.1 Quality stages

The quality engine SHALL execute:

1. structural validation;
2. schema compatibility validation;
3. semantic-binding validation;
4. unit normalization;
5. identifier and relationship validation;
6. temporal validation;
7. geospatial validation;
8. cross-dataset consistency validation;
9. domain-rule validation;
10. anomaly detection where configured.

## 14.2 Quality-response actions

Each quality rule SHALL specify one of:

```text
reject
quarantine
manual-review
accept-with-warning
accept-with-penalty
impute
fallback-to-conservative-value
```

## 14.3 Examples

| Data element                | Validation                                                         | Response                            |
| --------------------------- | ------------------------------------------------------------------ | ----------------------------------- |
| Rated asset power           | Must be positive and convertible to kW                             | Reject asset from bundle generation |
| GPS location                | Must be recent enough for selected planning mode                   | Accept with penalty or block        |
| Field polygon               | Must be valid geometry with known CRS                              | Quarantine field tasks              |
| Compatibility relation      | Must not contain contradictory overlapping intervals               | Quarantine relationship             |
| Fertilizer application rate | Must have convertible units                                        | Reject task configuration           |
| Inventory balance           | Available stock after reservations must remain non-negative        | Reject plan                         |
| Forecast                    | Must contain issue time and forecast interval                      | Apply conservative fallback         |
| Contract                    | Must contain farmer, task type, location, deadline, and price rule | Manual review                       |
| Cost norm                   | Must be approved and below staleness threshold                     | Warning or fallback norm            |

## 14.4 Quality findings

Each quality issue SHALL produce:

```yaml
qualityFindingId: string
ruleId: string
severity: info | warning | error | critical
entityRef: string
fieldRef: string?
detectedAt: timestamp
actionApplied: string
originalValue: any?
normalizedValue: any?
planningImpact: string
sourceRef: string
```

## 14.5 No silent imputation

Imputed values SHALL be:

* explicitly identified;
* traceable to an imputation policy;
* included in plan quality summaries;
* available to the explanation service.

---

# 15. Optimization-profile language

## 15.1 Purpose

The optimization profile SHALL define:

* input contracts;
* field bindings;
* relationship types;
* bundle-generation rules;
* hard, medium, and soft constraints;
* objective priorities;
* quality-dependent behavior;
* solver-adapter support;
* output-contract requirements.

## 15.2 Rule representation

Rules SHALL use a declarative typed abstract-syntax tree.

Arbitrary code, scripts, SQL fragments, and unsafe expression evaluation SHALL NOT be permitted inside optimization rules.

## 15.3 Minimum operators

The profile language SHALL initially support:

```text
and
or
not
eq
neq
gt
gte
lt
lte
in
exists
within
overlaps
add
subtract
multiply
divide
sum
min
max
coalesce
ageMinutes
distanceKm
travelTimeMinutes
convertUnit
```

## 15.4 Minimum domain functions

```text
hasCapability(asset, semanticTerm)
capabilityValue(asset, semanticTerm)
relationshipExists(predicate, fromEntity, toEntity, atTime)
compatible(bundle, task)
materialAvailable(materialType, depot, atTime)
forecastValue(forecastType, location, interval)
qualityScore(entity)
isPinned(task)
isFrozen(task)
```

## 15.5 Example rule

```yaml
id: sufficient-power
severity: hard
scope: task-bundle-assignment
assert:
  operator: gte
  args:
    - function: capabilityValue
      args:
        - ref: bundle
        - const: urn:xopt:capability:rated-power
    - operator: multiply
      args:
        - ref: task.requirements.minimumPowerKw
        - ref: task.location.powerDemandFactor
```

## 15.6 Example weather rule

```yaml
id: spraying-wind-limit
severity: hard
scope: task-schedule
when:
  operator: eq
  args:
    - ref: task.operationType
    - const: spraying
assert:
  operator: lte
  args:
    - function: forecastValue
      args:
        - const: wind-speed-mps
        - ref: task.locationRef
        - ref: proposedInterval
    - ref: policy.maxSprayingWindMps
```

---

# 16. Agricultural custom-services profile

The initial profile SHALL be:

```yaml
apiVersion: x-optimization/v0.1.0
kind: OptimizationProfile

metadata:
  id: agricultural-custom-services
  version: 0.1.0
  semanticModelRef: urn:xopt:model:agricultural-custom-services:0.1.0

planningModes:
  - id: periodic
    adapter: ortools-periodic
  - id: rolling
    adapter: timefold-rolling

bundleGeneration:
  roles:
    primaryAsset:
      - mobile-prime-mover
      - self-propelled-application-asset
    relatedEquipment:
      - implement
      - trailer
      - material-carrier
    operator:
      - equipment-operator

constraints:
  - compatible-equipment
  - sufficient-power
  - operator-qualified
  - asset-available
  - no-double-booking
  - required-material-available
  - respect-contract-time-window
  - respect-weather-window
  - respect-field-restrictions
  - respect-manual-overrides
  - protect-frozen-tasks

objectives:
  mode: lexicographic
  priorities:
    - maximize-mandatory-contract-fulfillment
    - minimize-contractual-penalties
    - maximize-expected-contribution-margin
    - minimize-plan-instability
    - minimize-repositioning-time
    - minimize-empty-distance
    - minimize-idle-time
    - balance-utilization
```

---

# 17. Planning-snapshot builder

## 17.1 Snapshot definition

A `PlanningSnapshot` SHALL be immutable.

```yaml
snapshotId: string
effectiveAt: timestamp
generatedAt: timestamp
planningMode: periodic | rolling
planningHorizon:
  from: timestamp
  to: timestamp
sourceWatermarks: object
contractVersions: object
avroSchemaVersions: object
mappingVersions: object
qualityPolicyVersions: object
optimizationProfileVersion: string
adapterCompatibilityVersion: string
assets: Asset[]
relationships: AssetRelationship[]
bundles: OperationalBundle[]
tasks: Task[]
inventory: InventoryPosition[]
forecasts: Forecast[]
commitments: Commitment[]
manualOverrides: object[]
qualitySummary: object
lineageRef: string
```

## 17.2 Snapshot build procedure

The snapshot builder SHALL:

1. select an effective timestamp;
2. load all required contract versions;
3. load operational state valid at that timestamp;
4. apply migration rules;
5. normalize units;
6. apply quality policies;
7. exclude quarantined entities;
8. generate feasible operational bundles;
9. derive task durations and resource requirements;
10. compute field-entry locations;
11. attach applicable forecasts;
12. calculate material availability after existing reservations;
13. attach quality scores;
14. generate an immutable snapshot;
15. calculate snapshot hash;
16. store lineage references.

## 17.3 Snapshot reproducibility

Rebuilding a snapshot with identical:

* source records;
* effective timestamp;
* contract versions;
* mapping versions;
* quality-policy versions;
* profile version;
* adapter-compatibility version

SHALL produce the same normalized snapshot hash.

---

# 18. Bundle-generation rules

## 18.1 Static filtering

Bundle generation SHALL first remove impossible combinations using:

* compatibility relationships;
* hitch type;
* PTO requirement;
* hydraulic-flow requirement;
* power requirement;
* payload;
* working width;
* operator certification;
* maintenance status;
* equipment availability.

## 18.2 Dynamic filtering

Bundle generation SHALL then consider:

* current attachment state;
* reconfiguration duration;
* current location;
* planning horizon;
* operator shift;
* forecast-dependent restrictions;
* material-loading capability;
* manual overrides.

## 18.3 Shared-resource exclusivity

A physical asset SHALL NOT appear in overlapping assignments.

This applies to:

* tractors;
* implements;
* operators;
* tankers;
* trailers;
* loading stations where loading capacity is modeled.

## 18.4 Bundle identity

A bundle ID SHALL be deterministic:

```text
bundleId = hash(sorted(assetIds) + sorted(operatorIds) + configurationVersion)
```

---

# 19. Timefold rolling-dispatch adapter

## 19.1 Purpose

The Timefold adapter SHALL perform near-real-time replanning for active operational bundles and open tasks.

## 19.2 MVP limitation

The MVP Timefold adapter SHALL schedule **active operational bundles**, not arbitrary future bundle reconfiguration.

An active bundle SHALL represent:

* equipment already attached;
* a self-propelled machine;
* or a dispatcher-approved bundle fixed for the rolling horizon.

Dynamic attachment and detachment decisions SHALL be handled by:

* periodic OR-Tools planning;
* dispatcher intervention;
* or a later custom Timefold model.

This restriction prevents simultaneous assignment of alternative bundles that share the same tractor, implement, or operator.

## 19.3 Planning horizon

The default rolling horizon SHALL be configurable.

Recommended initial default:

```yaml
rollingHorizonHours: 48
freezeWindowMinutes: 60
```

## 19.4 Mapping into Timefold

| Canonical object                 | Timefold object                          |
| -------------------------------- | ---------------------------------------- |
| Active `OperationalBundle`       | vehicle                                  |
| Bundle availability interval     | vehicle shift                            |
| Bundle current location          | shift start location                     |
| Bundle end location              | shift end location                       |
| `Task`                           | visit                                    |
| Task service duration            | visit service duration                   |
| Task time windows                | visit time windows                       |
| Required categorical capability  | required tag or skill                    |
| Required graded capability       | required skill level                     |
| Operator cost                    | shift cost or rate                       |
| Contract priority                | priority / mandatory visit configuration |
| Pinned assignment                | pinned visit                             |
| Frozen execution window          | `freezeTime`                             |
| Multi-resource synchronized task | visit group where supported              |
| Unassignable task                | unassigned visit with normalized reason  |

## 19.5 Replanning triggers

The rolling adapter SHALL replan when any of the following occurs:

```text
asset-unavailable
asset-location-materially-changed
task-created
task-cancelled
task-priority-changed
task-duration-updated
task-started
task-completed
task-delayed
forecast-updated
inventory-critical-change
manual-override-created
manual-override-removed
periodic-plan-published
```

## 19.6 Full-resubmission baseline

The Timefold adapter SHALL initially submit a complete updated rolling-planning dataset derived from the latest canonical snapshot.

The adapter SHALL NOT depend on JSON Patch support.

## 19.7 Optional patch capability

The adapter MAY implement patch submission behind:

```yaml
timefold:
  patchMode:
    enabled: false
    capability: preview
```

The production default SHALL remain `false` until explicitly enabled.

## 19.8 Freeze and pin behavior

The adapter SHALL:

* freeze tasks that have started;
* freeze tasks currently en route;
* freeze tasks whose planned start occurs within the configured freeze window;
* preserve explicitly pinned tasks;
* preserve material-loaded assignments where reassignment would be operationally invalid;
* expose every preserved assignment in the canonical output.

## 19.9 Plan-instability penalty

Rolling replanning SHALL minimize avoidable disruption.

The canonical model SHALL track:

```yaml
previousBundleId: string?
previousStartTime: timestamp?
previousSequencePosition: integer?
changePenalty: integer
```

Changes after the freeze window MAY be permitted but SHALL receive a configurable penalty.

## 19.10 Output normalization

Timefold output SHALL be normalized into:

* routes;
* task assignments;
* start and finish times;
* frozen assignments;
* pinned assignments;
* unassigned tasks;
* solver metadata;
* score;
* plan revision;
* quality summary;
* explanation references.

---

# 20. OR-Tools periodic-planning adapter

## 20.1 Purpose

The OR-Tools adapter SHALL create periodic plans for the next several days.

Recommended initial default:

```yaml
periodicHorizonDays: 7
periodicReplanSchedule: daily
```

## 20.2 Two-stage structure

The adapter SHALL use two OR-Tools stages.

### Stage A — CP-SAT assignment and schedule selection

CP-SAT SHALL select:

* feasible operational bundles;
* asset-to-task assignments;
* operator assignments;
* implement assignments;
* task start intervals;
* material reservations;
* depot-loading reservations;
* optional task deferral;
* optional task rejection;
* weather-window compliance;
* contract fulfillment;
* shared-resource exclusivity.

### Stage B — Routing solver route refinement

The routing solver SHALL refine:

* task sequence;
* repositioning;
* depot visits;
* travel-time feasibility;
* vehicle capacities;
* loading and unloading constraints;
* optional dropped tasks with penalties;
* route-duration limits.

## 20.3 Integer scaling

All CP-SAT quantities SHALL be integer-scaled.

Recommended canonical scales:

| Quantity             | Internal unit          |
| -------------------- | ---------------------- |
| Time                 | minutes                |
| Distance             | meters                 |
| Power                | watts or deciwatts     |
| Fuel                 | milliliters            |
| Fertilizer mass      | grams                  |
| Liquid volume        | milliliters            |
| Money                | smallest currency unit |
| Probability          | basis points           |
| Penalty coefficients | integer score units    |

The scaling configuration SHALL be versioned.

## 20.4 Shared-resource constraints

The CP-SAT model SHALL prevent overlapping use of:

* tractors;
* implements;
* operators;
* mobile tankers;
* trailers;
* loading resources;
* depot capacity where configured.

## 20.5 Optional-task modeling

Each task SHALL define:

```yaml
mandatory: boolean
dropPenalty: integer?
deferPenalty: integer?
revenueValue: integer?
```

Mandatory tasks SHALL have sufficiently high penalties or explicit hard constraints according to the contract profile.

Optional tasks SHALL be included only when their expected contribution is beneficial under the selected objective hierarchy.

## 20.6 Route-refinement feedback

The adapter SHALL calculate route-refinement deltas.

If route refinement materially changes feasibility or cost, the adapter SHALL:

1. update estimated route costs;
2. rerun CP-SAT;
3. rerun route refinement;
4. stop after convergence or configured iteration count.

Default:

```yaml
maxAssignmentRoutingIterations: 3
```

## 20.7 Periodic output

The periodic plan SHALL include:

* selected bundles;
* bundle-configuration actions;
* attachment and detachment actions;
* tasks by day;
* task sequence;
* routes;
* depot-loading schedule;
* fuel reservations;
* fertilizer, seed, and chemical reservations;
* expected revenue;
* expected cost;
* expected margin;
* deferred tasks;
* rejected tasks;
* contract-risk summary;
* weather-risk summary;
* quality summary.

---

# 21. Adapter service-provider interface

Every solver adapter SHALL implement:

```text
validateProfile(profile) -> ValidationReport
supports(feature) -> boolean
compile(snapshot, profile, config) -> SolverInput
solve(solverInput, config) -> SolverRawResult
normalize(solverRawResult, snapshot, profile) -> CanonicalPlan
explain(canonicalPlan, snapshot, profile) -> ExplanationBundle
health() -> AdapterHealth
```

## 21.1 Adapter manifest

Each adapter SHALL publish:

```yaml
adapterId: string
adapterVersion: string
solverName: string
solverVersion: string
supportedPlanningModes: string[]
supportedRuleOperators: string[]
supportedDomainFunctions: string[]
supportedFeatures: string[]
unsupportedFeatures: string[]
integerScalingPolicyRef: string?
```

## 21.2 Capability validation

A planning profile SHALL NOT run on an adapter unless every required rule and function is supported.

Unsupported rules SHALL cause profile validation failure before solving.

---

# 22. Canonical plan-output contract

## 22.1 Plan envelope

```yaml
planId: string
revisionId: string
parentRevisionId: string?
originPlanId: string
planningMode: periodic | rolling
snapshotId: string
optimizationProfileVersion: string
adapterId: string
adapterVersion: string
solverVersion: string
generatedAt: timestamp
effectiveFrom: timestamp
effectiveTo: timestamp?
status: draft | approved | published | superseded | rejected
score: object
qualitySummary: object
riskSummary: object
lineageRef: string
```

## 22.2 Assignment

```yaml
assignmentId: string
taskId: string
bundleId: string
assetIds: string[]
operatorIds: string[]
plannedStart: timestamp
plannedFinish: timestamp
routeRef: string?
materialReservationRefs: string[]
isFrozen: boolean
isPinned: boolean
expectedRevenueMinorUnits: integer
expectedCostMinorUnits: integer
expectedMarginMinorUnits: integer
qualityImpactRefs: string[]
explanationRef: string
```

## 22.3 Unassigned task

```yaml
taskId: string
reasonCode: string
details: object
recommendedAction: string?
explanationRef: string
```

Required reason codes:

```text
NO_COMPATIBLE_BUNDLE
INSUFFICIENT_POWER
NO_AVAILABLE_OPERATOR
NO_AVAILABLE_ASSET
NO_VALID_WEATHER_WINDOW
INSUFFICIENT_MATERIAL
CONTRACT_WINDOW_INFEASIBLE
LOCATION_DATA_INVALID
FIELD_GEOMETRY_INVALID
QUALITY_POLICY_BLOCK
MANUAL_OVERRIDE_CONFLICT
OPTIMIZATION_TRADEOFF
UNKNOWN
```

## 22.4 Material reservation

```yaml
reservationId: string
taskId: string
materialType: string
inventoryLocationRef: string
quantity: integer
canonicalUnit: string
reservedFrom: timestamp
reservedTo: timestamp?
status: provisional | confirmed | consumed | released
```

## 22.5 Plan revision

A rolling-plan update SHALL create a new immutable revision.

Published revisions SHALL NOT be mutated.

---

# 23. Operational APIs

## 23.1 Contract management

```text
POST   /v1/contracts/validate
POST   /v1/contracts
GET    /v1/contracts/{contractId}/{version}
GET    /v1/contracts/{contractId}/versions
POST   /v1/profiles/validate
POST   /v1/profiles
GET    /v1/profiles/{profileId}/{version}
```

## 23.2 Snapshot management

```text
POST   /v1/snapshots/build
GET    /v1/snapshots/{snapshotId}
GET    /v1/snapshots/{snapshotId}/quality
GET    /v1/snapshots/{snapshotId}/lineage
```

## 23.3 Optimization

```text
POST   /v1/plans/periodic
POST   /v1/plans/rolling
GET    /v1/plans/{planId}
GET    /v1/plans/{planId}/revisions
GET    /v1/plans/{planId}/explanations
POST   /v1/plans/{planId}/approve
POST   /v1/plans/{planId}/publish
POST   /v1/plans/{planId}/reject
```

## 23.4 Overrides

```text
POST   /v1/overrides
GET    /v1/overrides/{overrideId}
DELETE /v1/overrides/{overrideId}
```

---

# 24. Event types

The platform SHALL publish and consume versioned events.

Required event types:

```text
asset.master.updated
asset.state.updated
asset.unavailable
asset.relationship.updated
operator.shift.updated
order.created
order.updated
order.cancelled
forecast.updated
inventory.updated
manual-override.created
manual-override.removed
task.started
task.delayed
task.completed
snapshot.created
snapshot.rejected
plan.generated
plan.approved
plan.published
plan.superseded
plan.rejected
quality.finding.created
```

Each event SHALL contain:

```yaml
eventId: string
eventType: string
eventVersion: string
source: string
entityRef: string
observedAt: timestamp?
receivedAt: timestamp
recordedAt: timestamp
schemaRef: string
payload: object
```

---

# 25. Migration and compatibility

## 25.1 Version dimensions

Every plan SHALL record:

```yaml
sourceSchemaVersions: object
semanticMappingVersions: object
qualityPolicyVersions: object
optimizationProfileVersion: string
adapterVersion: string
solverVersion: string
```

## 25.2 Physical compatibility

Physical changes include:

* optional field addition;
* field removal;
* field rename;
* type change;
* default-value change.

Compatible renames SHOULD use Avro aliases.

## 25.3 Semantic compatibility

Semantic changes include:

* unit change;
* meaning change;
* temporal-role change;
* scope change;
* source-of-truth change;
* change from observed to inferred value;
* change in null interpretation.

Semantic changes SHALL require:

```yaml
semanticMigration:
  migrationId: string
  fromSemanticVersion: string
  toSemanticVersion: string
  transformRef: string
  validationFixtureRefs: string[]
```

## 25.4 Planning-behavior compatibility

Planning-behavior changes include:

* constraint hardness change;
* new objective priority;
* quality-action change;
* altered freeze window;
* changed penalty weight;
* new fallback behavior.

These SHALL require an optimization-profile version change.

## 25.5 Migration workflow

Breaking changes SHALL use:

1. new version registration;
2. migration implementation;
3. fixture-based migration tests;
4. dual-read or dual-write period;
5. historical replay;
6. shadow planning;
7. plan comparison;
8. approval;
9. cutover;
10. rollback readiness.

---

# 26. Explanation requirements

The platform SHALL explain:

* why a task was assigned;
* why a particular bundle was selected;
* why another bundle was rejected;
* why a task was deferred;
* why a task was dropped;
* which data-quality issues influenced a decision;
* which manual override influenced a decision;
* which weather restriction influenced a decision;
* which contract penalty influenced a decision;
* whether a field was imputed, stale, or inferred.

Example:

```yaml
taskId: task-2026-1048
decision: unassigned
reasonCode: NO_COMPATIBLE_BUNDLE
details:
  operationType: deep-cultivation
  requiredPowerKw: 220
  candidateBundlesEvaluated: 14
  rejectedCandidates:
    - bundleId: bundle-tractor-014-cultivator-003
      reason: INSUFFICIENT_POWER
      availablePowerKw: 180
      requiredPowerKw: 220
recommendedAction: subcontract-or-reschedule
```

---

# 27. Observability

The platform SHALL expose metrics for:

* events ingested;
* event-processing latency;
* late events;
* invalid payloads;
* schema-validation failures;
* semantic-binding failures;
* quarantined records;
* imputed values;
* snapshot-build duration;
* snapshot entity counts;
* feasible-bundle count;
* excluded-bundle count;
* optimization duration;
* solver status;
* assigned-task count;
* unassigned-task count;
* changed assignments per rolling revision;
* frozen assignments;
* expected margin;
* contract penalties;
* weather-risk exposure;
* plan-publication failures.

---

# 28. Security and governance

The platform SHALL:

* authenticate API callers;
* authorize contract publication;
* authorize plan approval and publication;
* preserve immutable audit records;
* distinguish system decisions from manual overrides;
* record the identity of users who approve plans;
* prevent unauthorized modification of published plans;
* classify sensitive fields in ODCS;
* restrict access to farmer contract terms and personal data;
* validate all external payloads before persistence;
* prevent executable code inside contracts and profiles.

---

# 29. Initial acceptance criteria

## 29.1 Schema and contracts

The MVP is accepted when:

* all required ODCS contracts exist;
* all required Avro schemas exist;
* each planning-relevant field contains an ODCS binding;
* each Avro `x-optimization` binding round-trips through the selected Avro library;
* each Avro binding round-trips through the selected schema registry;
* schema fingerprints and metadata hashes are stored independently;
* aliases are tested for field-renaming compatibility.

## 29.2 Snapshot builder

The MVP is accepted when:

* batch and stream inputs converge into one planning snapshot;
* timestamps are preserved;
* unit conversion is deterministic;
* quarantined records are excluded;
* imputed records remain traceable;
* snapshot hashes are reproducible;
* lineage references resolve to input records and contract versions.

## 29.3 Timefold rolling adapter

The MVP is accepted when:

* active bundles map to Timefold vehicles and shifts;
* tasks map to visits;
* time windows are respected;
* capabilities map to skills or tags;
* started tasks remain frozen;
* near-term tasks respect the configured freeze window;
* manually pinned tasks remain assigned;
* newly created urgent tasks trigger replanning;
* asset unavailability triggers replanning;
* rolling output produces a new immutable plan revision;
* alternative bundles sharing physical assets cannot be double-booked.

## 29.4 OR-Tools periodic adapter

The MVP is accepted when:

* CP-SAT selects compatible bundles;
* CP-SAT prevents shared-resource overlap;
* required materials remain within available inventory;
* weather windows are respected;
* contract deadlines and penalties affect assignment;
* optional tasks can be deferred or dropped with explicit penalties;
* routing refinement respects travel times and capacities;
* output contains attachment actions, routes, reservations, costs, and margins;
* unassigned tasks include normalized reason codes.

## 29.5 Governance

The MVP is accepted when:

* every plan records source schema versions;
* every plan records mapping versions;
* every plan records quality-policy versions;
* every plan records optimization-profile version;
* every plan records adapter and solver versions;
* every published plan is immutable;
* every manual override is attributable to a user;
* every unassigned task can be explained.

---

# 30. Delivery phases

## Phase 1 — Contract and schema foundation

Deliver:

* Avro schema conventions;
* ODCS contract template;
* `x-optimization` extension schema;
* semantic-term registry;
* contract validator;
* Avro metadata round-trip tests;
* initial agricultural contracts.

## Phase 2 — Canonical operational state

Deliver:

* ingestion gateway;
* batch importer;
* stream consumer;
* temporal state projector;
* unit normalizer;
* quality engine;
* relationship store;
* field and location model.

## Phase 3 — Snapshot builder

Deliver:

* snapshot builder;
* bundle generator;
* contract and quality version tracking;
* snapshot hashing;
* lineage persistence;
* snapshot inspection API.

## Phase 4 — OR-Tools periodic adapter

Deliver:

* CP-SAT assignment model;
* shared-resource interval constraints;
* inventory reservation logic;
* weather-window logic;
* contract-priority logic;
* routing refinement;
* normalized periodic plan output.

## Phase 5 — Timefold rolling adapter

Deliver:

* active-bundle projection;
* task-to-visit mapping;
* capability-to-skill/tag mapping;
* freeze and pin logic;
* full-dataset resubmission workflow;
* normalized rolling revision output;
* trigger-driven replanning.

## Phase 6 — Dispatcher workflow

Deliver:

* plan comparison;
* approval and publication;
* manual overrides;
* unassigned-task review;
* explanation UI;
* plan-revision history;
* operational monitoring dashboard.

---

# 31. MVP implementation assumptions

The initial implementation SHALL use the following assumptions unless changed through an approved architecture decision record:

1. Periodic planning runs daily.
2. Rolling planning covers the next 48 hours.
3. The default rolling freeze window is 60 minutes.
4. OR-Tools uses CP-SAT for assignment and scheduling.
5. OR-Tools routing refines travel and sequence decisions.
6. Timefold rolling dispatch schedules active bundles only.
7. Dynamic implement switching is handled in periodic planning.
8. JSON Patch-based Timefold revisions remain disabled by default.
9. Canonical units are mandatory.
10. Every solver decision is traceable to an immutable planning snapshot.
11. Every data-quality fallback is explicit.
12. Every published plan revision is immutable.

---

# 32. Definition of done

The initial implementation is complete when an operator can:

1. load agricultural asset, implement, farmer-order, contract, inventory, cost, field, and weather data;
2. inspect validation and quality findings;
3. generate a reproducible seven-day periodic OR-Tools plan;
4. publish the plan;
5. receive an asset-breakdown or urgent-order event;
6. rebuild the rolling snapshot;
7. create a revised Timefold dispatch plan without changing frozen work;
8. inspect assignments, reservations, margins, risk, and unassigned reasons;
9. compare plan revisions;
10. trace every decision back to source records, schema versions, semantic mappings, quality policies, and solver versions.
