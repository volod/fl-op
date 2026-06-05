# ADR-020: Snapshot solver-payload bridge driven by contract bindings

Date: 2026-06-05
Status: Accepted
Deciders: Volodymyr Lazurenko, Claude Code

## Context

The working solver chain (preprocessing, greedy, resource allocator, cluster
pool) consumes plain dict rows keyed by source CSV column names. The canonical
model must become the source of truth without rewriting that chain, and without
the solver reading raw source data (spec 4.3). The risk: the chain reads exact
column names and tolerates native types but also stringified lists.

## Decision

Carry a non-canonical `solver_payload` on the snapshot, produced by a
reverse-projection bridge (`snapshot/payload.py`). The bridge reconstructs each
solver row from the canonical objects using the SAME `x-optimization` bindings
that produced them: for each binding, the canonical value is read and written
under `binding.source_field`. The payload is excluded from the snapshot hash
(ADR-018).

## Rationale

Driving both forward mapping and reverse projection from one binding table
guarantees every bound column is reproduced with its exact name, and keeps the
encoding declarative. Only entities that survived quality policy exist as
canonical objects, so the solver sees the validated, normalized projection of
the snapshot, never raw source rows. The chain's `float(...)` casts and
list/string tolerance let the bridge emit native types safely.

## Consequences

- The solver internals are unmodified; the shared `solver/chain.py` helper is the
  single call site for both legacy pipelines and the adapters.
- A golden-row test asserts the projected rows equal the CSV-loaded rows on every
  bound field (`test_snapshot_repro.py`).
- Materialized snapshot bundles are a bounded sample (`BUNDLE_GENERATION_CAP`);
  assignment bundle ids remain deterministically reproducible from their assets.
