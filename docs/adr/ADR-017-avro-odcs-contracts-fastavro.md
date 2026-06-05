# ADR-017: Real Avro + ODCS contracts via fastavro, with dual fingerprints

Date: 2026-06-05
Status: Accepted
Deciders: Volodymyr Lazurenko, Claude Code

## Context

The proposal's core thesis is that the data contract / governance layer is the
hard, valuable part. The spec (sections 8-10, 9.4-9.5) requires real Avro
schemas carrying `x-optimization` metadata, ODCS contracts mirroring the
bindings, a metadata-preservation round-trip test, and two independent
fingerprints: `avroParsingFingerprint` (serialization structure) and
`optimizationMetadataHash` (normalized optimization metadata).

Two Avro libraries were considered: the reference `avro` package and `fastavro`.
The reference library strips unknown top-level properties (like `x-optimization`)
when re-serializing a parsed schema, which would break metadata round-trip.

## Decision

Author real `.avsc` Avro schemas and ODCS YAML contracts under `contracts/`, one
per source dataset, plus the `OptimizationProfile`. Use **fastavro** (pure
Python, no JVM). Compute `avroParsingFingerprint` over fastavro's parsing
canonical form, and compute `optimizationMetadataHash` directly from the raw
`.avsc` JSON. Use a local file-based registry (`contracts/registry.yaml`); no
external schema-registry service.

## Rationale

fastavro is pip-installable and JVM-free, consistent with the project's
"run anywhere Python runs" stance (ADR-001). It was verified that fastavro
preserves `x-optimization` blocks through parse + JSON round-trip, and that its
parsing canonical form excludes `x-optimization` — so a semantic edit moves only
the metadata hash, never the structural fingerprint. Deriving the metadata hash
from raw JSON (rather than the parsed object) decouples governance from any
library preservation quirk.

## Consequences

- `fastavro` and `pyyaml` are added as runtime dependencies.
- The registry stores both fingerprints and raises `MetadataLossError` if a
  recomputed `optimizationMetadataHash` diverges from the stored baseline.
- ODCS bindings are cross-checked field-for-field against the Avro bindings
  during `fl-op contracts validate`.
