"""Schema-evolution policy: change classification, bump policy, baselines."""

import pathlib
import shutil

import pytest
import yaml

from fl_op.contracts.evolution import (
    CHANGE_BACKWARD,
    CHANGE_BREAKING,
    CHANGE_IDENTICAL,
    ChangeReport,
    check_evolution,
    classify_change,
    freeze_baselines,
    schema_snapshot,
    version_policy_errors,
)
from fl_op.contracts.odcs_loader import load_odcs_contract
from fl_op.contracts.registry import FileRegistry
from fl_op.core.paths import CONTRACTS_ROOT

_FIELD = {"logicalType": "string", "physicalType": "string", "required": True}
_OPTIONAL = {"logicalType": "string", "physicalType": "string", "required": False}


def test_identical_schema_classifies_identical() -> None:
    fields = {"a": dict(_FIELD)}
    assert classify_change(fields, fields).change_class == CHANGE_IDENTICAL


def test_added_optional_field_is_backward_compatible() -> None:
    report = classify_change({"a": dict(_FIELD)}, {"a": dict(_FIELD), "b": dict(_OPTIONAL)})
    assert report.change_class == CHANGE_BACKWARD


def test_added_required_field_is_breaking() -> None:
    report = classify_change({"a": dict(_FIELD)}, {"a": dict(_FIELD), "b": dict(_FIELD)})
    assert report.change_class == CHANGE_BREAKING


def test_removed_field_is_breaking() -> None:
    report = classify_change({"a": dict(_FIELD), "b": dict(_FIELD)}, {"a": dict(_FIELD)})
    assert report.change_class == CHANGE_BREAKING


def test_type_change_is_breaking() -> None:
    changed = {"a": {**_FIELD, "logicalType": "number"}}
    report = classify_change({"a": dict(_FIELD)}, changed)
    assert report.change_class == CHANGE_BREAKING


def test_requiredness_change_is_breaking_in_both_directions() -> None:
    assert (
        classify_change({"a": dict(_FIELD)}, {"a": dict(_OPTIONAL)}).change_class
        == CHANGE_BREAKING
    )
    assert (
        classify_change({"a": dict(_OPTIONAL)}, {"a": dict(_FIELD)}).change_class
        == CHANGE_BREAKING
    )


def test_identical_allows_same_or_higher_version_but_not_lower() -> None:
    change = ChangeReport(CHANGE_IDENTICAL)
    assert version_policy_errors("c", "1.2.0", "1.2.0", change) == []
    assert version_policy_errors("c", "1.2.0", "1.2.1", change) == []
    assert version_policy_errors("c", "1.2.0", "1.1.0", change)


def test_backward_change_requires_at_least_minor_bump() -> None:
    change = ChangeReport(CHANGE_BACKWARD, ["added optional field 'x'"])
    assert version_policy_errors("c", "1.2.0", "1.2.0", change)
    assert version_policy_errors("c", "1.2.0", "1.2.1", change)
    assert version_policy_errors("c", "1.2.0", "1.3.0", change) == []
    assert version_policy_errors("c", "1.2.0", "2.0.0", change) == []


def test_breaking_change_requires_major_bump() -> None:
    change = ChangeReport(CHANGE_BREAKING, ["removed field 'x'"])
    assert version_policy_errors("c", "1.2.0", "1.3.0", change)
    assert version_policy_errors("c", "1.2.0", "2.0.0", change) == []


def test_schema_snapshot_extracts_fields_and_versions() -> None:
    odcs = load_odcs_contract(
        CONTRACTS_ROOT / "domains" / "agricultural" / "odcs" / "vehicles.odcs.yaml"
    )
    snapshot = schema_snapshot(odcs, "vehicles")
    assert snapshot["contractId"] == "vehicles"
    assert snapshot["version"] == odcs.version
    assert snapshot["fields"]["vehicle_id"]["required"] is True


def test_repository_baselines_are_current() -> None:
    """The committed baselines must match the committed contracts.

    This is the CI gate: a contract edit without a reviewed baseline refresh
    (and the policy-required version bump) fails here.
    """
    report = check_evolution()
    failing = [e for c in report.contracts for e in c.errors] + report.stale_baselines
    assert report.ok, failing


@pytest.fixture
def contracts_copy(tmp_path) -> pathlib.Path:
    """A mutable copy of the contracts tree (generated schemas excluded)."""
    dest = tmp_path / "contracts"
    shutil.copytree(
        CONTRACTS_ROOT, dest, ignore=shutil.ignore_patterns("generated", "__pycache__")
    )
    return dest


def _mutate_vehicles(root: pathlib.Path, version: str, add_required: bool) -> None:
    path = root / "domains" / "agricultural" / "odcs" / "vehicles.odcs.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["version"] = version
    doc["schema"][0]["properties"].append(
        {
            "name": "extra_field",
            "logicalType": "string",
            "physicalType": "string",
            "required": add_required,
        }
    )
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def test_check_fails_on_unbumped_breaking_change(contracts_copy) -> None:
    registry = FileRegistry(root=contracts_copy)
    freeze_baselines(registry)
    _mutate_vehicles(contracts_copy, version="1.0.0", add_required=True)

    report = check_evolution(FileRegistry(root=contracts_copy))
    vehicles = next(c for c in report.contracts if c.contract_id == "vehicles")
    assert vehicles.change_class == CHANGE_BREAKING
    assert vehicles.errors
    assert not report.ok


def test_check_accepts_minor_bump_for_added_optional_field(contracts_copy) -> None:
    registry = FileRegistry(root=contracts_copy)
    freeze_baselines(registry)
    _mutate_vehicles(contracts_copy, version="1.1.0", add_required=False)

    report = check_evolution(FileRegistry(root=contracts_copy))
    vehicles = next(c for c in report.contracts if c.contract_id == "vehicles")
    assert vehicles.change_class == CHANGE_BACKWARD
    assert vehicles.errors == []


def test_check_flags_missing_and_stale_baselines(contracts_copy) -> None:
    registry = FileRegistry(root=contracts_copy)
    freeze_baselines(registry)

    (contracts_copy / "evolution" / "vehicles.json").unlink()
    (contracts_copy / "evolution" / "ghost-contract.json").write_text("{}\n")

    report = check_evolution(FileRegistry(root=contracts_copy))
    vehicles = next(c for c in report.contracts if c.contract_id == "vehicles")
    assert vehicles.baseline_version is None
    assert vehicles.errors
    assert report.stale_baselines
