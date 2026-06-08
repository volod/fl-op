"""Adapter SPI conformance, manifest, and profile validation."""

from fl_op.adapters.base import infeasible_to_unassigned
from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.adapters.registry import get_adapter
from fl_op.adapters.spi import SolverAdapter
from fl_op.canonical.enums import ReasonCode
from fl_op.contracts.registry import FileRegistry


def test_periodic_adapter_satisfies_protocol() -> None:
    adapter = OrToolsPeriodicAdapter()
    assert isinstance(adapter, SolverAdapter)


def test_registry_resolves_profile_adapters() -> None:
    registry = FileRegistry()
    profile = registry.get_profile("agricultural-custom-services")
    for mode in ("periodic", "rolling"):
        adapter_id = profile.adapter_for_mode(mode)
        assert adapter_id is not None
        assert isinstance(get_adapter(adapter_id), SolverAdapter)


def test_manifest_fields_present() -> None:
    m = OrToolsPeriodicAdapter().manifest
    assert m.adapter_id == "ortools-periodic"
    assert m.solver_name == "google-ortools"
    assert "periodic" in m.supported_planning_modes


def test_profile_validation_passes_for_enforced_constraints() -> None:
    registry = FileRegistry()
    profile = registry.get_profile("agricultural-custom-services")
    report = OrToolsPeriodicAdapter().validate_profile(profile)
    assert report.ok, report.unsupported_constraints


def test_infeasible_records_use_canonical_reason_codes() -> None:
    task = infeasible_to_unassigned(
        {
            "order_id": "o1",
            "cluster_id": "c1",
            "reason_code": ReasonCode.NO_COMPATIBLE_BUNDLE.value,
            "detail": "no feasible vehicle/implement pair",
        }
    )
    assert task.reason_code == ReasonCode.NO_COMPATIBLE_BUNDLE
    assert task.details == {
        "detail": "no feasible vehicle/implement pair",
        "cluster_id": "c1",
    }
