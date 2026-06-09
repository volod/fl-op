"""Dual fingerprints for the contract layer.

Two independent hashes are maintained so that a change to optimization semantics
is detectable even when the Avro serialization structure is unchanged, and vice
versa:

  avroParsingFingerprint   - identifies serialization-relevant structure. Computed
                             over the Avro Parsing Canonical Form, which by
                             definition excludes docs, defaults, aliases, and any
                             unknown properties. Source: generated Avro schema.

  optimizationMetadataHash - identifies the normalized semantic metadata of a
                             domain's canonical mapping document. Source: the
                             *.mapping.yaml under contracts/domains/<domain>.
"""

import hashlib
import json
from typing import Any

import fastavro


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def avro_parsing_fingerprint(schema_json: dict[str, Any]) -> str:
    """SHA-256 over the Avro Parsing Canonical Form of the schema."""
    canonical = fastavro.schema.to_parsing_canonical_form(schema_json)
    return _sha256_hex(canonical)


def _normalize(value: Any) -> Any:
    """Recursively normalize a JSON value so key order does not affect the hash."""
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _collect_mapping_blocks(mapping_doc: dict[str, Any]) -> dict[str, Any]:
    """Collect the semantic blocks from a canonical mapping document.

    The record-level metadata (minus the cosmetic ``domain`` key) under
    "contract", and each field mapping under "field:<sourceField>".
    """
    blocks: dict[str, Any] = {}
    meta = mapping_doc.get("metadata")
    if isinstance(meta, dict):
        blocks["contract"] = {k: v for k, v in meta.items() if k != "domain"}
    for fm in mapping_doc.get("fieldMappings", []):
        if isinstance(fm, dict) and "sourceField" in fm:
            blocks[f"field:{fm['sourceField']}"] = {
                k: v for k, v in fm.items() if k != "sourceField"
            }
    return blocks


def mapping_metadata_hash(mapping_doc: dict[str, Any]) -> str:
    """SHA-256 over the normalized semantic metadata of a canonical mapping doc."""
    blocks = _collect_mapping_blocks(mapping_doc)
    normalized = _normalize(blocks)
    canonical = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return _sha256_hex(canonical)
