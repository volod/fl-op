"""Dual fingerprints for Avro schemas (spec 9.5).

Two independent hashes are maintained so that a change to optimization semantics
is detectable even when the Avro serialization structure is unchanged, and vice
versa:

  avroParsingFingerprint  - identifies serialization-relevant structure. Computed
                            over the Avro Parsing Canonical Form, which by
                            definition excludes docs, defaults, aliases, and any
                            unknown properties such as `x-optimization`.

  optimizationMetadataHash - identifies the normalized `x-optimization` metadata.
                            Computed directly from the raw schema JSON so that it
                            is independent of any Avro-library preservation
                            behavior.

Verified against fastavro: `to_parsing_canonical_form` strips `x-optimization`,
so mutating a binding's `canonicalUnit` changes only the metadata hash.
"""

import hashlib
import json
from typing import Any

import fastavro

from fl_op.core.constants import XOPT_NAMESPACE


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def avro_parsing_fingerprint(schema_json: dict[str, Any]) -> str:
    """SHA-256 over the Avro Parsing Canonical Form of the schema."""
    canonical = fastavro.schema.to_parsing_canonical_form(schema_json)
    return _sha256_hex(canonical)


def collect_xopt_blocks(schema_json: dict[str, Any]) -> dict[str, Any]:
    """Collect every x-optimization block from a record schema, keyed by location.

    Keys are "record" for the record-level block and "field:<name>" for each
    field-level block. Returns only blocks that are present.
    """
    blocks: dict[str, Any] = {}
    record_block = schema_json.get(XOPT_NAMESPACE)
    if isinstance(record_block, dict):
        blocks["record"] = record_block
    for field in schema_json.get("fields", []):
        if not isinstance(field, dict):
            continue
        field_block = field.get(XOPT_NAMESPACE)
        if isinstance(field_block, dict):
            blocks[f"field:{field['name']}"] = field_block
    return blocks


def _normalize(value: Any) -> Any:
    """Recursively normalize a JSON value so key order does not affect the hash."""
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def optimization_metadata_hash(schema_json: dict[str, Any]) -> str:
    """SHA-256 over the normalized x-optimization metadata of the schema."""
    blocks = collect_xopt_blocks(schema_json)
    normalized = _normalize(blocks)
    canonical = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return _sha256_hex(canonical)


def both_fingerprints(schema_json: dict[str, Any]) -> dict[str, str]:
    """Return both fingerprints as a dict ready to store in the registry."""
    return {
        "avroParsingFingerprint": avro_parsing_fingerprint(schema_json),
        "optimizationMetadataHash": optimization_metadata_hash(schema_json),
    }
