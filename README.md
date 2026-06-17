# fl-op

Fleet optimization CLI -- a decision support system for assigning
multi-resource bundles to orders at production scale, with a declarative
data-contract layer that runs the same canonical state in both batch and stream
mode.

**Problem class**: Heterogeneous Fleet VRP with Time Windows (HFVRPTW) +
Multi-resource Scheduling + Profit-Maximizing Order Selection.

**Default domain and scale**: drone logistics (`drone_logistics`) with
100 vehicles, 400 payload modules, 250 concurrent deliveries, and 50 hubs
(overridable via CLI flags, environment variables, or Makefile). Other
registered domains remain selectable explicitly.
Production-scale runs (3000+ vehicles) require explicit overrides.

**Stack**: Python 3.10+, OR-Tools routing library, NumPy, scikit-learn,
Pydantic v2, fastavro, uv.

---

## Quick Start

```bash
# 1. Create virtualenv and install dependencies
make venv

# 2. Run the batch solver pipeline at smoke-test scale (~5 seconds)
#    generate-data (avro) -> solve -> analyse -> console statistics
make quickstart

# 3. Run the full declarative demo: contracts -> snapshot -> batch + stream
make demo

# Use a different dataset format (avro default, also csv or parquet):
make quickstart FORMAT=parquet
```

`make demo` generates a drone logistics dataset and then runs the end-to-end
story: validate the ODCS data contracts, build an immutable planning snapshot,
produce a periodic (batch) mixed UGV/UAV plan with battery kWh/electricity cost
accounting, synthesize an execution-event stream, and produce rolling (stream)
dispatch revisions. Artifacts land under
`$DATA_DIR` (default: `.data/`).

Cost optimization is the default. For deadline-sensitive comparisons, run
`fl-op plan periodic --objective time`, `fl-op plan rolling --objective time`,
or `fl-op demo --objective time` to minimize travel/service/completion time
while keeping the same hard deadlines and safety restrictions.

---

## Commands at a glance

| Command | Purpose |
|---------|---------|
| `fl-op generate-data` | Generate a synthetic (or real-augmented) fleet dataset. |
| `fl-op solve` | Run the full fleet scheduling solver (batch). |
| `fl-op analyse` | Pretty-print statistics for a completed solver run. |
| `fl-op reschedule` | Re-run the solver after in-progress field events. |
| `fl-op query-contract` | Fast feasibility + margin estimate for a new order (no solver). |
| `fl-op contracts check-generation` | Validate ODCS contracts have complete generation hints (`--format avro\|proto\|es\|parquet`). |
| `fl-op contracts generate` | Generate physical schemas from ODCS contracts (`--format avro\|proto\|es\|parquet`). |
| `fl-op contracts validate` | Validate contracts: generated schemas, canonical mappings, fingerprints, profiles. |
| `fl-op contracts evolution-check` | Check ODCS contracts against reviewed migration history and metadata-hash gates. |
| `fl-op contracts evolution-freeze` | Record reviewed schema + metadata snapshots for all ODCS contracts. |
| `fl-op snapshot build` | Map source data into canonical objects and build a reproducible snapshot. |
| `fl-op plan periodic` | Periodic (batch) OR-Tools plan from an immutable snapshot. |
| `fl-op plan rolling` | Rolling (stream) dispatch producing immutable plan revisions. |
| `fl-op tune` | Optuna study over solver parameters against a recorded KPI baseline. |
| `fl-op tune-promote` | Promote reviewed tuned parameters to a shared or scoped overlay. |
| `fl-op serve` | HTTP API: feasibility checks and published plan retrieval. |
| `fl-op demo` | Full contract -> snapshot -> batch + stream demonstration. |

All commands accept `--data latest` / `--schedule latest`. See
[`docs/usage.md`](docs/usage.md) for the full command-by-command walkthrough with
example inputs and outputs.

---

## Documentation

Start with the entry point that fits your goal; each links onward to the detail.

| If you want to ... | Start here |
|---|---|
| Run the CLI command by command (inputs, outputs, benchmarks, output layout) | [`docs/usage.md`](docs/usage.md) |
| Understand how the system works today | [`docs/current-implementation.md`](docs/current-implementation.md) -- the implementation-guide entry point, linking the focused section pages under [`docs/implementation/`](docs/implementation/) |
| Author a new domain pack end to end | [`docs/authoring-domain-contracts.md`](docs/authoring-domain-contracts.md) -- feasibility study, methodology, costing, glossary, validation ladder |
| Learn the canonical model and ontology | [`docs/reference/optimization-ontology.md`](docs/reference/optimization-ontology.md), [`docs/reference/canonical-model.md`](docs/reference/canonical-model.md), [`docs/reference/domain-mapping.md`](docs/reference/domain-mapping.md) |
| Understand the math and the solver | [`docs/algorithms/`](docs/algorithms/) -- problem formulation, solver pipeline, learning path |
| See how the model survives the real world | [`docs/reference/model-world-divergence.md`](docs/reference/model-world-divergence.md) |
| Track open work | [`docs/future-improvements.md`](docs/future-improvements.md) -- open backlog only; delivered detail lives in the implementation guide |

---

## Development

```bash
make venv          # create .venv and install all dependencies
make quickstart    # generate-data + solve + analyse at smoke-test scale
make demo          # contracts -> snapshot -> periodic (batch) + rolling (stream)
make check-gen     # validate ODCS generation hints for all formats
make contracts-gen # generate Avro, Protobuf, ES, and Parquet schemas from ODCS
make contracts     # validate the full suite (canonical model + per-domain mappings)
make canonical-validate    # validate only the canonical optimization model
make validate-drone-logistics # validate the default drone logistics pack
make validate-construction # prove the construction pack maps onto the canonical model
make validate-roadside     # validate the roadside-infrastructure runnable pack
make data          # default benchmark (manual, ~10 min)
uv run pytest      # run test suite (< 120 s)
```

Tests require no external services. The smoke test (`tests/test_smoke.py`)
runs the full generate-data -> solve -> analyse -> reschedule -> query-contract
pipeline at minimum scale. The session fixture in `tests/conftest.py` generates a
50v/200i/20o/5d dataset shared across all unit tests.

Domain selection is registry-driven. The default active pack is
`drone_logistics`; use `ACTIVE_DOMAIN=<domain>` for another single pack or
`ACTIVE_DOMAINS=agricultural,construction` for a staged shared-fleet snapshot.
The solver still consumes the same canonical row vocabulary.
