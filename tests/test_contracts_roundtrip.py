"""ODCS completeness, generated-Avro integrity, and suite validation."""

import pytest

from fl_op.contracts.fingerprint import odcs_metadata_hash
from fl_op.contracts.registry import FileRegistry
from fl_op.contracts.schema_gen import check_generation
from fl_op.contracts.validate import validate_suite


@pytest.fixture(scope="module")
def registry() -> FileRegistry:
    return FileRegistry()


def _odcs_contract_ids(registry: FileRegistry) -> list[str]:
    return [cid for cid in registry.list_contracts() if registry.get_entry(cid).odcs_ref]


def _avro_contract_ids(registry: FileRegistry) -> list[str]:
    return [cid for cid in registry.list_contracts() if registry.get_entry(cid).avro_ref]


def test_suite_validates(registry: FileRegistry) -> None:
    report = validate_suite(registry)
    assert report.ok, [
        (c.contract_id, c.errors) for c in report.contracts if not c.ok
    ] + report.profile_errors


def test_every_odcs_contract_has_bindings(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        assert odcs is not None
        assert odcs.bindings, f"{cid} has no xOptimization field bindings in ODCS"


def test_generated_avro_has_no_xoptimization(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        avro = registry.get_avro(cid)
        assert "x-optimization" not in avro.schema_json, (
            f"{cid}: generated Avro schema must not contain x-optimization blocks"
        )
        for field_def in avro.fields:
            assert "x-optimization" not in field_def, (
                f"{cid}: field '{field_def.get('name')}' must not contain x-optimization"
            )


def test_avro_generation_ready_for_all_odcs(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        assert odcs is not None
        report = check_generation(odcs.doc, cid, "avro")
        assert report.ok, f"{cid}: Avro generation check failed: {report.errors}"


def test_proto_generation_ready_for_all_odcs(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        assert odcs is not None
        report = check_generation(odcs.doc, cid, "proto")
        assert report.ok, f"{cid}: Proto generation check failed: {report.errors}"


def test_es_generation_ready_for_all_odcs(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        assert odcs is not None
        report = check_generation(odcs.doc, cid, "es")
        assert report.ok, f"{cid}: ES generation check failed: {report.errors}"


def test_fingerprints_are_deterministic(registry: FileRegistry) -> None:
    for cid in _odcs_contract_ids(registry):
        odcs = registry.get_odcs(cid)
        assert odcs is not None
        a = odcs_metadata_hash(odcs.doc)
        b = odcs_metadata_hash(odcs.doc)
        assert a == b, f"{cid}: odcs_metadata_hash is not deterministic"

    for cid in _avro_contract_ids(registry):
        avro = registry.get_avro(cid)
        a = avro.avro_parsing_fingerprint
        b = avro.avro_parsing_fingerprint
        assert a == b, f"{cid}: avro_parsing_fingerprint is not deterministic"
