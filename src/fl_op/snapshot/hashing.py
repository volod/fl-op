"""Reproducible snapshot hashing.

The hash covers the canonical content only: per-run identifiers, generation
timestamp, and the non-canonical solver bridge payload are excluded. Rebuilding
a snapshot from identical source records, effective timestamp, and version
dimensions therefore yields an identical hash.

The digest is a namespaced content hash pinned to ``SNAPSHOT_HASH_VERSION``.
Pinning to a dedicated version (rather than the global ``NAMESPACE_VERSION``)
keeps snapshot identity stable when the global cache version is bumped: a
solver-cache invalidation must never re-identify snapshots or orphan the tuned
overlays and manifests that cite them. The snapshot version is bumped only when
the canonical content layout itself changes.
"""

from typing import Any

from fl_op.core.constants import SNAPSHOT_HASH_VERSION
from fl_op.provenance.namespace import content_hash

_SNAPSHOT_NAMESPACE = "snapshot"


def compute_snapshot_hash(canonical_content: dict[str, Any]) -> str:
    """Namespaced SHA-256 over a snapshot's canonical-JSON content."""
    return content_hash(
        _SNAPSHOT_NAMESPACE,
        canonical_content,
        version=SNAPSHOT_HASH_VERSION,
    )
