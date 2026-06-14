"""Shared artifact and provenance foundation.

This package centralizes the engine's content-hashing rules and the provenance
artifacts built on top of them:

* ``namespace`` -- the versioned content-hash primitive every cache key,
  snapshot hash, and manifest digest flows through;
* ``manifest`` -- per-run ``manifest.json`` sidecars recording artifact kind,
  source snapshot hashes, scope, and file digests;
* ``registry`` -- a read-only scanner that aggregates cache provenance, run
  manifests, and tuned-overlay selection metadata under the data root.
"""

from fl_op.provenance.namespace import (
    NAMESPACE_VERSION,
    canonical_json,
    content_hash,
)

__all__ = [
    "NAMESPACE_VERSION",
    "canonical_json",
    "content_hash",
]
