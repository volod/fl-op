# ADR-018: Immutable, reproducibly-hashed planning snapshots

Date: 2026-06-05
Status: Accepted
Deciders: Volodymyr Lazurenko, Claude Code

## Context

The spec (sections 17.1, 17.3) requires that a `PlanningSnapshot` be immutable
and that rebuilding it from identical source records, effective timestamp, and
version dimensions yields the same normalized hash. This underpins auditability:
every plan traces to a specific snapshot hash.

## Decision

Make `PlanningSnapshot` (and `Plan`/`PlanRevision`) frozen Pydantic models. The
snapshot hash is computed over the canonical content only, explicitly excluding
`snapshot_id`, `generated_at`, and the `solver_payload` bridge.

## Rationale

`generated_at` and `snapshot_id` vary per run and must not affect the content
hash. `solver_payload` is a derived, non-canonical projection (ADR-020) and is
excluded so the hash reflects semantics, not the bridge encoding. Canonical JSON
serialization with sorted keys makes the hash independent of field ordering.

## Consequences

- Building twice with the same inputs and `effective_at` produces an identical
  `snapshot_hash` (verified by `test_snapshot_repro.py`).
- Mutating a snapshot raises a Pydantic `ValidationError`.
- Plans record the `snapshot_hash` they were produced from, closing the lineage
  loop from plan back to source.
