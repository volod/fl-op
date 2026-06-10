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
#    generate-data (avro) -> solve -> analyse -> console statistics
make quickstart

# 3. Run the full declarative demo: contracts -> snapshot -> batch + stream
make demo

# Use a different dataset format (avro default, also csv or parquet):
make quickstart FORMAT=parquet
```

`make demo` generates a dataset and then runs the end-to-end story: validate the
ODCS data contracts, build an immutable planning snapshot, produce a periodic
(batch) plan, synthesize an execution-event stream, and produce rolling (stream)
dispatch revisions. Artifacts land under `$DATA_DIR` (default: `.data/`).

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
| `fl-op contracts validate` | Validate ODCS contracts: dual fingerprints and generation-ready check. |
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
  `$DATA_DIR/` output layout.
- **Current implementation**: [`docs/current-implementation.md`](docs/current-implementation.md)
  — contracts, snapshots, solver chain, rolling dispatch, and run-log analysis.
- **Optimization ontology**: [`docs/reference/optimization-ontology.md`](docs/reference/optimization-ontology.md)
  — the domain-neutral entity ontology, semantic-term vocabulary, covered
  optimization use cases and domains, algorithms, and further reading.
- **Model-world divergence**: [`docs/reference/model-world-divergence.md`](docs/reference/model-world-divergence.md)
  — the effects a distributed, non-deterministic operational system produces
  when the implemented entity model is not the world, and which mechanism
  covers each.
- **Canonical optimization model**: [`docs/reference/canonical-model.md`](docs/reference/canonical-model.md)
  — the three-layer architecture and the domain-neutral entity / capability /
  semantic-term contract the engine consumes.
- **Domain mapping packs**: [`docs/reference/domain-mapping.md`](docs/reference/domain-mapping.md)
  — how a physical domain (agricultural, construction) projects onto the canonical
  model, extra (analytical) fields, and how to add a new domain.
- **Algorithms**: [`docs/algorithms/`](docs/algorithms/)
  — problem formulation, solver pipeline, and a learning path for the math.
- **Future improvements**: [`docs/future-improvements.md`](docs/future-improvements.md)
  — targeted improvements for solver quality, snapshot scale, contracts, and rolling dispatch.

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
make validate-construction # prove the construction pack maps onto the canonical model
make data          # default benchmark (manual, ~10 min)
uv run pytest      # run test suite (< 120 s)
```

Tests require no external services. The smoke test (`tests/test_smoke.py`)
runs the full generate-data -> solve -> analyse -> reschedule -> query-contract
pipeline at minimum scale. The session fixture in `tests/conftest.py` generates a
50v/200i/20o/5d dataset shared across all unit tests.
