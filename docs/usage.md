# fl-op Usage Guide

Detailed command-by-command walkthrough for the fl-op CLI. For a high-level
overview and Quick Start, see the top-level [`README.md`](../README.md). For the
current pipeline architecture, see
[`docs/current-implementation.md`](current-implementation.md).

All commands accept `--data latest` / `--schedule latest` to pick up the most
recent run automatically. Every run writes to `.data/<method>/<timestamp>/` and
never overwrites a previous run.

---

## Batch solver workflow

### Step 1 - Generate data

```bash
.venv/bin/fl-op generate-data \
    --vehicles 100 \
    --implements 400 \
    --orders 250 \
    --depots 50 \
    --seed 42
```

Those are the CLI defaults (set via environment variables or `.env`). The
registry active domain is `drone_logistics`, so the same run can be started
with:

```bash
.venv/bin/fl-op generate-data --seed 42
```

For large-scale runs, pass the desired counts explicitly:

```bash
.venv/bin/fl-op generate-data --vehicles 3000 --implements 20000 --orders 2500 --depots 50 --seed 42
```

Default drone logistics output written to `.data/generate-data/<timestamp>/`:

```
ugvs.avro                uavs.avro
payload-modules.avro     drone-operators.avro
logistics-hubs.avro      delivery-locations.avro
restricted-zones.avro    delivery-orders.avro
travel-links.avro        prices.avro
weather.json             metadata.json
```

The default format is avro. Pass `--format csv` or `--format parquet` to
generate a different format. The format is recorded in `metadata.json` and
auto-detected by downstream commands (`solve`, `snapshot build`, etc.).

To load real fleet data instead of synthetic:

```bash
.venv/bin/fl-op generate-data \
    --vehicles 1500 --implements 6000 --orders 2500 --depots 500 \
    --data-path /path/to/real/csvs/
```

Real CSVs take priority; missing fields fill from synthetic distributions.

To generate and plan another registered domain pack, select the domain at
generation time and activate it for planning with the `ACTIVE_DOMAIN` override.
Counts map onto each domain's entities: drone logistics uses
vehicles=UGV/UAV fleet, implements=payload modules, orders=deliveries,
depots=logistics hubs; construction uses vehicles=machines,
implements=attachments, orders=jobs, depots=yards; roadside uses
vehicles=service vehicles, implements=service kits, orders=signage assets,
depots=service depots.

```bash
.venv/bin/fl-op generate-data --seed 42
.venv/bin/fl-op plan periodic --data latest

.venv/bin/fl-op generate-data --domain agricultural --seed 42
ACTIVE_DOMAIN=agricultural .venv/bin/fl-op plan periodic --data latest

.venv/bin/fl-op generate-data --domain construction --seed 42
ACTIVE_DOMAIN=construction .venv/bin/fl-op plan periodic --data latest

.venv/bin/fl-op generate-data --domain roadside --vehicles 4 --implements 8 --orders 10 --depots 2 --seed 42
ACTIVE_DOMAIN=roadside .venv/bin/fl-op plan periodic --data latest
```

Available generator domains come from `contracts/registry.yaml` domain entries;
each entry declares the Python generator callable used by the `generate-data`
command's `--domain` option.

Shared-fleet planning can project several selected domain packs into one
canonical snapshot/solve when the source directory has been staged with the
needed datasets. Select the set with `ACTIVE_DOMAINS` (or adapter config
`domains` in Python). Policy merging is not automatic; the plan call still uses
one optimization profile.

```bash
ACTIVE_DOMAINS=agricultural,construction .venv/bin/fl-op snapshot build --data mixed-data --mode periodic
ACTIVE_DOMAINS=agricultural,construction .venv/bin/fl-op plan periodic --data mixed-data
```

---

### Step 2 - Solve

```bash
.venv/bin/fl-op solve --data latest
```

Or point to a specific dataset directory:

```bash
.venv/bin/fl-op solve --data .data/generate-data/<timestamp>/
```

**Example output** (small run -- 50 vehicles / 200 implements / 20 orders):

```
Fleet Optimization Schedule Report
========================================
Dispatched:   17
Infeasible:   3
Total margin: 347629.82 EUR
Greedy base:  386670.36 EUR
Margin delta: -39040.54 EUR
Total fuel:   6905.8 L

Infeasibility reasons:
  NO_COMPATIBLE_BUNDLE: 3
```

Output written to `.data/solve/<timestamp>/`:

```
schedule.json          # full dispatch packages (one per assigned order)
schedule_report.txt    # human-readable summary
schedule_kpis.json     # machine-readable KPIs for dashboards
infeasible_orders.json # orders that could not be assigned with reason codes
```

**Sample dispatch package** from `schedule.json`:

Dispatch packages are keyed by canonical names (`prime_asset_id`,
`related_asset_id`, `operator_asset_id`, `task_id`, `depot_ref`):

```json
{
  "dispatch_id": "a9720e7b-0a72-4e52-80b6-bbb6c86f2fea",
  "prime_asset_id": "vehicle_00001",
  "related_asset_id": "implement_000001",
  "operator_asset_id": "operator_00009",
  "task_id": "order_000001",
  "depot_ref": "depot_0004",
  "scheduled_start": "2026-05-21T16:55:12+00:00",
  "scheduled_end":   "2026-05-22T16:55:12+00:00",
  "estimated_fuel_l": 436.56,
  "estimated_margin_eur": 1358.08,
  "route_waypoints": [{"lat": 46.640382, "lon": 33.091568}]
}
```

**Machine-readable KPIs** from `schedule_kpis.json`:

```json
{
  "schema_version": "1.0",
  "n_dispatched": 17,
  "n_infeasible": 3,
  "total_estimated_margin_eur": 347629.82,
  "greedy_baseline_margin_eur": 386670.36,
  "solver_improvement_eur": -39040.54,
  "total_fuel_l": 6905.79,
  "total_fertilizer_kg": 1240.50,
  "infeasibility_reasons": {"NO_COMPATIBLE_BUNDLE": 3}
}
```

`greedy_baseline_margin_eur` is the admitted-task greedy warm-start margin
estimated with the same fuel/material prices as the final plan.
`solver_improvement_eur` is the signed final-plan margin delta against that
baseline; it can be negative when routing feasibility, assignment-count
priority, or other constraints trade away margin.

To inspect the latest solver run in the terminal:

```bash
.venv/bin/fl-op analyse --schedule latest
```

This prints served/rejected percentages, vehicle and implement usage,
economic KPIs, top-used resources, and ASCII bar charts by cluster/day.

---

### Step 3 - Reschedule after field events

Create an events file describing what changed in the field:

```json
[
  {"type": "mark_started", "order_id": "order_000001"},
  {"type": "mark_started", "order_id": "order_000003"}
]
```

Then reschedule:

```bash
.venv/bin/fl-op reschedule \
    --data latest \
    --schedule latest \
    --events events.json
```

Orders with status `started` are frozen. The solver re-optimises remaining
orders with the current fleet state. Output goes to `.data/reschedule/<ts>/`
and includes a `plan_diff.json` (structured diff: `frozen_orders`, `added`,
`removed`, `rescheduled`, `newly_infeasible`) and `plan_diff.txt` (human
summary of what changed vs. the previous schedule).

---

### Step 4 - Query a new contract

Before accepting a new order, check feasibility and get margin estimates without
running the full solver:

```json
{
  "order_id": "prospect_001",
  "operation_type": "SPRAYING",
  "field_id": "field_000099",
  "area_ha": 120,
  "deadline": "2026-06-15T00:00:00+00:00",
  "penalty_per_day_eur": 800,
  "estimated_revenue_eur": 18000
}
```

```bash
.venv/bin/fl-op query-contract \
    --data latest \
    --schedule latest \
    --order prospect_001.json
```

Returns top-3 prime-mover + related-equipment options with conflict risk. The
candidate keys are canonical (`prime_asset_id` / `related_asset_id`):

```json
{
  "feasible": true,
  "candidates": [
    {
      "prime_asset_id": "vehicle_00042",
      "related_asset_id": "implement_005301",
      "estimated_margin_eur": 14200.00,
      "schedule_conflict_risk": "low"
    },
    {
      "prime_asset_id": "vehicle_00017",
      "related_asset_id": "implement_002847",
      "estimated_margin_eur": 13850.00,
      "schedule_conflict_risk": "medium"
    }
  ]
}
```

Response in under 5 seconds at production scale. No solver call involved.

---

## Declarative data-contract layer (batch + stream)

On top of the solver, fl-op provides a solver-neutral data-contract platform.
ODCS contracts are the single source of truth for all semantic metadata (field
bindings, canonical units, planning use, quality policies). Avro, Protobuf, and
Elasticsearch schemas are generated from ODCS and carry no embedded semantic
blocks. The mapping engine reads ODCS bindings to translate governed source
fields into stable canonical abstractions (`Asset`, `Capability`, `Task`,
`OperationalBundle`, ...), builds an immutable, reproducibly-hashed planning
snapshot, and optimizes it in both batch (periodic) and stream (rolling) mode.
See [`docs/current-implementation.md`](current-implementation.md).

```bash
# Check ODCS contracts have complete generation hints for a given format.
.venv/bin/fl-op contracts check-generation --format avro   # or proto, es, parquet
# or: make check-gen

# Generate physical schemas from ODCS contracts (output to contracts/generated/).
.venv/bin/fl-op contracts generate --format avro            # or proto, es, parquet
# or: make contracts-gen   (generates all four formats)

# Validate the contract suite: generated schemas, canonical mappings,
# fingerprints, profiles, and metadata-loss guards.
.venv/bin/fl-op contracts validate
# or: make contracts

# Schema evolution: check every ODCS contract against its committed reviewed
# history (contracts/evolution/), enforcing pairwise version-bump policy:
# added optional fields need a minor bump, anything breaking needs a major
# bump, and mapping-semantic hash drift must be reviewed in the same gate.
.venv/bin/fl-op contracts evolution-check     # or: make evolution-check
# After a reviewed contract/mapping change (with the policy-required version
# bump where structural schema changed), record the new history snapshot:
.venv/bin/fl-op contracts evolution-freeze    # or: make evolution-freeze

# Build an immutable, reproducibly-hashed planning snapshot from source data.
.venv/bin/fl-op snapshot build --data latest --mode periodic

# Periodic (batch) plan: canonical assignments + normalized unassigned reasons.
.venv/bin/fl-op plan periodic --data latest

# Rolling (stream) dispatch: one immutable revision per execution event, with a
# freeze window protecting started/imminent tasks and a plan-instability penalty.
.venv/bin/fl-op plan rolling --data latest --events events.jsonl

# Explain why every changed assignment moved between rolling revisions. Plain
# re-solve changes include solver attribution from plan scores when available.
.venv/bin/fl-op plan diff-revisions --plan latest

# Full story end to end (contracts -> snapshot -> batch -> stream).
.venv/bin/fl-op demo --data latest      # or: make demo
```

Why this matters: the source word (`tractor`, `sprayer`, `operator`) is
irrelevant -- the solver reasons about capabilities and roles. Every solver
decision traces back through the snapshot hash to source records, schema
versions, and quality findings. The rolling adapter is Python-native OR-Tools
and uses the same solver chain as periodic planning.

Artifacts land under `.data/snapshot/`, `.data/plan-periodic/`, and
`.data/plan-rolling/<ts>/revisions/<n>/`.

---

## Parameter tuning and experiment tracking

`fl-op tune` runs a seeded Optuna TPE study over the tunable solver
parameters (cluster target size, greedy score weights, per-cluster time
limit) against recorded KPI baselines:

```bash
.venv/bin/fl-op tune --data latest --trials 20 --seed 7
.venv/bin/fl-op tune --data latest --extra-data .data/generate-data/20260601T120000 --jobs 4
.venv/bin/fl-op tune-promote --best-params .data/tune/20260612T090000/best_params.json --reviewed-by ops
```

Artifacts land under `.data/tune/<timestamp>/`: `baseline.json`,
`trials.json`, and `best_params.json` (best parameters plus the improvement
over the baseline objective). By default the study records a Pareto frontier:
maximize business objective, minimize instability, and minimize wall time.
`--extra-data` averages the objective across datasets, and `--jobs > 1` uses
Optuna RDB storage (`study.db` in the run directory unless `--storage` or
`TUNE_STORAGE_URI` is set).

`fl-op tune-promote` writes the reviewed overlay
`.data/tune/solver-parameters-tuned.json`; periodic and rolling plan runs load
that overlay on top of the checked-in profile defaults. With
`MLFLOW_LOGGING_ENABLED=1`, every trial, the tuning baseline, and every
periodic/rolling plan run are logged as MLflow runs (KPIs, version dimensions,
solve-telemetry summary) to a local SQLite store under `.data/mlruns` -- or to
`MLFLOW_TRACKING_URI` if set -- so parameter experiments are comparable across
datasets.

---

## Serving API

`fl-op serve` exposes the published planning state over HTTP (loopback by
default; FastAPI + uvicorn):

```bash
.venv/bin/fl-op serve            # or: make serve
```

Plan and feasibility routes are public only when `SERVE_AUTH_TOKEN` is unset
for local development. Set `SERVE_AUTH_TOKEN` to require
`Authorization: Bearer <token>`; binding outside loopback, for example
`SERVE_HOST=0.0.0.0`, requires the token. `/health` remains unauthenticated
for load balancers. By default the API reads artifacts from `$DATA_DIR`; set
`SERVE_ARTIFACT_ROOT=/mnt/fl-op-artifacts` to serve a shared mounted artifact
tree from several instances.

| Endpoint | Meaning |
|----------|---------|
| `GET /health` | liveness probe |
| `GET /plans/{periodic\|rolling}` | published run ids, newest last |
| `GET /plans/{mode}/{run_id}` | plan document (`latest` allowed; rolling returns the newest revision) |
| `GET /plans/rolling/{run_id}/revisions` | rolling revision summary |
| `GET /plans/rolling/{run_id}/revisions/{n}` | one revision's plan |
| `POST /feasibility` | query-contract evaluation for a new order |

`POST /feasibility` takes `{"order": {...}, "data": "latest", "schedule":
"latest"}` and returns the same feasibility/candidate result as
`fl-op query-contract`, without writing run artifacts. `data` and `schedule`
may also be run ids (`20260612T120000`) or artifact-root-relative paths under
`generate-data/` and `solve/`.

---

## Event-bus ingestion

Rolling planning reads execution events from the source selected by
`EVENT_SOURCE_KIND`: `jsonl` (default) reads the `--events` file; `kafka`
consumes `EVENT_BROKER_TOPIC` from `EVENT_BROKER_BOOTSTRAP_SERVERS` instead
(requires the broker extra: `uv sync --extra broker`). Both sources validate
events identically, and a rolling run drains the visible backlog
(`EVENT_BROKER_MAX_EMPTY_POLLS` consecutive empty polls) before publishing
revisions.

Integrations can register more source kinds with
`fl_op.stream.broker.register_event_source(kind, factory,
uses_dedup_store=True)`. Use the dedup flag for sources that may redeliver
events after a process restart; leave it off for intentionally replayed files
or test feeds.

---

## Benchmarks

| Scale | generate-data | solve (8 cores) |
|-------|--------------|-----------------|
| Smoke test (10v / 30i / 5o / 2d) | < 1 s | < 5 s |
| Default / CI (100v / 400i / 250o / 50d) | < 5 s | < 60 s |
| Large scale (3000v / 20000i / 2500o / 50d) | < 60 s | 5-10 min |

Run the default benchmark manually (not a CI target):

```bash
make data
```

Override the Makefile defaults from the command line:

```bash
make data VEHICLES=3000 IMPLEMENTS=20000 ORDERS=2500 DEPOTS=50
```

---

## Output directory layout

```
$DATA_DIR/                       # default: .data/ -- override via DATA_DIR env var
  generate-data/<timestamp>/     # dataset for one generate-data run
  solve/<timestamp>/             # schedule.json, schedule_kpis.json,
                                 # schedule_report.txt, infeasible_orders.json
  reschedule/<timestamp>/        # same outputs as solve + plan_diff.json,
                                 # plan_diff.txt
  query-contract/<timestamp>/    # query_result.json
  snapshot/<timestamp>/          # snapshot.json (canonical + reproducible hash)
  plan-periodic/<timestamp>/     # plan.json + snapshot.json (batch plan)
  plan-rolling/<timestamp>/      # revisions/<n>/plan.json + revisions_summary.json
  revision-diff/<timestamp>/     # revision_diff.json + revision_diff.txt
  tune/<timestamp>/              # baseline.json, trials.json, best_params.json
  tune/solver-parameters-tuned.json       # reviewed tuned solver overlay
  cache/compat-matrix/            # content-keyed compatibility matrices
  cache/preprocessing/            # candidate-filter and cluster-spec caches
  cache/feasibility/              # exact /feasibility response cache
  cache/solver-feedback/          # worker RSS and LNS objective-delta feedback
  mlruns/                        # local MLflow store (MLFLOW_LOGGING_ENABLED=1)
  quality/observation-error-rates.jsonl   # append-only cross-run error-rate trend
  quality/service-prognosis.jsonl         # per-revision service-prognosis outcomes
  quality/completion-lead-times.jsonl     # task completion lead/schedule errors
```

All solve/reschedule JSON files include `schema_version: "1.0"`, `run_metadata`
(timestamp, command args, dataset path), and `run_telemetry` (wall time, CPU
time, phase breakdown, peak RSS). Old runs are never overwritten; each run gets
its own timestamp directory.
