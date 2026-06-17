[Authoring manual](../authoring-domain-contracts.md) > 9. Reference tables and cheat-sheet

# 9. Reference tables and command cheat-sheet

## 9.1 missingValuePolicy values

From `fl_op/contracts/xopt.py:MissingValuePolicy`:

| Value | Behavior |
|---|---|
| `accept-optional` | Skip silently, no finding, do not drop the row. For by-design optional fields. |
| `reject-for-planning` | Emit an ERROR finding; the row is excluded from planning. Default for required canonical fields. |
| `accept-with-warning` | Keep the row, emit a WARNING finding. |
| `fallback-to-conservative-value` | Substitute a conservative default, emit a finding. |
| `impute` | Substitute an imputed value (treated like the conservative fallback path). |
| `accept-with-penalty` | Accept but mark for downstream penalty handling. |
| `quarantine` | Hold the row out for review. |
| `manual-review` | Flag for manual review. |

The first four cover the vast majority of real mappings.

## 9.2 planningUse tags (most common)

Free-form documentation/validation hints. Frequently used:
`identity`, `classification`, `geospatial`, `capacity`, `compatibility-filter`,
`cost`, `objective`, `routing`, `assignment`, `duration-estimation`,
`time-window`, `commitment`, `weather-window`, `material-availability`,
`monitoring`, `field-restriction`, `freeze`, `replanning-trigger`, `precedence`,
`alternative-selection`, `display`, `lineage`, `quality`.

## 9.3 quantity kinds

`identifier`, `categorical`, `categorical-set`, `text`, `ordinal`, `count`,
`number`, `measurement`, `money` (EUR), `area` (ha), `length` (m/km), `mass`
(kg), `volume` (L), `power` (kW), `energy` (kWh), `energy-flow-rate` (kWh/h),
`flow-rate`, `speed` (km/h, m/s), `angle` (deg), `duration` (s/min/d), `time`
(s), `timestamp`, `ratio` (%), `geometry`, `interval-set`, `rate-map`, `object`,
`evidence`.

## 9.4 Semantic-term vocabulary by namespace

This is the canonical thesaurus. A mapping may bind only to a term listed in
`contracts/canonical/model.yaml`. Grouped by namespace:

| Namespace | Meaning | Example terms (canonical unit) |
|---|---|---|
| `urn:xopt:identity:*` | Stable identifiers | `asset-id`, `location-id`, `task-id`, `observation-id`, `cost-rate-id`, `plan-id`, `revision-id` |
| `urn:xopt:attribute:*` | Descriptive attributes | `asset-type`, `mobility`, `latitude`/`longitude` (deg), `area`/`service-area` (ha), `work-quantity`, `work-quantity-unit`, `service-duration` (min), `load-demand` (kg), `load-material`, `polygon`, `soil-type`, `operation-type`, `priority-class`, `task-status`, `expected-revenue` (EUR), `event-type` |
| `urn:xopt:capability:*` | Abilities | `rated-power`/`required-power` (kW), `fuel-tank-volume` (L), `fuel-consumption-rate` (L/h), `energy-capacity` (kWh), `energy-consumption-rate` (kWh/h), `travel-speed` (km/h), `working-width` (m), `min`/`max-operating-speed` (km/h), `load-capacity` (kg), `load-capacities` (map), `compatible-operations`, `operator-certification`, `work-rates` (map) |
| `urn:xopt:availability:*` | Working time | `shift-start`, `shift-end` (s) |
| `urn:xopt:maintenance:*` | Maintenance master data | `last-service-at`, `service-interval` (d) |
| `urn:xopt:commitment:*` | Obligations | `deadline`, `lateness-penalty` (EUR), `hardness`, `type` |
| `urn:xopt:relationship:*` | Cross-entity refs | `home-depot`, `location`, `contract`, `entity-ref`, `alternative-group`, `depends-on`, `pickup-location`, `from`/`to-location`, `task` |
| `urn:xopt:inventory:*` | Material positions | `fuel` (L), `fertilizer` (kg), `energy` (kWh), `material-type`, `quantity` |
| `urn:xopt:forecast:*` | Predicted environment | `wind-speed` (m/s), `precipitation-rate` (mm/h), `soil-moisture` (%) |
| `urn:xopt:observation:*` | Measured values | `metric`, `value`, `state`, `unit`, `quality-flag` |
| `urn:xopt:time:*` | Timestamps/intervals | `observed-at`, `ingested-at`, `forecast-from`/`-to`, `valid-from`/`-to`, `workable-windows`, `planned-start`/`-end` |
| `urn:xopt:travel:*` | Travel-network edges | `travel-time` (s), `distance` (km), `network-mode` |
| `urn:xopt:cost:*` | Priced rates | `rate-type`, `unit-price` (EUR), `per-unit` |
| `urn:xopt:restriction:*` | Location restrictions | `prohibited-operations`, `restricted-windows` |
| `urn:xopt:plan:*` | Plan output | `planning-mode`, `status`, `reason-code`, `expected-cost`/`-margin` (EUR), `optimization-objective` |
| `urn:xopt:quality:*` | Quality summary | `finding-count`, `entities-excluded`, `observation-error-rates` |

## 9.5 Command cheat-sheet

```bash
# --- validate the canonical model and your pack ---
fl-op contracts canonical-validate                 # canonical model alone
fl-op contracts validate-domain --domain <d>       # pack covers canonical model
fl-op contracts validate [--write]                 # full suite; --write re-stamps
fl-op contracts check-generation --format avro      # generation hints complete
fl-op contracts generate --format avro|proto|es|parquet [--contract X] [--out-dir D]

# --- evolution gate ---
fl-op contracts evolution-check
fl-op contracts evolution-freeze

# --- data and planning ---
fl-op generate-data --domain <d> --seed 42 [--vehicles N --implements N --orders N --depots N] [--format csv|avro|parquet] [--data-path DIR]
ACTIVE_DOMAIN=<d> fl-op snapshot build --data latest --mode periodic [--effective-at TS]
ACTIVE_DOMAIN=<d> fl-op plan periodic --data latest [--objective time]
ACTIVE_DOMAIN=<d> fl-op plan rolling  --data latest --events events.jsonl
ACTIVE_DOMAINS=a,b fl-op plan periodic --data mixed-data    # shared-fleet

# --- inspect / serve / tune ---
fl-op solve --data latest
fl-op analyse --schedule latest
fl-op query-contract --data latest --schedule latest --order prospect.json
fl-op demo --data latest
fl-op serve
fl-op tune --data latest --trials 20 --seed 7

# convenience aliases: make contracts | check-gen | contracts-gen | canonical-validate
#                      make evolution-check | demo | serve | data
```

---

Previous: [8. Evolution, fingerprints, and documentation hygiene](08-evolution-and-hygiene.md) | Next: [10. Knowledge path: deep dive](10-knowledge-path.md)
</content>
