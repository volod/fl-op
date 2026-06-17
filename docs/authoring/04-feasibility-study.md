[Authoring manual](../authoring-domain-contracts.md) > 4. Phase B: feasibility study

# 4. Phase B: feasibility study

The feasibility study answers two questions before you commit effort:

1. **Can this domain be expressed on the canonical ontology as it stands?**
   (coverage / fit)
2. **Should it be modeled now, or does it hit a known ontology gap?**
   (go / no-go)

## 4.1 Ontology-fit checklist

For every concept from the Phase A worksheet, confirm a canonical home exists.
Use the entity table in
[../reference/optimization-ontology.md](../reference/optimization-ontology.md) and the
vocabulary in [9.4](09-reference-tables.md#94-semantic-term-vocabulary-by-namespace).

- [ ] Every resource maps to `asset` with a role the profile can declare.
- [ ] Every place maps to `location` (work site or depot).
- [ ] Every demand maps to `task` with at minimum `taskId`, `locationRef`,
      `operationType`.
- [ ] Every economic value uses an existing money/quantity term
      (`expected-revenue`, `lateness-penalty`, cost-rate `unit-price`).
- [ ] Every measurable ability maps to an existing capability term, or you can
      justify a new one.
- [ ] Each entity's **required** canonical bindings are all covered by some
      source field (see the required-coverage rule below).

**Required-binding coverage rule.** `fl-op contracts validate` fails unless the
union of a domain's mappings covers every *required* canonical binding for each
entity it targets. The required fields per entity (from the canonical ODCS
contracts):

| Entity | Required bindings |
|---|---|
| `asset` | `asset.assetId` |
| `location` | `location.locationId`, `location.lat`, `location.lon` |
| `task` | `task.taskId`, `task.locationRef`, `task.operationType` |
| `forecast` | `forecast.forecastId` |
| `observation` | `observation.observationId`, `observation.entityRef`, `observation.metric`, `observation.observedAt` |
| `commitment` | `commitment.commitmentId` |
| `execution-event` | `event.eventId`, `event.eventType`, `event.observedAt`, `event.entityRef` |
| `travel-link` | `travelLink.linkId`, `travelLink.fromLocationRef`, `travelLink.toLocationRef`, `travelLink.travelTimeS` |
| `cost-rate` | `costRate.costRateId`, `costRate.rateType`, `costRate.unitPrice`, `costRate.perUnit` |

If a required binding has no source field, the domain is not yet feasible to plan
until you add that field to the physical schema.

## 4.2 Use-case coverage

Check that the optimization behavior your domain needs is *implemented*, not just
declared. The coverage matrix in
[../reference/optimization-ontology.md](../reference/optimization-ontology.md#optimization-use-cases-covered)
is authoritative. Implemented capabilities you can rely on include: heterogeneous
fleet routing with deadlines, multi-resource bundle assignment, profit-maximizing
task selection, rolling re-planning with stability, condition-based maintenance,
weather windows, material/inventory feasibility, operator qualification,
multi-stage precedence, mutually exclusive alternatives, capacity-constrained
delivery, pickup-and-delivery, restricted zones, mode-aware travel, and data-driven
cost rates.

## 4.3 Gap analysis (go / no-go gate)

Compare your needs against the **known ontology gaps** (the engine deliberately
does not yet model these; tracked in
[../future-improvements.md](../future-improvements.md)):

- Routing the path *around* a restricted sub-polygon (restrictions clip the work
  area by the unrestricted fraction instead).
- Fully-optional reload insertions (one reload per vehicle stays mandatory).
- Standalone `commitment` entities (today deadline/penalty are consumed
  task-embedded).
- Composite multi-domain policy *merging* (shared-fleet projection works, but the
  caller supplies one profile).
- 3D airspace deconfliction, charging-queue capacity (drone-specific).
- Per-link tolls and per-asset cost curves (cost is fleet-level today; see
  [Section 6](06-costing.md)).

Decision:

- **Go** if every concept has a canonical home and any unmet need is a "nice to
  have" rather than a hard requirement. Most domains are a clean go: the four
  shipped packs all run unchanged.
- **Go with a vocabulary addition** if you need a genuinely new *meaning* (a new
  capability or attribute). This is a small, reviewed change to
  `contracts/canonical/model.yaml`. Prefer reusing an existing term; add one only
  when no existing term carries the meaning.
- **No-go (defer)** if a hard requirement hits a known gap (for example you
  *must* route around obstacles, not scale the area). File or reference the gap
  in `future-improvements.md` and scope it as engine work, not a pack.

## 4.4 Output of the feasibility study

A one-page record: the entity-mapping worksheet, the required-coverage
confirmation, the list of capabilities relied on, and the go/no-go decision with
any vocabulary additions justified. This record is the spec for Phase C.

---

Previous: [3. Phase A: domain description methodology](03-domain-description.md) | Next: [5. Phase C: step-by-step authoring](05-authoring-steps.md)
</content>
