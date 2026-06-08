"""Dual fingerprints for the contract layer.

Two independent hashes are maintained so that a change to optimization semantics
is detectable even when the Avro serialization structure is unchanged, and vice
versa:

  avroParsingFingerprint   - identifies serialization-relevant structure. Computed
                             over the Avro Parsing Canonical Form, which by
                             definition excludes docs, defaults, aliases, and any
                             unknown properties. Source: generated Avro schema.

  optimizationMetadataHash - identifies the normalized xOptimization metadata.
                             Computed directly from the ODCS document so that it
                             is independent of any Avro-library preservation
                             behavior. Source: ODCS contract.
"""

import hashlib
import json
from typing import Any

import fastavro

from fl_op.core.constants import XOPT_ODCS_PROPERTY


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def avro_parsing_fingerprint(schema_json: dict[str, Any]) -> str:
    """SHA-256 over the Avro Parsing Canonical Form of the schema."""
    canonical = fastavro.schema.to_parsing_canonical_form(schema_json)
    return _sha256_hex(canonical)


def _find_odcs_property(custom_properties: Any, name: str) -> Any:
    if not isinstance(custom_properties, list):
        return None
    for item in custom_properties:
        if isinstance(item, dict) and item.get("property") == name:
            return item.get("value")
    return None


def _collect_odcs_xopt_blocks(odcs_doc: dict[str, Any]) -> dict[str, Any]:
    """Collect every xOptimization block from an ODCS document, keyed by location.

    Keys are "contract" for the root-level block and "field:<name>" for each
    field-level block. Returns only blocks that are present.
    """
    blocks: dict[str, Any] = {}
    root_xopt = _find_odcs_property(odcs_doc.get("customProperties"), XOPT_ODCS_PROPERTY)
    if isinstance(root_xopt, dict):
        blocks["contract"] = root_xopt
    for schema_obj in odcs_doc.get("schema", []):
        if not isinstance(schema_obj, dict):
            continue
        for prop in schema_obj.get("properties", []):
            if not isinstance(prop, dict):
                continue
            field_xopt = _find_odcs_property(prop.get("customProperties"), XOPT_ODCS_PROPERTY)
            if isinstance(field_xopt, dict):
                blocks[f"field:{prop['name']}"] = field_xopt
    return blocks


def _normalize(value: Any) -> Any:
    """Recursively normalize a JSON value so key order does not affect the hash."""
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def odcs_metadata_hash(odcs_doc: dict[str, Any]) -> str:
    """SHA-256 over the normalized xOptimization metadata of an ODCS document."""
    blocks = _collect_odcs_xopt_blocks(odcs_doc)
    normalized = _normalize(blocks)
    canonical = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return _sha256_hex(canonical)
