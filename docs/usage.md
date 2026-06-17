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
drone-scenarios.json     scenario-events.jsonl
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
# Cost is the default objective; use --objective time for minimal-time runs.
.venv/bin/fl-op plan periodic --data latest --objective time

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
  "energy_resource_type": "fuel",
  "estimated_energy_quantity": 436.56,
  "estimated_energy_unit": "L",
  "estimated_energy_cost_eur": 633.01,
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
  "total_energy_cost_eur": 10013.40,
  "total_energy_quantity_by_type": {"fuel": 6905.79},
  "total_energy_quantity_by_unit": {"L": 6905.79},
  "total_fertilizer_kg": 1240.50,
  "infeasibility_reasons": {"NO_COMPATIBLE_BUNDLE": 3}
}
```

`greedy_baseline_margin_eur` is the admitted-task greedy warm-start margin
estimated with the same energy/material prices as the final plan.
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
ODCS contracts are the source of truth for physical field structure, while
domain mapping documents are the source of truth for optimization semantics
(field bindings, canonical units, planning use, quality policies). Avro,
Protobuf, and Elasticsearch schemas are generated from ODCS and carry no
embedded semantic blocks. The mapping engine reads those governed mappings to
translate source fields into stable canonical abstractions (`Asset`,
`Capability`, `Task`, `OperationalBundle`, ...), builds an immutable,
reproducibly-hashed planning snapshot, and optimizes it in both batch
(periodic) and stream (rolling) mode.
See [`docs/current-implementation.md`](current-implementation.md).

To author a brand-new domain pack end to end (feasibility study, domain
description methodology, mappings, profile, registry wiring, costing, and the
validation ladder), follow the manual in
[`docs/authoring-domain-contracts.md`](authoring-domain-contracts.md).

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

# Schema evolution: check every ODCS contract and canonical mapping against its
# committed reviewed history (contracts/evolution/). Added optional ODCS fields,
# unit conversions, and enum/list expansions need minor bumps; breaking schema
# changes and binding retargets need major bumps. Mapping-semantic hash drift
# must still be reviewed in the same gate after the semantic class is reported.
.venv/bin/fl-op contracts evolution-check     # or: make evolution-check
# After a reviewed contract/mapping change with the policy-required contract or
# mapping version bump, record the new history snapshot:
.venv/bin/fl-op contracts evolution-freeze    # or: make evolution-freeze

# Build an immutable, reproducibly-hashed planning snapshot from source data.
.venv/bin/fl-op snapshot build --data latest --mode periodic

# Periodic (batch) plan: canonical assignments + normalized unassigned reasons.
.venv/bin/fl-op plan periodic --data latest

# Optional minimal-time objective; cost remains the default.
.venv/bin/fl-op plan periodic --data latest --objective time

# Rolling (stream) dispatch: one immutable revision per execution event, with a
# freeze window protecting started/imminent tasks and a plan-instability penalty.
.venv/bin/fl-op plan rolling --data latest --events events.jsonl
.venv/bin/fl-op plan rolling --data latest --events events.jsonl --objective time

# Explain why every changed assignment moved between rolling revisions. Plain
# re-solve changes include solver attribution from plan scores when available,
# naming the binding resource (capacity / time / fleet) behind dropped tasks.
.venv/bin/fl-op plan diff-revisions --plan latest

# Full story end to end (contracts -> snapshot -> batch -> stream).
.venv/bin/fl-op demo --data latest      # or: make demo
.venv/bin/fl-op demo --data latest --objective time
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
limit, LNS budget, and rolling change penalty) against recorded KPI baselines:

```bash
.venv/bin/fl-op tune --data latest --trials 20 --seed 7
.venv/bin/fl-op tune --data latest --extra-data .data/generate-data/20260601T120000 --jobs 4
.venv/bin/fl-op tune-promote --best-params .data/tune/20260612T090000/best_params.json --reviewed-by ops
.venv/bin/fl-op tune-promote --best-params .data/tune/20260612T090000/best_params.json --domain drone_logistics --profile drone-logistics --adapter-version 0.1.0 --reviewed-by ops
```

Artifacts land under `.data/tune/<timestamp>/`: `baseline.json`,
`trials.json`, and `best_params.json` (best parameters plus the improvement
over the baseline objective). By default the study records a Pareto frontier:
maximize business objective, minimize instability, and minimize wall time.
`--extra-data` scores multiple datasets with workload weights derived from task
counts, and `--jobs > 1` uses Optuna RDB storage (`study.db` in the run
directory unless `--storage` or `TUNE_STORAGE_URI` is set).

`fl-op tune-promote` writes the reviewed overlay
`.data/tune/solver-parameters-tuned.json` by default. Supplying `--domain`,
`--profile`, and `--adapter-version` writes a scoped overlay under
`.data/tune/<domain>/<profile>/<adapter-version>/solver-parameters-tuned.json`;
`--expires-at` can bound that overlay's validity window. Drone logistics loads
its checked-in tuning defaults plus matching scoped overlays, while legacy
shared overlays continue to serve the older profiles. With
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

Plan and feasibility routes are guarded by the security gateway; `/health`
stays public for load balancers. `SERVE_AUTH_MODE` selects how callers
authenticate (auto when unset: OIDC if an issuer is set, static tokens if any
are set, otherwise open for loopback dev):

- Static tokens: `SERVE_AUTH_TOKENS` is a comma-separated accept-list sent as
  `Authorization: Bearer <token>` (`SERVE_AUTH_TOKEN` is folded in). Listing
  more than one token supports zero-downtime rotation -- add the new token,
  roll clients over, then drop the retired one.
- OIDC/JWT: set `SERVE_OIDC_ISSUER` (and usually `SERVE_OIDC_AUDIENCE`) to
  validate RFC 7519 bearer JWTs -- signature (RS256 via `SERVE_OIDC_JWKS_URL`,
  or HS256 via `SERVE_OIDC_HS256_SECRET`), issuer, audience, and expiry, with
  scopes read from the `scope`/`scp`/`roles` claims. Requires the auth extra:
  `uv sync --extra auth`.

Authorization is per scope: plan routes need `plans:read` and feasibility needs
`feasibility:evaluate` (static tokens are unrestricted unless given an explicit
scope set), so a known caller missing the scope gets 403 and an unauthenticated
one 401. Binding outside loopback (for example `SERVE_HOST=0.0.0.0`) requires an
authenticator. An opt-in in-process rate limiter
(`SERVE_RATE_LIMIT_REQUESTS`/`SERVE_RATE_LIMIT_WINDOW_S`, 0 = off) returns 429
per principal, and every protected request is audited to the
`fl_op.serving.audit` logger -- and to JSONL under `$DATA_DIR/serving/` when
`SERVE_AUDIT_LOG_FILENAME` is set.

By default the API reads artifacts from `$DATA_DIR`; set
`SERVE_ARTIFACT_ROOT=/mnt/fl-op-artifacts` to serve a shared mounted artifact
tree from several instances. Set `SERVE_ARTIFACT_BACKEND=object-store` with
`SERVE_OBJECT_STORE_KIND=local` and `SERVE_OBJECT_STORE_LOCAL_ROOT` to read
through the object-store backend instead: only runs carrying a `_COMMITTED`
marker are served, so a reader never sees a half-published run. The built-in
client is a filesystem-backed reference (no vendor SDK); a networked backend
plugs into the same `ObjectStoreClient` seam.

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
consumes `EVENT_BROKER_TOPIC` from `EVENT_BROKER_BOOTSTRAP_SERVERS` (the broker
extra: `uv sync --extra broker`); `redis` reads `EVENT_REDIS_STREAM` through the
`EVENT_REDIS_GROUP` consumer group (the redis extra: `uv sync --extra redis`).
All sources validate events identically, and a rolling run drains the visible
backlog (`EVENT_BROKER_MAX_EMPTY_POLLS` / `EVENT_REDIS_MAX_EMPTY_POLLS`
consecutive empty polls) before publishing revisions. Kafka and Redis
acknowledge consumption (commit offsets / `XACK`) only after the revisions are
published and their event ids recorded in the durable dedup store, so a
redelivery after a restart is suppressed instead of producing a duplicate
revision -- effectively exactly-once.

Integrations can register more source kinds with
`fl_op.stream.broker.register_event_source(kind, factory,
uses_dedup_store=True)`; the Redis Streams adapter (`fl_op/stream/redis_stream.py`)
is a worked example. Use the dedup flag for sources that may redeliver events
after a process restart; leave it off for intentionally replayed files or test
feeds.

### Running Redis locally

A `docker-compose.yml` ships a Redis service for the Redis Streams source:

```bash
docker compose up -d redis                       # start Redis on localhost:6379
export EVENT_SOURCE_KIND=redis
export EVENT_REDIS_URL=redis://localhost:6379/0  # endpoint (overrides host/port/db)
```

`EVENT_REDIS_URL` is the single-knob endpoint; without it the source falls back
to `EVENT_REDIS_HOST`/`EVENT_REDIS_PORT`/`EVENT_REDIS_DB`. Producers publish each
event as a JSON string in the `EVENT_REDIS_BODY_FIELD` (default `data`) field,
for example `XADD fl-op.execution-events * data '{"event_id": ...}'`.

The test suite runs against an in-memory `fakeredis` server by default (no
broker needed); set `FL_OP_TEST_REDIS_URL=redis://localhost:6379/0` to exercise
the same tests against the real endpoint.

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
  tune/<domain>/<profile>/<adapter-version>/solver-parameters-tuned.json
                                 # scoped reviewed tuned solver overlay
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
