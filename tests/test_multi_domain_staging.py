"""Multi-domain staging, policy composition, and capability metadata.

Covers the "Multi-domain staging and policy composition" improvement:

* composite profile merging (weather stricter bounds, monitoring overrides
  unioned with primary scalars winning, constraints unioned with enforced
  winning, contract/mode unions),
* collision-free mixed-domain source staging (flat layout collides, per-domain
  subdirectories resolve it) and the warning findings it emits,
* missing-source detection and its warning findings,
* generator capability metadata derived from the registry.
"""

import json
import pathlib
from datetime import datetime, timezone

from fl_op.contracts.profile import (
    ConstraintSpec,
    MonitoringPolicyOverride,
    MonitoringPolicySpec,
    WeatherPolicySpec,
    _merge_constraints,
)
from fl_op.contracts.registry import FileRegistry
from fl_op.data.domain_generators import (
    all_generator_capabilities,
    domain_generator_capabilities,
    registered_generator_domains,
)
from fl_op.snapshot.builder import (
    SnapshotBuilder,
    _missing_source_findings,
    _source_collision_findings,
)

_DETECTED_AT = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _write_metadata(data_dir: pathlib.Path, data_format: str = "csv") -> None:
    """Write the minimal metadata detect_format needs to pick a codec."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "metadata.json").write_text(
        json.dumps({"run_metadata": {"data_format": data_format}})
    )


# -- policy composition --------------------------------------------------------


def test_weather_policy_composed_takes_stricter_bound_and_unions_sensitivity():
    primary = WeatherPolicySpec(
        maxWindMs=10.0,
        maxRainMmPerH=5.0,
        maxSoilMoisturePct=40.0,
        sensitivity={"spraying": ["wind", "rain"]},
    )
    secondary = WeatherPolicySpec(
        maxWindMs=8.0,
        maxRainMmPerH=9.0,
        maxSoilMoisturePct=30.0,
        sensitivity={"aerial-survey": ["wind"]},
    )

    merged = primary.composed_with(secondary)

    # Stricter (lower) bound of either pack wins on every limit.
    assert merged.maxWindMs == 8.0
    assert merged.maxRainMmPerH == 5.0
    assert merged.maxSoilMoisturePct == 30.0
    # Sensitivity maps union across domains.
    assert merged.sensitivity == {
        "spraying": ["wind", "rain"],
        "aerial-survey": ["wind"],
    }


def test_weather_policy_composed_primary_wins_shared_sensitivity_key():
    primary = WeatherPolicySpec(sensitivity={"op": ["wind"]})
    secondary = WeatherPolicySpec(sensitivity={"op": ["rain"]})

    merged = primary.composed_with(secondary)

    assert merged.sensitivity["op"] == ["wind"]


def test_monitoring_policy_composed_keeps_primary_scalars_unions_overrides():
    primary = MonitoringPolicySpec(
        servicePriorityClass=2,
        assetTypeOverrides={"sensor": MonitoringPolicyOverride(servicePriorityClass=1)},
        assetOverrides={"s-1": MonitoringPolicyOverride(serviceDeadlineDays=1)},
    )
    secondary = MonitoringPolicySpec(
        servicePriorityClass=9,
        assetTypeOverrides={"uav": MonitoringPolicyOverride(servicePriorityClass=3)},
        assetOverrides={"u-1": MonitoringPolicyOverride(serviceDeadlineDays=7)},
    )

    merged = primary.composed_with(secondary)

    # Scalars keep the primary profile's value.
    assert merged.servicePriorityClass == 2
    # Override maps union across domains.
    assert set(merged.assetTypeOverrides) == {"sensor", "uav"}
    assert set(merged.assetOverrides) == {"s-1", "u-1"}


def test_monitoring_policy_composed_primary_wins_override_collision():
    primary = MonitoringPolicySpec(
        assetTypeOverrides={"sensor": MonitoringPolicyOverride(servicePriorityClass=1)}
    )
    secondary = MonitoringPolicySpec(
        assetTypeOverrides={"sensor": MonitoringPolicyOverride(servicePriorityClass=8)}
    )

    merged = primary.composed_with(secondary)

    assert merged.assetTypeOverrides["sensor"].servicePriorityClass == 1


def test_merge_constraints_unions_by_id_enforced_wins():
    primary = [ConstraintSpec(id="c1", severity="hard", enforced=False)]
    secondary = [
        ConstraintSpec(id="c1", severity="hard", enforced=True),
        ConstraintSpec(id="c2", severity="soft", enforced=False),
    ]

    merged = _merge_constraints(primary, secondary)

    by_id = {c.id: c for c in merged}
    assert set(by_id) == {"c1", "c2"}
    # An enforced constraint wins the conflict so no hard rule is relaxed.
    assert by_id["c1"].enforced is True


def test_merge_constraints_keeps_primary_enforced_over_relaxed_secondary():
    primary = [ConstraintSpec(id="c1", severity="hard", enforced=True)]
    secondary = [ConstraintSpec(id="c1", severity="hard", enforced=False)]

    merged = _merge_constraints(primary, secondary)

    assert len(merged) == 1
    assert merged[0].enforced is True


def test_composite_profile_merges_two_domain_profiles():
    registry = FileRegistry()
    profile_ids = registry.domain_profile_ids(["agricultural", "drone_logistics"])
    if len(profile_ids) < 2:
        # Both packs declare profiles in the shipped registry; skip defensively
        # if a future registry trims one rather than asserting on a fixture.
        return

    primary = registry.get_profile(profile_ids[0])
    secondary = registry.get_profile(profile_ids[1])
    composite = registry.composite_profile(["agricultural", "drone_logistics"])

    assert composite is not None
    # Weather limits collapse to the stricter (lower) bound of either profile.
    assert composite.weatherPolicy.maxWindMs == min(
        primary.weatherPolicy.maxWindMs, secondary.weatherPolicy.maxWindMs
    )
    assert composite.weatherPolicy.maxRainMmPerH == min(
        primary.weatherPolicy.maxRainMmPerH, secondary.weatherPolicy.maxRainMmPerH
    )
    # Identity/scalars come from the primary profile.
    assert composite.monitoring.servicePriorityClass == (
        primary.monitoring.servicePriorityClass
    )
    # Input contracts union (primary order preserved, unseen appended).
    for cid in primary.inputContracts:
        assert cid in composite.inputContracts
    for cid in secondary.inputContracts:
        assert cid in composite.inputContracts


def test_composite_profile_none_when_no_domain_declares_profile():
    registry = FileRegistry()
    # No selected domains means no profile ids to compose, so callers fall back
    # to engine defaults exactly as before.
    assert registry.composite_profile([]) is None


# -- collision-free mixed-domain staging --------------------------------------


def test_source_collisions_detected_for_shared_flat_file(tmp_path):
    registry = FileRegistry()
    data_dir = tmp_path / "flat"
    _write_metadata(data_dir)

    builder = SnapshotBuilder(registry=registry, domains=["agricultural", "construction"])
    collisions = builder.source_collisions(data_dir)

    # operators.csv is staged by both packs; the flat layout makes both
    # contracts resolve to the same physical path -> a collision per contract.
    collided_files = {file_name for _, file_name, _ in collisions}
    assert "operators.csv" in collided_files


def test_source_collisions_resolved_by_per_domain_subdirs(tmp_path):
    registry = FileRegistry()
    data_dir = tmp_path / "staged"
    _write_metadata(data_dir)
    for domain in ("agricultural", "construction"):
        domain_dir = data_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        (domain_dir / "operators.csv").write_text("id\n")

    builder = SnapshotBuilder(registry=registry, domains=["agricultural", "construction"])
    collisions = builder.source_collisions(data_dir)

    collided_files = {file_name for _, file_name, _ in collisions}
    assert "operators.csv" not in collided_files


def test_missing_source_files_reported_when_data_dir_empty(tmp_path):
    registry = FileRegistry()
    data_dir = tmp_path / "empty"
    _write_metadata(data_dir)

    builder = SnapshotBuilder(registry=registry, domains=["agricultural"])
    missing = builder.missing_source_files(data_dir)

    # No source files were written, so every mapped contract is missing.
    assert {cid for cid, _ in missing} == set(builder.mapped_contracts)
    assert builder.mapped_contracts  # guard: the domain maps at least one contract


# -- quality finding helpers ---------------------------------------------------


def test_missing_source_findings_helper_shape():
    findings = _missing_source_findings(
        [("agricultural.operators", "operators.csv")], _DETECTED_AT
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "dq://dataset/source-file-missing"
    assert finding.entity_ref == "agricultural.operators"
    assert finding.field_ref == "operators.csv"
    assert finding.severity.value == "warning"


def test_source_collision_findings_helper_shape():
    findings = _source_collision_findings(
        [("agricultural.operators", "operators.csv", "construction")], _DETECTED_AT
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule_id == "dq://dataset/source-file-collision"
    assert finding.entity_ref == "agricultural.operators"
    assert finding.field_ref == "operators.csv"
    assert "construction" in finding.planning_impact


# -- generator capability metadata --------------------------------------------


def test_domain_generator_capabilities_shape():
    registry = FileRegistry()
    domains = registered_generator_domains(registry)
    assert domains  # the shipped registry declares generator-bearing domains

    caps = domain_generator_capabilities(domains[0], registry)
    assert caps["domain"] == domains[0]
    assert caps["generator"]
    assert isinstance(caps["canonicalEntities"], list)
    assert isinstance(caps["contracts"], list)
    assert isinstance(caps["sourceFormats"], list)
    assert isinstance(caps["declared"], dict)


def test_all_generator_capabilities_covers_every_generator_domain():
    registry = FileRegistry()
    caps = all_generator_capabilities(registry)
    assert set(caps) == set(registered_generator_domains(registry))
    for domain, payload in caps.items():
        assert payload["domain"] == domain
