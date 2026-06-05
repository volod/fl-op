# ADR-016: Solver-neutral canonical model with a snapshot seam

Date: 2026-06-05
Status: Accepted
Deciders: Volodymyr Lazurenko, Claude Code

## Context

The original POC fed raw source CSVs (vehicle, implement, order) directly into
the solver. Source vocabulary therefore leaked into the optimization model, and
there was no place to attach data-quality, lineage, or versioning. The spec
(docs/specs/shema.md sections 4.1, 11, 17) requires that source-system vocabulary
not define the optimization model, and that solvers consume only immutable
planning snapshots, never raw source data (section 4.3).

## Decision

Introduce a solver-neutral canonical model (`src/fl_op/canonical/`) with the
abstractions actually used by the demo slice: `Asset`, `Capability`,
`Location`, `Task`, `OperationalBundle`, `Forecast`, `Commitment`,
`InventoryPosition`, `PlanningSnapshot`, `Plan`, `Assignment`, `UnassignedTask`,
`MaterialReservation`. Insert an immutable `PlanningSnapshot` between the data
layer and the solver so both batch and stream adapters consume snapshots.

## Rationale

A single `Asset` abstraction with `roles` unifies tractors, implements, and
operators; a single `Location` unifies fields and depots. This is exactly the
"name is less important than data semantics" principle from the proposal: the
solver reasons about capabilities and roles, not about the word "tractor".

The snapshot is the seam where quality filtering, unit normalization, version
stamping, and reproducible hashing all attach.

## Consequences

- Models are Pydantic v2 and frozen (`ConfigDict(frozen=True)`); see ADR-018.
- The existing dict-based solver chain is not rewritten; it is fed from the
  snapshot via a reverse-projection bridge (see ADR-020).
- Field names stay faithful to the spec so JSON/YAML output matches the contract.
