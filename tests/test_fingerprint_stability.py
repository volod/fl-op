"""Dual-fingerprint independence and metadata-integrity guard."""

import copy

import pytest

from fl_op.contracts.fingerprint import avro_parsing_fingerprint, odcs_metadata_hash
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

_ODCS_DOC = {
    "id": "test",
    "customProperties": [
        {
            "property": "xOptimization",
            "value": {
                "extensionVersion": "0.1.0",
                "semanticModelRef": "urn:xopt:model:test:0.1.0",
                "dataProductRole": "assetMaster",
            },
        }
    ],
    "schema": [
        {
            "name": "test",
            "properties": [
                {
                    "name": "p",
                    "physicalType": "double",
                    "customProperties": [
                        {
                            "property": "xOptimization",
                            "value": {
                                "extensionVersion": "0.1.0",
                                "semanticTerm": "urn:xopt:capability:rated-power",
                                "binding": "asset.capabilities.ratedPower",
                                "canonicalUnit": "kW",
                            },
                        }
                    ],
                }
            ],
        }
    ],
}


def test_semantic_change_moves_only_metadata_hash() -> None:
    base_doc = copy.deepcopy(_ODCS_DOC)
    mutated_doc = copy.deepcopy(_ODCS_DOC)
    mutated_doc["schema"][0]["properties"][0]["customProperties"][0]["value"]["canonicalUnit"] = "W"

    assert odcs_metadata_hash(base_doc) != odcs_metadata_hash(mutated_doc)


def test_structural_change_moves_only_parsing_fingerprint() -> None:
    base = copy.deepcopy(_AVRO_SCHEMA)
    structural = copy.deepcopy(_AVRO_SCHEMA)
    structural["fields"].append({"name": "r", "type": ["null", "double"], "default": None})

    assert avro_parsing_fingerprint(base) != avro_parsing_fingerprint(structural)


def test_metadata_hash_ignores_key_order() -> None:
    base_doc = copy.deepcopy(_ODCS_DOC)
    reordered_doc = copy.deepcopy(_ODCS_DOC)
    block = reordered_doc["schema"][0]["properties"][0]["customProperties"][0]["value"]
    reordered_doc["schema"][0]["properties"][0]["customProperties"][0]["value"] = {
        "canonicalUnit": block["canonicalUnit"],
        "binding": block["binding"],
        "semanticTerm": block["semanticTerm"],
        "extensionVersion": block["extensionVersion"],
    }
    assert odcs_metadata_hash(base_doc) == odcs_metadata_hash(reordered_doc)


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
