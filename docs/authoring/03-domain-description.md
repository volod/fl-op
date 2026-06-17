[Authoring manual](../authoring-domain-contracts.md) > 3. Phase A: domain description

# 3. Phase A: domain description methodology

Before writing a single YAML file, describe the domain in plain language by
answering six elicitation questions. The questions map one-to-one onto the
canonical entities, so the answers *are* your draft contract list.

## 3.1 The six questions

1. **What resources execute the work?** (-> `asset`)
   List every machine, tool, and person. For each, note: can it move under its
   own power (prime mover), is it attached/carried (related equipment), is it a
   person (operator), or is it fixed in place (stationary, monitored)? Note the
   static abilities (power, width, speed, capacity, certifications) and the
   working hours.

2. **Where does work happen and where do resources rest?** (-> `location`)
   Work sites (with coordinates and, if area-shaped, size or polygon) and depots
   (with material inventory: fuel, energy, consumables).

3. **What is demanded?** (-> `task`)
   The unit of work: its operation type, where, how much (area / quantity /
   fixed duration), by when (deadline), how valuable (revenue), how costly to
   miss (penalty per day), how urgent (priority class). Optional richer demand:
   mutually exclusive variants, precedence, pickup-and-delivery, load demand,
   workable windows.

4. **What obligations and conditions constrain it?**
   (-> `commitment`, `forecast`, location restrictions)
   Contractual deadlines/penalties (usually folded onto `task`), environmental
   windows (weather), and location restrictions (prohibited operations, curfews).

5. **What is being measured in the field?** (-> `observation`, monitoring)
   Sensor readings, telemetry, inspections. If you have stationary equipment that
   needs condition-based servicing, this is how service tasks get derived.

6. **What changes during execution?** (-> `execution-event`)
   The events that force a re-plan in streaming mode: task started/progressed/
   completed, asset unavailable, new observation, corrected row.

Plus two economic inputs that cut across the above:

7. **What does it cost to operate, and what are resources priced at?**
   (-> `cost-rate` + the profile's cost policy; see [Section 6](06-costing.md))

8. **How does the travel network look?** (-> `travel-link`, optional)
   If you have a distance matrix or road/air graph, model it; otherwise the
   engine falls back to great-circle distance and asset travel speed.

## 3.2 Worked example: the `utilities` domain

Power-line right-of-way vegetation management. Answers:

| Question | Domain answer | Canonical target |
|---|---|---|
| Resources | service trucks (self-driven) | `asset` / `mobile-prime-mover` |
| | mulcher/cutter heads (truck-mounted attachments) | `asset` / `implement` |
| | line crews (certified) | `asset` / `operator` |
| | pole-mounted condition sensors (fixed) | `asset` / stationary |
| Locations | depots/yards (hold fuel) | `location` / depot |
| | right-of-way spans (the work sites, area-shaped) | `location` / work site |
| Demand | vegetation-clearing jobs (CLEARING op, area ha, deadline, revenue, penalty) | `task` |
| Conditions | wind/rain windows (no cutting in high wind) | `forecast` + `weatherPolicy` |
| | protected spans (curfew windows, prohibited ops) | location restrictions |
| Measurements | pole-sensor battery/health readings | `observation` -> derived service |
| Dynamics | job started/done, truck breakdown | `execution-event` |
| Economics | fuel price, crew wage, machine wear, toll | `cost-rate` + profile |
| Network | optional road distance matrix | `travel-link` |

This single table is the input to the feasibility study and the blueprint for
the file list you will author.

## 3.3 The entity-mapping worksheet

Produce, for each contract you intend to author, a row:

```
contract id        canonical entity   asset role (assets only)   source file
service-trucks     asset              mobile-prime-mover         service-trucks.csv
cutter-heads       asset              implement                  cutter-heads.csv
crews              asset              operator                   crews.csv
pole-sensors       asset              (stationary)               pole-sensors.csv
yards              location           -                          yards.csv
spans              location           -                          spans.csv
clearing-jobs      task               -                          clearing-jobs.csv
weather            forecast           -                          weather.json
pole-readings      observation        -                          pole-readings.jsonl
prices             cost-rate          -                          prices.csv
travel-links       travel-link        -                          travel-links.csv
events             execution-event    -                          events.jsonl
```

The minimum runnable set is `asset` (at least one prime mover, one implement,
one operator), `location` (at least one depot and work sites), and `task`.
Everything else is optional and additive.

---

Previous: [2. Glossary and thesaurus](02-glossary-and-thesaurus.md) | Next: [4. Phase B: feasibility study](04-feasibility-study.md)
</content>
