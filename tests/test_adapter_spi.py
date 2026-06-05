"""Adapter SPI conformance, manifest, profile validation, reason-code coverage (spec 21)."""

from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.adapters.reason_codes import to_reason_code
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


def test_reason_code_mapping_covers_all_legacy_reasons() -> None:
    legacy = [
        "no_compatible_vehicle_implement_pair",
        "prize_collecting_unserved",
        "solver_timeout",
        "worker_crash",
        "unknown",
    ]
    for r in legacy:
        assert isinstance(to_reason_code(r), ReasonCode)
    assert to_reason_code("no_compatible_vehicle_implement_pair") == ReasonCode.NO_COMPATIBLE_BUNDLE
    assert to_reason_code("anything-else") == ReasonCode.UNKNOWN
