[Authoring manual](../authoring-domain-contracts.md) > 6. Costing methods

# 6. Costing methods

Cost is the default optimization objective, so getting the cost model right is
central. There are two layers: **engine cost constants** (the fallback) and
**data-driven cost rates** (the `cost-rate` entity, which wins when present).

## 6.1 The two layers

1. **Engine cost constants** (`src/fl_op/core/constants.py`, overridable by env
   var). These are the fleet-wide fallback prices used when no cost-rate row
   prices a resource:

   | Constant | Default | Meaning |
   |---|---|---|
   | `FUEL_COST_EUR_PER_L` | 1.45 | Diesel/fuel price per litre |
   | `ELECTRICITY_COST_EUR_PER_KWH` | 0.18 | Electric energy price |
   | `FERTILIZER_COST_EUR_PER_KG` | 0.55 | Consumable material price |
   | `LABOR_COST_EUR_PER_H` | 0.0 | Operator wage per operating hour |
   | `MACHINE_WEAR_COST_EUR_PER_H` | 0.0 | Wear/depreciation per operating hour |
   | `TOLL_COST_EUR_PER_KM` | 0.0 | Toll per km travelled |

   The operating rates (labor, wear, toll) default to **zero**, so those arc-cost
   terms vanish unless you price them (via constants or cost-rate data). Energy
   defaults are non-zero.

2. **Data-driven cost rates** (`cost-rate` entity). Each row is
   `{costRateId, rateType, unitPrice, perUnit, validFrom?, validTo?}`. When the
   snapshot carries a rate valid at planning time for a resource code, that rate
   overrides the constant. This is how the same engine prices fuel differently
   per run, or applies a time-bounded surcharge, from data alone.

   Canonical rate-type codes the engine interprets:
   `fuel`, `fertilizer` (material), `electricity`, `labor`, `machine-wear`,
   `toll`. Map your price rows' `resource_type` column onto these.

## 6.2 Cost-rate resolution algorithm

`solver/cost_rates.py:resolve_unit_price`: among all rates whose `rateType`
matches and whose `[validFrom, validTo)` window contains the planning time
(absent bounds are open), the rate with the **latest `validFrom`** wins. If no
rate applies, the engine constant `default` is used. The resolved prices are
frozen into a `ResourcePrices` record that is picklable across the worker pool,
so routing arc costs, dispatch margins, the greedy warm-start score, and KPIs are
all priced from one consistent source.

## 6.3 Where costs enter the optimization

The objective maximizes total net margin over selected tasks. For a task `t`
served by bundle `(m, r)` (see
[../algorithms/01-problem-formulation.md](../algorithms/01-problem-formulation.md#7-objective-function)):

```
margin_t(m, r) = revenue_t
               - energy_cost_t(m, r)       # on-task energy
               - reposition_cost_t(m)      # empty travel to the site
               - material_cost_t(r)        # consumable draw (0 if none)
               - operating_cost_t(m, r)    # labour + wear over travel+service hours
               - toll_cost_t(m)            # per-km toll over travelled distance
```

Component formulas:

- `energy_cost = duration_h * energy_consumption_rate_m * price(energy_type)`
  where the energy type is fuel (L) or electricity (kWh) per the prime mover.
- `reposition_cost = haversine(loc_m, site_t) / travel_speed * consumption_rate * energy_price`.
- `material_cost = area * material_kg_per_area * price(material)`; zero for
  non-material operations. `material_kg_per_area` comes from the profile's
  `materialDemand`.
- `operating_cost = (travel_h + service_h) * operating_eur_per_h`, where
  `operating_eur_per_h = labor_eur_per_h + machine_wear_eur_per_h`. A bundle that
  finishes faster therefore saves wages and wear, not just energy.
- `toll_cost = distance_km * toll_eur_per_km` (geodesic arc distance today,
  applied uniformly per leg).

`duration_{m,r} = work_quantity / effective_rate_{m,r}`. For area work the
fallback coverage model is `effective_rate = working_width * field_speed / 10`
[ha/h]; a declared `work-rates` map overrides it per unit.

Power compatibility (which `(m, r)` pairs are even allowed) uses
`POWER_MARGIN_PCT` (default 10): `rated_power_m >= required_power_r * (1 - 10/100)`.

## 6.4 Costing methods you can choose

| Method | How | When to use |
|---|---|---|
| Constant fleet pricing | Leave cost-rate data out; rely on `core/constants.py` (or env overrides) | Quick start, uniform fleet, smoke tests |
| Data-driven energy/material | Author a `prices` dataset mapped to `cost-rate` with `fuel`/`electricity`/`fertilizer` rows | Real, possibly time-varying resource prices |
| Time-based operating cost | Add `labor` and/or `machine-wear` cost-rate rows (or set the env constants) | When finishing faster has real wage/wear value; makes `--objective time`-like trade-offs show up under the cost objective |
| Distance toll | Add a `toll` rate (EUR/km) | Toll-road or per-distance surcharge fleets |
| Validity-windowed pricing | Set `validFrom`/`validTo` on rate rows | Seasonal or contract-period price changes |

## 6.5 Worked costing example (utilities)

A `prices.csv` mapped onto `cost-rate`:

```
price_id, resource_type, price_eur, per_unit, valid_from,            valid_to
p_fuel,   fuel,          1.60,      L,        2026-01-01T00:00:00Z,
p_crew,   labor,         42.00,     h,        2026-01-01T00:00:00Z,
p_wear,   machine-wear,  18.00,     h,        2026-01-01T00:00:00Z,
```

Planning a 30 ha CLEARING job with a cutter (working width 3 m, field speed
10 km/h => effective rate 3 ha/h) on a truck burning 28 L/h, sited 12 km from the
truck's current position (travel speed 40 km/h), revenue EUR 9000:

- `service_h = 30 / 3 = 10 h`
- `energy_cost = 10 * 28 * 1.60 = EUR 448`
- `travel_h = 12 / 40 = 0.3 h`; `reposition energy = 0.3 * 28 * 1.60 = EUR 13.44`
- `operating_cost = (0.3 + 10) * (42 + 18) = 10.3 * 60 = EUR 618`
- `toll_cost = 0` (no toll rate)
- `margin = 9000 - 448 - 13.44 - 618 = EUR 7920.56`

With `labor`/`machine-wear` priced, a faster cutter (higher effective rate, fewer
service hours) now wins on margin, not just on time, because operating cost falls
with duration. With those rates left at the zero default, only energy and
material differentiate bundles.

The residual open cost-model work (per-link tolls, fixed per-visit service fees,
per-vehicle/per-operator rates) is tracked in
[../future-improvements.md](../future-improvements.md#22-cost-model-expansion).

---

Previous: [5. Phase C: step-by-step authoring](05-authoring-steps.md) | Next: [7. Runtime feasibility checks and verification](07-runtime-feasibility.md)
</content>
