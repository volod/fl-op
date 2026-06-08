"""Reproducible snapshot hashing.

The hash covers the canonical content only: per-run identifiers, generation
timestamp, and the non-canonical solver bridge payload are excluded. Rebuilding
a snapshot from identical source records, effective timestamp, and version
dimensions therefore yields an identical hash.
"""

import hashlib
import json
from typing import Any


def _canonical_json(content: dict[str, Any]) -> str:
    return json.dumps(content, separators=(",", ":"), sort_keys=True, default=str)


def compute_snapshot_hash(canonical_content: dict[str, Any]) -> str:
    """SHA-256 over the canonical-JSON serialization of a snapshot's content."""
    return hashlib.sha256(_canonical_json(canonical_content).encode("utf-8")).hexdigest()
