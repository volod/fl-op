[Implementation guide](../current-implementation.md) > Architecture and domain packs

# Architecture and domain packs

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

Four domain packs exist today and are runnable end to end with registered
contracts, data generators, and profiles: drone logistics, agricultural custom
services, construction earthworks, and roadside infrastructure. Drone logistics
is the default domain. It models autonomous last-mile delivery for
manufacturers, restaurants, and online stores with mixed uncrewed ground
vehicles (`UGV`) and uncrewed aerial vehicles (`UAV`), payload modules,
operators, logistics hubs, delivery points, road/air travel links, weather,
restricted zones, explicit battery kWh capacity/use, electricity cost-rate
rows, and compatibility fuel-equivalent fields for older integrations. Drone
datasets also write `drone-scenarios.json` and `scenario-events.jsonl`; drone
scenarios cover heavy manufacturer deliveries, urgent restaurant meals,
ordinary online-store parcels, bad-weather periods, no-fly activation,
road-only destinations, UAV speed wins, UGV feasibility wins, hub energy
scarcity, and asset outage events. Drone plans include
`score.drone_logistics_kpis`: fill rate, on-time rate, delivery margin, mode
split, UGV/UAV utilization, support-team utilization, unassigned reasons,
energy or fuel-equivalent usage, rolling churn, weather-blocked UAV tasks,
and no-fly exclusions, plus two post-solve fidelity passes embedded under
`airspace_deconfliction` and `charging_schedule` (see below).

Two domain-fidelity passes run after the shared solve and are embedded in the
drone KPI block (`planning/airspace.py`, `planning/charging.py`):

- **4D airspace deconfliction** (`airspace_deconfliction`) models the vertical
  and temporal dimensions the routing solver does not. Each aerial (`UAV`)
  flight's lateral path is reconstructed from canonical geometry (home hub,
  pickup, drop-off), and its airborne window spans the inbound transit leg as
  well as on-task service (`planned_start` minus the path length at the asset's
  travel speed, so proximity reflects when the drone is actually flying). Two
  flights *conflict* when the minimum lateral distance between their paths is
  below `AIRSPACE_HORIZONTAL_SEPARATION_M` (a local meter projection via
  `core/geometry.segment_min_distance_m`) and their airborne windows are within
  `AIRSPACE_TIME_BUFFER_S`. A greedy degree-ordered corridor colouring first
  places conflicting flights into distinct vertically separated altitude
  corridors (`AIRSPACE_CORRIDOR_COUNT` levels spaced by
  `AIRSPACE_VERTICAL_SEPARATION_M` from `AIRSPACE_BASE_ALTITUDE_M`). When the
  conflict graph needs more corridors than exist, a deadline-bounded
  temporal-separation pass holds the later same-corridor flight until the
  corridor clears (a deterministic per-corridor list schedule, capped at each
  flight's deadline slack so no hold misses a delivery); only conflicts that
  cannot clear within slack stay residual. The computed holds are then **applied
  to the published dispatch** (`apply_airspace_holds` in both adapters re-times a
  held flight's `planned_start`/`planned_finish`), so the plan dispatches the
  deconflicted schedule rather than only annotating it, and the held finish times
  flow into the charging pass's arrival times. Frozen/pinned flights carry zero
  slack and are never moved, so a rolling revision's committed work is untouched.
  The pass reports per-flight corridor/altitude and applied hold, the
  corridor/timed/residual split of the conflict pairs, flights held with
  total/max hold, corridors used, and peak concurrent flights.
- **Charging-station scheduling** (`charging_schedule`) models hub recharge
  throughput, which the solver consumes only as battery *inventory*. Each used
  asset's spent energy (consumption rate x on-plan busy hours, capped at battery
  capacity) is replenished at its home hub, whose `charging_slots` parallel bays
  share its aggregate `charging_power_kw`. Sessions take the earliest-free bay
  and queue when every bay is busy, so the pass reports per-hub utilization,
  queue waits, peak queue depth, peak concurrent charging, and each asset's
  recharge turnaround (queue wait + charge time) with a `ready_at` time and an
  at-risk count (`CHARGING_TURNAROUND_RISK_S`) -- the readiness signal a
  queue-aware reassignment would consume. Hubs carry `chargingPowerKw`/
  `chargingSlots` as generic canonical Location capacity fields
  (`urn:xopt:capacity:charging-power` / `:charging-slots`), reusable by any
  depot domain. Checked-in drone tuning defaults live in
`contracts/domains/drone_logistics/tuning.yaml`; they cover UAV weather
thresholds, UGV road-speed buckets, delivery/drop penalties, customer-class
deadline penalties, UGV/UAV fleet mix, payload capacity classes, energy cost
rates, cluster-size limits, LNS budgets, and rolling instability penalties.
Drone rolling replay scenarios exercise `task.started`, `asset.unavailable`,
weather degradation, no-fly activation, hub inventory or energy shortage,
urgent order insertion, and customer cancellation. The roadside pack is
monitoring-driven: service vehicles, service kits, and technicians dispatch
`EQUIPMENT_SERVICE` visits derived from inspection findings about stationary
signage and sensor assets along road segments.
The construction pack is earthworks-native: volume-shaped jobs (excavation,
trenching, hauling) carry m3 quantities and volume-moving attachments declare
m3-per-hour work rates, so durations come from the rate, not an area proxy.
By default one domain is active per run: registry.yaml `activeDomain`,
currently `drone_logistics`, overridable with `ACTIVE_DOMAIN=agricultural`,
`ACTIVE_DOMAIN=construction`, or `ACTIVE_DOMAIN=roadside`.
Shared-fleet runs can select several packs with
`ACTIVE_DOMAINS=agricultural,construction` or by passing adapter config
`domains=[...]`; the snapshot and solver projection then use the union of the
selected domains' canonical bindings. The `generate-data` command's `--domain`
option defaults to the registry active domain and resolves the generator
callable declared by that domain's registry entry. Profile input contract refs
resolve inside the active domain
(`operators` can mean construction operators in the construction profile).
Solver inputs resolve their binding tables by canonical entity and asset role,
never by contract id, so switching domains or unioning selected domains needs
no solver change. Multi-domain policy merging is not automatic: the caller
still supplies one optimization profile.
</content>
