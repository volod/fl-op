# fl-op

Fleet optimization CLI — a decision support system for assigning
means-implement pairs to orders at production scale.

**Problem class**: Heterogeneous Fleet VRP with Time Windows (HFVRPTW) +
Multi-resource Scheduling + Profit-Maximizing Order Selection.

**Default benchmark scale**: 100 vehicles, 400 implements, 250 concurrent
orders, 50 depots. Larger runs can be requested from the CLI or Makefile.

**Stack**: Python 3.10+, OR-Tools routing library, NumPy, scikit-learn, Pydantic v2, uv.

---

## Quick Start

```bash
# 1. Create virtualenv and install dependencies
make venv

# 2. Run the full pipeline at smoke-test scale (takes ~5 seconds)
make quickstart
```

`make quickstart` runs four steps under the hood:

```
generate-data  ->  solve  ->  analyse  ->  console statistics
```

To run each step manually:

```bash
# Generate synthetic dataset: 50 vehicles, 200 implements, 20 orders, 5 depots
.venv/bin/fl-op generate-data --vehicles 50 --implements 200 --orders 20 --depots 5 --seed 42

# Solve: picks up the most recent generated dataset automatically
.venv/bin/fl-op solve --data latest

# Pretty-print the latest schedule and resource statistics
.venv/bin/fl-op analyse --schedule latest

# Reschedule after marking an order as started
.venv/bin/fl-op reschedule --data latest --schedule latest \
    --events events.json

# Query feasibility for a new contract (no solver call, fast)
.venv/bin/fl-op query-contract --data latest --schedule latest \
    --order new_order.json
```

---

## Typical Workflow

### Step 1 — Generate data

```bash
.venv/bin/fl-op generate-data \
    --vehicles 1500 \
    --implements 6000 \
    --orders 2500 \
    --depots 500 \
    --seed 42
```

Those are the CLI defaults, so the same run can be started with:

```bash
.venv/bin/fl-op generate-data --seed 42
```

Output written to `.data/generate-data/current-timestamp/`:

```
depots.csv         implements.csv    operators.csv
fields.csv         orders.csv        vehicles.csv
contracts.json     weather.json      metadata.json
```

To load real fleet data instead of synthetic:

```bash
.venv/bin/fl-op generate-data \
    --vehicles 1500 --implements 6000 --orders 2500 --depots 500 \
    --data-path /path/to/real/csvs/
```

Real CSVs take priority; missing fields fill from synthetic distributions.

---

### Step 2 — Solve

```bash
.venv/bin/fl-op solve --data latest
```

Or point to a specific dataset directory:

```bash
.venv/bin/fl-op solve --data .data/generate-data/current-timestamp/
```

**Example output** (50 vehicles / 200 implements / 20 orders):

```
Fleet Optimization Schedule Report
========================================
Dispatched:   17
Infeasible:   3
Total margin: 347629.82 EUR
Greedy base:  386670.36 EUR
Improvement:  -39040.54 EUR
Total fuel:   6905.8 L

Infeasibility reasons:
  no_allocated_vehicles: 3
```

Output written to `.data/solve/current-timestamp/`:

```
schedule.json          # full dispatch packages (one per assigned order)
schedule_report.txt    # human-readable summary
schedule_kpis.json     # machine-readable KPIs for dashboards
infeasible_orders.json # orders that could not be assigned with reason codes
```

**Sample dispatch package** from `schedule.json`:

```json
{
  "dispatch_id": "a9720e7b-0a72-4e52-80b6-bbb6c86f2fea",
  "vehicle_id": "vehicle_00001",
  "implement_id": "implement_000001",
  "operator_id": "operator_00009",
  "order_id": "order_000001",
  "depot_id": "depot_0004",
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
  "total_fuel_l": 6905.79
}
```

`greedy_baseline_margin_eur` is what a naive nearest-vehicle greedy assignment
would earn. `solver_improvement_eur` is how much OR-Tools improved on it.

To inspect the latest solver run in the terminal:

```bash
.venv/bin/fl-op analyse --schedule latest
```

This prints served/rejected percentages, vehicle and implement usage,
economic KPIs, top-used resources, and ASCII bar charts by cluster/day.

---

### Step 3 — Reschedule after field events

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
and includes a `plan_diff.json` (structured) and `plan_diff.txt` (human summary
of what changed vs. the previous schedule).

---

### Step 4 — Query a new contract

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

Returns top-3 vehicle-implement options with conflict risk:

```json
{
  "feasible": true,
  "candidates": [
    {
      "vehicle_id": "vehicle_00042",
      "implement_id": "implement_005301",
      "estimated_margin_eur": 14200.00,
      "schedule_conflict_risk": "low"
    },
    {
      "vehicle_id": "vehicle_00017",
      "implement_id": "implement_002847",
      "estimated_margin_eur": 13850.00,
      "schedule_conflict_risk": "medium"
    },
    {
      "vehicle_id": "vehicle_00089",
      "implement_id": "implement_009112",
      "estimated_margin_eur": 13200.00,
      "schedule_conflict_risk": "low"
    }
  ]
}
```

Response in under 5 seconds at production scale. No solver call involved.

---

## Benchmarks

| Scale | generate-data | solve (8 cores) |
|-------|--------------|-----------------|
| Smoke test (50v / 200i / 20o / 5d) | < 1 s | < 5 s |
| Default benchmark (1500v / 6000i / 2500o / 500d) | < 60 s | 5-10 min |

Run the default benchmark manually (not a CI target):

```bash
make data
```

Override the Makefile defaults from the command line:

```bash
make data VEHICLES=3000 IMPLEMENTS=20000 ORDERS=2500 DEPOTS=50
```

---

## Output Directory Layout

```
.data/
  generate-data/<timestamp>/     # dataset for one generate-data run
  solve/<timestamp>/             # schedule + KPIs for one solve run
  reschedule/<timestamp>/        # updated schedule + plan_diff
  query-contract/<timestamp>/    # feasibility result for one new order
```

All JSON files include `schema_version: "1.0"` and `run_metadata` for
traceability. Old runs are never overwritten; each run gets its own timestamp
directory.

---

## Project Structure

```
src/fl_op/
  main.py                  # CLI entry point (click group, 5 commands)
  core/
    constants.py           # all numeric constants (no magic numbers)
    paths.py               # --latest resolver, path traversal guard
  models/
    enums.py               # OperationType, ImplementType, VehicleType, OrderStatus
    types.py               # TypedDict pipeline contracts
    vehicle.py / implement.py / operator.py / depot.py
    field.py / order.py / contract.py / weather.py
    compat_matrix.py       # numpy bool + float32 ndarray, memmap I/O
  data/
    generator.py           # vectorized synthetic data + real CSV merge
  solver/
    preprocessing.py       # compat filter + haversine BallTree clustering
    resource_allocator.py  # global pre-allocation (penalty-weighted priority)
    greedy.py              # vectorized warm-start scorer
    cluster_solver.py      # OR-Tools routing library worker
    aggregator.py          # multiprocessing Pool + KPI aggregation
    analysis/              # solve artifact loading, metrics, console report
    reschedule.py          # rolling-horizon re-optimization
    query.py               # fast contract feasibility query
docs/
  design/                  # approved system design + engineering review test plan
  adr/                     # 15 Architecture Decision Records
```

---

## Further Reading

- **Algorithm deep-dive**: [`docs/algorithms/01-problem-formulation.md`](docs/algorithms/01-problem-formulation.md)
  — mathematical problem statement, HFVRPTW formulation, objective function.
- **Solver pipeline**: [`docs/algorithms/02-solver-pipeline.md`](docs/algorithms/02-solver-pipeline.md)
  — hierarchical decomposition, compatibility matrix, BallTree clustering,
  pre-allocation, OR-Tools routing model, greedy warm-start.
- **Learning path**: [`docs/algorithms/03-learning-path.md`](docs/algorithms/03-learning-path.md)
  — structured reading list and concept map for readers with a math background
  who want to understand or extend the solver.
- **System design**: [`docs/design/main-design.md`](docs/design/main-design.md)
  — full architecture, layer contracts, test requirements, failure modes.
- **Architecture decisions**: [`docs/adr/README.md`](docs/adr/README.md)
  — 15 ADRs explaining every significant technical choice.

---

## Development

```bash
make venv          # create .venv and install all dependencies
make quickstart    # generate-data + solve + analyse at smoke-test scale
make data          # default benchmark (manual, ~10 min)
uv run pytest      # run test suite (24 tests, < 5 s)
```

Tests require no external services. The smoke test (`tests/test_smoke.py`)
runs the full generate-data -> solve -> reschedule -> query-contract pipeline
on a 10-vehicle dataset.
