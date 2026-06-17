[Authoring manual](../authoring-domain-contracts.md) > 2. Glossary and thesaurus

# 2. Glossary and thesaurus

## 2.1 Core terms

| Term | Definition |
|---|---|
| **Data contract** | A governed, versioned declaration of a dataset's structure and meaning. In fl-op a "data contract" is the pair (physical ODCS schema + canonical mapping), registered in `registry.yaml`. |
| **ODCS** | Open Data Contract Standard. The schema format used for the *physical* contracts (`kind: DataContract`, `apiVersion: v3.0.0`). |
| **Canonical model** | The domain-neutral ontology the engine consumes: entities + a controlled vocabulary of semantic terms. Declared in `contracts/canonical/model.yaml`. `canonicalModelRef: urn:xopt:model:canonical:0.1.0`. |
| **Canonical entity** | One of the engine's abstractions: `asset`, `location`, `task`, `forecast`, `observation`, `commitment`, `execution-event`, `travel-link`, `cost-rate`, and the output entity `plan`. |
| **Semantic term** | A controlled meaning (`urn:xopt:capability:rated-power`) that fixes value type, quantity kind, and canonical unit. A mapping may only bind to a term that exists in the vocabulary. |
| **Binding** | The canonical attribute path a physical field projects onto (for example `asset.capabilities.ratedPower`). |
| **Canonical binding** | The `customProperties` block (in canonical ODCS) or the `fieldMappings` entry (in a domain mapping) that ties a field to a binding + semantic term + unit. |
| **Mapping document** | `kind: CanonicalMapping`. The per-contract file that declares every field's binding. The authority for semantics. |
| **Asset role** | The role an asset plays in a bundle: `mobile-prime-mover`, `implement` (related equipment), `operator`, or a stationary variant. Declared in the mapping's `metadata.assetRole` and resolved by the profile's `bundleGeneration.roles`. |
| **Optimization profile** | `kind: OptimizationProfile`. The per-domain policy bundle (`profile.yaml`). |
| **Domain pack** | A directory under `contracts/domains/<domain>/` holding `odcs/`, `mappings/`, and `profile.yaml`. |
| **Registry** | `contracts/registry.yaml`. Wires domains, profiles, and contracts together and stores fingerprints. |
| **Snapshot** | An immutable, reproducibly-hashed canonical planning input built from source data (`fl-op snapshot build`). Identical inputs hash to identical plans. |
| **Bundle** | The triple the solver assigns to a task: prime mover + related equipment + operator. |
| **Plan / Assignment / UnassignedTask** | The output entities: the plan envelope, each served task's bundle+schedule, and each rejected task's reason code. |
| **Quantity kind** | The dimension of a value (`power`, `area`, `mass`, `money`, `duration`, ...). Fixed by the semantic term. |
| **Canonical unit** | The single unit every domain's values are normalized to for that term (kW, ha, kg, EUR, s, ...). |
| **planningUse** | A list of tags declaring how a bound field is used downstream (`capacity`, `compatibility-filter`, `objective`, `routing`, ...). Documentation/validation hints; free-form. |
| **missingValuePolicy** | What happens when a bound field is missing or unparseable (reject the row, warn, fall back, skip). See [9.1](09-reference-tables.md#91-missingvaluepolicy-values). |
| **Fingerprint** | A stored hash: `avroParsingFingerprint` (structural) and `optimizationMetadataHash` (semantic, from the mapping). Guards against silent drift. |
| **Evolution gate** | The semver-classified review of contract/mapping changes (`evolution-check` / `evolution-freeze`). |

## 2.2 Thesaurus: source word -> canonical concept

This is the translation table you will run in your head while authoring. Any
domain word maps to a canonical entity + role + capability. The four shipped
packs and our running `utilities` example:

| Your domain says ... | Canonical entity | Role / field |
|---|---|---|
| tractor, excavator, service-truck, UGV, UAV | `asset` | role `mobile-prime-mover` |
| sprayer, plow, bucket, breaker, cutter-head, payload-module, service-kit | `asset` | role `implement` (related equipment) |
| operator, driver, crew, technician, pilot | `asset` | role `operator` |
| pole sensor, weather station, fixed signage | `asset` | `mobility: stationary` (monitored, not dispatched) |
| field, work site, span, delivery point, job site | `location` | a work site (carries lat/lon/area) |
| depot, yard, hub, service depot | `location` | a depot (carries `inventory.*`) |
| order, job, delivery, clearing job, work request | `task` | the unit of work |
| engine kW, machine power | `asset.capabilities.ratedPower` | capability `rated-power` |
| implement draw kW, attachment power | `asset.capabilities.requiredPower` | capability `required-power` |
| swath, cut width | `asset.capabilities.workingWidth` | capability `working-width` |
| operation: SPRAYING, EXCAVATION, CLEARING | `task.operationType` + asset `compatible-operations` | attribute `operation-type` |
| revenue, contract value | `task.revenueValue` | `expected-revenue` (EUR) |
| deadline, due date | `task.deadline` | `deadline` |
| late penalty | `task.penaltyPerDay` | `lateness-penalty` (EUR) |
| fuel price, kWh price, material price, toll | `cost-rate` row | `rate-type` + `unit-price` |
| wind, rain, soil moisture forecast | `forecast` | `wind-speed`, `precipitation-rate`, `soil-moisture` |
| sensor reading, telemetry, inspection result | `observation` | `metric` + `value`/`stateValue` |
| road/air arc, distance-matrix entry | `travel-link` | `travel-time` + `network-mode` |
| "task started/done", "asset down", "new reading" | `execution-event` | `eventType` (rolling re-solve trigger) |

The full controlled vocabulary, grouped by namespace, is the canonical thesaurus
in [9.4](09-reference-tables.md#94-semantic-term-vocabulary-by-namespace). When
you find yourself wanting a term that is not there, that is a signal to read the
feasibility study ([Phase B](04-feasibility-study.md)) before adding a vocabulary
entry.

---

Previous: [1. The mental model](01-mental-model.md) | Next: [3. Phase A: domain description methodology](03-domain-description.md)
</content>
