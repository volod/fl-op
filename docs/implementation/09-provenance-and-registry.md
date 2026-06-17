[Implementation guide](../current-implementation.md) > Artifact provenance and registry

# Artifact provenance and registry

- `fl_op/provenance/namespace.py` is the single content-hashing primitive for the
  whole codebase. `canonical_json` serializes any payload deterministically
  (sorted keys, compact separators, `str` fallback); `content_hash(namespace,
  payload)` wraps the payload in `{namespace, namespace_version, payload}` before
  hashing so two subsystems never collide. By default the version folded in is the
  global `PROVENANCE_NAMESPACE_VERSION`, so a single bump invalidates every derived
  cache at once. A call site that needs a hash whose stability is decoupled from
  global cache invalidation passes an explicit `version`.
- `snapshot/hashing.py:compute_snapshot_hash` routes through `content_hash` under
  the `"snapshot"` namespace, but pinned to its own `SNAPSHOT_HASH_VERSION` rather
  than the global namespace version. A snapshot hash is a durable identity (tuned
  overlays and manifests cite it as provenance), so a cache-invalidating bump of
  `PROVENANCE_NAMESPACE_VERSION` must never re-identify snapshots or orphan the
  overlays that reference them. `SNAPSHOT_HASH_VERSION` is bumped only when the
  snapshot's canonical content layout itself changes.
- The content-addressed caches were unified onto `content_hash`: compatibility
  matrix keys (`solver/feasibility.py:compat_cache_key`), preprocessing /
  candidate-filter keys (`solver/preprocessing.py:_hash_payload`), and
  `/feasibility` request keys (`solver/query_pipeline.py:
  feasibility_request_cache_key`) all share the versioned primitive. Leaf
  binary digests over raw numpy bytes (`preprocessing._array_digest`) and file
  bytes (`query_pipeline._file_digest`) stay on a bare SHA-256 and are folded
  into the namespaced payload, since binary content has no canonical-JSON form.
- Artifact manifests (`provenance/manifest.py`) are additive provenance
  sidecars. `write_manifest` drops a `manifest.json` next to a run's primary
  artifacts recording the artifact kind, schema versions, generation time,
  derived snapshot hashes, optional tuned-overlay scope, and a SHA-256 of every
  file in the run directory (recursive, manifest excluded). `manifestHash` is a
  `content_hash` over all fields except the volatile `generatedAt`, so two runs
  that produced byte-identical artifacts from the same inputs share a manifest
  hash. Snapshot builds (`planning/snapshots.py`) now emit a manifest beside
  `snapshot.json` with the snapshot hash and planning mode as scope.
  `verify_manifest` re-hashes the on-disk files and reports missing, mismatched,
  or untracked files.
- The artifact registry (`provenance/registry.py`) is a read-only scanner over
  `$DATA_DIR`. It aggregates three views: per-namespace cache provenance (entry
  counts, total bytes, last-modified) for the caches listed in
  `CACHE_PROVENANCE_DIRNAMES`; every manifest sidecar with its declared snapshot
  hashes and scope; and reviewed tuned solver overlays with their selection
  metadata (scope, `source_snapshot_hashes`, reviewer, review time). It never
  mutates artifacts.
- `fl-op artifacts` (`cli/artifacts_commands.py`) exposes the foundation:
  `artifacts registry` logs the aggregated provenance summary and, with
  `--write`, persists the index to
  `$DATA_DIR/registry/artifact-registry.json`; `artifacts verify --run-dir
  <dir>` re-checks a run's files against its manifest and exits non-zero on any
  mismatch.
</content>
