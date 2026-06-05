# fl-op

Fleet optimization CLI — a decision support system for assigning
means-implement pairs to orders at production scale, with a declarative
data-contract layer that runs the same canonical state in both batch and stream
mode.

**Problem class**: Heterogeneous Fleet VRP with Time Windows (HFVRPTW) +
Multi-resource Scheduling + Profit-Maximizing Order Selection.

**Default scale**: 100 vehicles, 400 implements, 250 concurrent orders,
50 depots (overridable via CLI flags, environment variables, or Makefile).
Production-scale runs (3000+ vehicles) require explicit overrides.

**Stack**: Python 3.10+, OR-Tools routing library, NumPy, scikit-learn,
Pydantic v2, fastavro, uv.

---

## Quick Start

```bash
# 1. Create virtualenv and install dependencies
make venv

# 2. Run the batch solver pipeline at smoke-test scale (~5 seconds)
#    generate-data -> solve -> analyse -> console statistics
make quickstart

# 3. Run the full declarative demo: contracts -> snapshot -> batch + stream
make demo
```

`make demo` generates a dataset and then runs the end-to-end story: validate the
Avro + ODCS data contracts, build an immutable planning snapshot, produce a
periodic (batch) plan, synthesize an execution-event stream, and produce rolling
(stream) dispatch revisions. Artifacts land under `.data/`.

---

## Commands at a glance

| Command | Purpose |
|---------|---------|
| `fl-op generate-data` | Generate a synthetic (or real-augmented) fleet dataset. |
| `fl-op solve` | Run the full fleet scheduling solver (batch). |
| `fl-op analyse` | Pretty-print statistics for a completed solver run. |
| `fl-op reschedule` | Re-run the solver after in-progress field events. |
| `fl-op query-contract` | Fast feasibility + margin estimate for a new order (no solver). |
| `fl-op contracts validate` | Validate Avro + ODCS contracts: round-trip, dual fingerprints, binding agreement. |
| `fl-op snapshot build` | Map source data into canonical objects and build a reproducible snapshot. |
| `fl-op plan periodic` | Periodic (batch) OR-Tools plan from an immutable snapshot. |
| `fl-op plan rolling` | Rolling (stream) dispatch producing immutable plan revisions. |
| `fl-op demo` | Full contract -> snapshot -> batch + stream demonstration. |

All commands accept `--data latest` / `--schedule latest`. See
[`docs/usage.md`](docs/usage.md) for the full command-by-command walkthrough with
example inputs and outputs.

---

## Documentation

- **Usage guide**: [`docs/usage.md`](docs/usage.md)
  — command-by-command walkthrough, sample inputs/outputs, benchmarks, and the
  `.data/` output layout.
- **Data-contract platform**: [`docs/design/data-contract-platform.md`](docs/design/data-contract-platform.md)
  — declarative Avro/ODCS contracts, source-to-canonical mapping, immutable
  snapshots, and the batch + stream adapters.
- **System design**: [`docs/design/main-design.md`](docs/design/main-design.md)
  — full architecture, layer contracts, test requirements, failure modes.
- **Algorithms**: [`docs/algorithms/`](docs/algorithms/)
  — problem formulation, solver pipeline, and a learning path for the math.
- **Architecture decisions**: [`docs/adr/README.md`](docs/adr/README.md)
  — 20 ADRs explaining every significant technical choice.

---

## Development

```bash
make venv          # create .venv and install all dependencies
make quickstart    # generate-data + solve + analyse at smoke-test scale
make demo          # contracts -> snapshot -> periodic (batch) + rolling (stream)
make contracts     # validate the Avro + ODCS contract suite
make data          # default benchmark (manual, ~10 min)
uv run pytest      # run test suite (139 tests, < 120 s)
```

Tests require no external services. The smoke test (`tests/test_smoke.py`)
runs the full generate-data -> solve -> analyse -> reschedule -> query-contract
pipeline at minimum scale. The session fixture in `tests/conftest.py` generates a
50v/200i/20o/5d dataset shared across all unit tests.
