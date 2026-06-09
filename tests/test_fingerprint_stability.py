"""Dual-fingerprint independence and metadata-integrity guard."""

import copy

import pytest

from fl_op.contracts.fingerprint import avro_parsing_fingerprint, mapping_metadata_hash
from fl_op.contracts.registry import FileRegistry, MetadataLossError

_AVRO_SCHEMA = {
    "type": "record",
    "name": "R",
    "namespace": "test",
    "fields": [
        {"name": "p", "type": "double"},
        {"name": "q", "type": "string"},
    ],
}

_MAPPING_DOC = {
    "metadata": {
        "domain": "test",
        "sourceContract": "vehicles",
        "canonicalEntity": "asset",
        "canonicalModelRef": "urn:xopt:model:canonical:0.1.0",
    },
    "fieldMappings": [
        {
            "sourceField": "rated_power_kw",
            "binding": "asset.capabilities.ratedPower",
            "semanticTerm": "urn:xopt:capability:rated-power",
            "canonicalUnit": "kW",
        }
    ],
}


def test_semantic_change_moves_only_metadata_hash() -> None:
    base_doc = copy.deepcopy(_MAPPING_DOC)
    mutated_doc = copy.deepcopy(_MAPPING_DOC)
    mutated_doc["fieldMappings"][0]["canonicalUnit"] = "W"

    assert mapping_metadata_hash(base_doc) != mapping_metadata_hash(mutated_doc)


def test_structural_change_moves_only_parsing_fingerprint() -> None:
    base = copy.deepcopy(_AVRO_SCHEMA)
    structural = copy.deepcopy(_AVRO_SCHEMA)
    structural["fields"].append({"name": "r", "type": ["null", "double"], "default": None})

    assert avro_parsing_fingerprint(base) != avro_parsing_fingerprint(structural)


def test_metadata_hash_ignores_key_order() -> None:
    base_doc = copy.deepcopy(_MAPPING_DOC)
    reordered_doc = copy.deepcopy(_MAPPING_DOC)
    fm = reordered_doc["fieldMappings"][0]
    reordered_doc["fieldMappings"][0] = {
        "canonicalUnit": fm["canonicalUnit"],
        "binding": fm["binding"],
        "sourceField": fm["sourceField"],
        "semanticTerm": fm["semanticTerm"],
    }
    assert mapping_metadata_hash(base_doc) == mapping_metadata_hash(reordered_doc)


def test_metadata_loss_guard_raises_on_divergent_stored_hash() -> None:
    registry = FileRegistry()
    cid = "vehicles"
    entry = registry.get_entry(cid)
    entry.stored_fingerprints = {"optimizationMetadataHash": "deadbeef"}
    with pytest.raises(MetadataLossError):
        registry.verify_no_metadata_loss(cid)


def test_metadata_loss_guard_passes_for_matching_hash() -> None:
    registry = FileRegistry()
    cid = "vehicles"
    computed = registry.compute_fingerprints(cid)
    registry.get_entry(cid).stored_fingerprints = dict(computed)
    result = registry.verify_no_metadata_loss(cid)
    assert result["optimizationMetadataHash"] == computed["optimizationMetadataHash"]
