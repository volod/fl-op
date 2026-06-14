"""Shared, versioned content-hashing primitive.

Every content hash in the engine (cache keys, snapshot hashes, manifest entries)
flows through this module so the serialization rules stay byte-identical across
call sites. A namespace label keeps otherwise-identical payloads from different
subsystems in distinct key spaces, and a version folded into the digest lets us
invalidate a whole key space by bumping one constant.

``content_hash(namespace, payload)`` is the single entry point. By default it
folds in the global ``NAMESPACE_VERSION``, so bumping that constant invalidates
every derived cache wholesale. Call sites that need a hash whose stability is
independent of the global cache version (notably the snapshot hash, which is a
durable identity cited as provenance) pass an explicit ``version``.
"""

import hashlib
import json
from typing import Any, Optional

from fl_op.core.constants import PROVENANCE_NAMESPACE_VERSION

NAMESPACE_VERSION: str = PROVENANCE_NAMESPACE_VERSION


def canonical_json(payload: Any) -> str:
    """Deterministic JSON used for every provenance digest.

    Keys are sorted, separators are compact, and non-JSON values fall back to
    ``str`` so the encoding is stable regardless of insertion order or dict
    identity.
    """
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)


def content_hash(namespace: str, payload: Any, *, version: Optional[str] = None) -> str:
    """Namespaced, version-prefixed content hash.

    The digest is taken over a wrapper object so two payloads that are equal but
    belong to different subsystems never collide, and so a version bump
    invalidates every derived hash in that key space.

    ``version`` defaults to the global ``NAMESPACE_VERSION`` (cache-key
    behaviour: one bump invalidates everything). Pass an explicit version for
    hashes whose stability must be decoupled from global cache invalidation,
    such as the snapshot identity hash.
    """
    framed = {
        "namespace": namespace,
        "namespace_version": version if version is not None else NAMESPACE_VERSION,
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(framed).encode("utf-8")).hexdigest()
