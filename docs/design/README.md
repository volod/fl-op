# Design Documents

| Document | Description |
|----------|-------------|
| [main-design.md](main-design.md) | Full system design: problem statement, approaches, architecture layers, test requirements, failure modes. Approved 2026-05-21 after office-hours + plan-eng-review + autoplan review pipeline. |
| [eng-review-test-plan.md](eng-review-test-plan.md) | Engineering review test plan: 36 test paths across all pipeline layers, edge cases, and E2E smoke test commands. |
| [data-contract-platform.md](data-contract-platform.md) | Declarative data-contract + solver-neutral planning layer: Avro/ODCS contracts, source-to-canonical mapping, immutable snapshots, and batch + stream adapters. Implements the vertical slice of docs/specs/shema.md. |
