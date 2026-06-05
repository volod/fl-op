"""Dual-fingerprint independence and metadata-loss guard (spec 9.5)."""

import copy

import pytest

from fl_op.contracts.fingerprint import (
    avro_parsing_fingerprint,
    optimization_metadata_hash,
)
from fl_op.contracts.registry import FileRegistry, MetadataLossError

_SCHEMA = {
    "type": "record",
    "name": "R",
    "namespace": "test",
    "x-optimization": {"extensionVersion": "0.1.0", "semanticEntity": "urn:xopt:entity:asset"},
    "fields": [
        {
            "name": "p",
            "type": "double",
            "x-optimization": {
                "extensionVersion": "0.1.0",
                "semanticTerm": "urn:xopt:capability:rated-power",
                "binding": "asset.capabilities.ratedPower",
                "canonicalUnit": "kW",
            },
        }
    ],
}


def test_semantic_change_moves_only_metadata_hash() -> None:
    base = copy.deepcopy(_SCHEMA)
    mutated = copy.deepcopy(_SCHEMA)
    mutated["fields"][0]["x-optimization"]["canonicalUnit"] = "W"  # semantic change

    assert avro_parsing_fingerprint(base) == avro_parsing_fingerprint(mutated)
    assert optimization_metadata_hash(base) != optimization_metadata_hash(mutated)


def test_structural_change_moves_only_parsing_fingerprint() -> None:
    base = copy.deepcopy(_SCHEMA)
    structural = copy.deepcopy(_SCHEMA)
    structural["fields"].append({"name": "q", "type": ["null", "double"], "default": None})

    assert avro_parsing_fingerprint(base) != avro_parsing_fingerprint(structural)
    assert optimization_metadata_hash(base) == optimization_metadata_hash(structural)


def test_metadata_hash_ignores_key_order() -> None:
    base = copy.deepcopy(_SCHEMA)
    reordered = copy.deepcopy(_SCHEMA)
    # Rebuild the block with keys inserted in a different order.
    block = reordered["fields"][0]["x-optimization"]
    reordered["fields"][0]["x-optimization"] = {
        "canonicalUnit": block["canonicalUnit"],
        "binding": block["binding"],
        "semanticTerm": block["semanticTerm"],
        "extensionVersion": block["extensionVersion"],
    }
    assert optimization_metadata_hash(base) == optimization_metadata_hash(reordered)


def test_metadata_loss_guard_raises_on_divergent_stored_hash() -> None:
    registry = FileRegistry()
    cid = "vehicles"
    entry = registry.get_entry(cid)
    # Inject a stale stored hash to simulate undetected metadata drift.
    entry.stored_fingerprints = {"optimizationMetadataHash": "deadbeef"}
    with pytest.raises(MetadataLossError):
        registry.verify_no_metadata_loss(cid)


def test_metadata_loss_guard_passes_for_matching_hash() -> None:
    registry = FileRegistry()
    cid = "vehicles"
    computed = registry.get_avro(cid).fingerprints
    registry.get_entry(cid).stored_fingerprints = dict(computed)
    # Should not raise.
    assert registry.verify_no_metadata_loss(cid)["optimizationMetadataHash"] == (
        computed["optimizationMetadataHash"]
    )
