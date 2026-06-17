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
and no-fly exclusions. Checked-in drone tuning defaults live in
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
