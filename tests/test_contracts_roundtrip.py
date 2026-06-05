"""Avro x-optimization metadata round-trip and ODCS/Avro binding agreement (spec 9.4, 29.1)."""

import pytest

from fl_op.contracts.fingerprint import collect_xopt_blocks
from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.validate import validate_suite


@pytest.fixture(scope="module")
def registry() -> FileRegistry:
    return FileRegistry()


def _avro_contract_ids(registry: FileRegistry) -> list[str]:
    return [cid for cid in registry.list_contracts() if registry.get_entry(cid).avro_ref]


def test_suite_validates(registry: FileRegistry) -> None:
    report = validate_suite(registry)
    assert report.ok, [
        (c.contract_id, c.errors) for c in report.contracts if not c.ok
    ] + report.profile_errors


def test_every_contract_has_bindings(registry: FileRegistry) -> None:
    for cid in _avro_contract_ids(registry):
        avro = registry.get_avro(cid)
        assert avro.bindings, f"{cid} has no x-optimization field bindings"
        assert avro.record_meta is not None, f"{cid} has no record-level x-optimization"


def test_metadata_survives_fastavro_roundtrip(registry: FileRegistry) -> None:
    for cid in _avro_contract_ids(registry):
        avro = registry.get_avro(cid)
        before = collect_xopt_blocks(avro.schema_json)
        after = collect_xopt_blocks(avro.roundtrip_metadata())
        assert before == after, f"{cid}: x-optimization metadata lost on round-trip"
        assert before, f"{cid}: expected at least one x-optimization block"


def test_odcs_bindings_match_avro(registry: FileRegistry) -> None:
    for cid in _avro_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        if odcs is None:
            continue
        avro_map = {b.source_field: b.binding for b in registry.get_avro(cid).bindings}
        assert odcs.binding_map() == avro_map, f"{cid}: ODCS/Avro binding mismatch"


def test_fingerprints_are_deterministic(registry: FileRegistry) -> None:
    for cid in _avro_contract_ids(registry):
        a = registry.get_avro(cid).fingerprints
        b = registry.get_avro(cid).fingerprints
        assert a == b, f"{cid}: fingerprints not deterministic across loads"
