"""Multi-domain coverage: runnable construction pack and roadside example pack.

The construction pack is exercised end to end (generator -> snapshot ->
adapter -> canonical plan) with ACTIVE_DOMAIN=construction; the engine code
path is identical to the agricultural one, proving the canonical model keeps
the engine domain-neutral. The roadside pack is validation-level: mappings
must cover the canonical model and its monitoring profile must load.
"""

import os
import pathlib

import pytest

from fl_op.canonical.enums import PlanningMode
from fl_op.contracts.registry import FileRegistry

_CONSTRUCTION_COUNTS = {"machines": 12, "attachments": 40, "jobs": 10, "yards": 3}
_ROADSIDE_COUNTS = {"vehicles": 4, "kits": 8, "signs": 10, "depots": 2}


def _cap(name: str, value, unit: str = ""):
    from fl_op.canonical.asset import Capability

    return Capability(
        capability_id=name,
        semantic_term=f"urn:xopt:capability:{name}",
        value=value,
        canonical_unit=unit or None,
    )


def _shared_fleet_snapshot():
    from datetime import datetime, timedelta, timezone

    from fl_op.canonical.asset import Asset, GeoLocation
    from fl_op.canonical.bundle import BundleFeasibilitySummary
    from fl_op.canonical.common import TimeInterval, VersionDimensions
    from fl_op.canonical.enums import PlanningMode
    from fl_op.canonical.location import Location
    from fl_op.canonical.snapshot import PlanningSnapshot
    from fl_op.canonical.task import Task

    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    vehicle = Asset(
        asset_id="shared_prime_1",
        asset_type="shared-prime",
        roles=["mobile-prime-mover"],
        home_depot_ref="shared_depot",
        location=GeoLocation(lat=48.5, lon=32.0),
        capabilities=[
            _cap("rated-power", 240.0, "kW"),
            _cap("fuel-tank-volume", 200.0, "L"),
            _cap("fuel-consumption-rate", 18.0, "L/h"),
            _cap("travel-speed", 30.0, "km/h"),
        ],
    )
    implement = Asset(
        asset_id="shared_tool_1",
        asset_type="shared-tool",
        roles=["implement"],
        home_depot_ref="shared_depot",
        capabilities=[
            _cap("compatible-operations", ["SPRAYING", "EXCAVATION"]),
            _cap("required-power", 120.0, "kW"),
            _cap("working-width", 12.0, "m"),
            _cap("min-operating-speed", 4.0, "km/h"),
            _cap("max-operating-speed", 8.0, "km/h"),
            _cap("work-rates", {"m3": 100.0}),
        ],
    )
    operator = Asset(
        asset_id="shared_operator_1",
        asset_type="operator",
        roles=["operator"],
        home_depot_ref="shared_depot",
        capabilities=[
            _cap("operator-certification", ["SPRAYING", "EXCAVATION"]),
        ],
    )
    return PlanningSnapshot(
        snapshot_id="snap-shared",
        effective_at=now,
        generated_at=now,
        planning_mode=PlanningMode.PERIODIC,
        planning_horizon=TimeInterval(**{"from": now, "to": now + timedelta(days=7)}),
        version_dimensions=VersionDimensions(optimization_profile_version="0.1.0"),
        assets=[vehicle, implement, operator],
        locations=[
            Location(
                location_id="shared_depot",
                location_type="depot",
                lat=48.5,
                lon=32.0,
            ),
            Location(
                location_id="field_1",
                location_type="field",
                lat=48.51,
                lon=32.01,
                area_ha=5.0,
            ),
            Location(
                location_id="site_1",
                location_type="field",
                lat=48.52,
                lon=32.02,
                area_ha=1.0,
            ),
        ],
        bundle_summary=BundleFeasibilitySummary(),
        tasks=[
            Task(
                task_id="agri_order_1",
                order_id="contract-a",
                operation_type="SPRAYING",
                location_ref="field_1",
                area_ha=5.0,
                work_quantity=5.0,
                work_quantity_unit="ha",
                deadline=now + timedelta(days=2),
                revenue_value_eur=1200.0,
                penalty_per_day_eur=100.0,
            ),
            Task(
                task_id="construction_job_1",
                order_id="contract-c",
                operation_type="EXCAVATION",
                location_ref="site_1",
                area_ha=1.0,
                work_quantity=80.0,
                work_quantity_unit="m3",
                deadline=now + timedelta(days=3),
                revenue_value_eur=1800.0,
                penalty_per_day_eur=150.0,
            ),
        ],
    )


@pytest.fixture(scope="module")
def construction_dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Generate a small construction dataset once per module."""
    from fl_op.data.domain_generators import GenerationRequest, run_domain_generator

    base = tmp_path_factory.mktemp("construction-data")
    registry = FileRegistry()
    orig_cwd = os.getcwd()
    os.chdir(base)
    try:
        run_domain_generator(
            "construction",
            GenerationRequest(
                vehicles=_CONSTRUCTION_COUNTS["machines"],
                implements=_CONSTRUCTION_COUNTS["attachments"],
                orders=_CONSTRUCTION_COUNTS["jobs"],
                depots=_CONSTRUCTION_COUNTS["yards"],
                seed=21,
            ),
            registry=registry,
        )
        dirs = sorted((base / ".data" / "generate-data").iterdir())
        return dirs[-1]
    finally:
        os.chdir(orig_cwd)


@pytest.fixture(scope="module")
def construction_domain():
    """Activate the construction domain for the duration of the module."""
    prior = os.environ.get("ACTIVE_DOMAIN")
    os.environ["ACTIVE_DOMAIN"] = "construction"
    try:
        yield "construction"
    finally:
        if prior is None:
            os.environ.pop("ACTIVE_DOMAIN", None)
        else:
            os.environ["ACTIVE_DOMAIN"] = prior


@pytest.fixture(scope="module")
def construction_plan(construction_dataset_dir, construction_domain):
    from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
    from fl_op.snapshot import SnapshotBuilder

    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(
        construction_dataset_dir, PlanningMode.PERIODIC
    )
    profile = registry.get_profile(registry.active_profile_id)
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)
    return snapshot, plan


@pytest.fixture(scope="module")
def roadside_dataset_dir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Generate a small roadside dataset once per module."""
    from fl_op.data.domain_generators import GenerationRequest, run_domain_generator

    base = tmp_path_factory.mktemp("roadside-data")
    registry = FileRegistry()
    orig_cwd = os.getcwd()
    os.chdir(base)
    try:
        run_domain_generator(
            "roadside",
            GenerationRequest(
                vehicles=_ROADSIDE_COUNTS["vehicles"],
                implements=_ROADSIDE_COUNTS["kits"],
                orders=_ROADSIDE_COUNTS["signs"],
                depots=_ROADSIDE_COUNTS["depots"],
                seed=33,
            ),
            registry=registry,
        )
        dirs = sorted((base / ".data" / "generate-data").iterdir())
        return dirs[-1]
    finally:
        os.chdir(orig_cwd)


@pytest.fixture(scope="module")
def roadside_domain():
    """Activate the roadside domain for the duration of the module."""
    prior = os.environ.get("ACTIVE_DOMAIN")
    os.environ["ACTIVE_DOMAIN"] = "roadside"
    try:
        yield "roadside"
    finally:
        if prior is None:
            os.environ.pop("ACTIVE_DOMAIN", None)
        else:
            os.environ["ACTIVE_DOMAIN"] = prior


@pytest.fixture(scope="module")
def roadside_plan(roadside_dataset_dir, roadside_domain):
    from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
    from fl_op.snapshot import SnapshotBuilder

    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(
        roadside_dataset_dir, PlanningMode.PERIODIC
    )
    profile = registry.get_profile(registry.active_profile_id)
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)
    return snapshot, plan


class TestActiveDomainOverride:
    def test_default_active_domain_is_registry_index(self):
        assert FileRegistry().active_domain == "drone_logistics"

    def test_env_override_selects_domain(self, monkeypatch):
        monkeypatch.setenv("ACTIVE_DOMAIN", "construction")
        registry = FileRegistry()
        assert registry.active_domain == "construction"
        assert registry.active_profile_id == "construction-earthworks"

    def test_domain_local_contract_ids_resolve_inside_domain(self):
        registry = FileRegistry()
        assert registry.resolve_contract_id("operators", domain="construction") == (
            "construction-operators"
        )
        assert registry.resolve_contract_id("operators", domain="agricultural") == (
            "operators"
        )
        assert registry.resolve_contract_id("roadside/service-vehicles") == (
            "roadside-service-vehicles"
        )

    def test_unknown_override_is_rejected(self, monkeypatch):
        monkeypatch.setenv("ACTIVE_DOMAIN", "ghost-domain")
        with pytest.raises(KeyError, match="ghost-domain"):
            _ = FileRegistry().active_domain

    def test_active_domains_env_selects_shared_domain_set(self, monkeypatch):
        monkeypatch.setenv("ACTIVE_DOMAINS", "agricultural,construction")
        assert FileRegistry().active_domains == ["agricultural", "construction"]


class TestSharedFleetPlanning:
    def test_solver_projection_unions_domain_bindings(self):
        from fl_op.solver.inputs import SECTION_RELATED, SECTION_TASKS, build_solver_inputs

        rows = build_solver_inputs(
            _shared_fleet_snapshot(),
            FileRegistry(),
            domains=["agricultural", "construction"],
        )
        tasks = {task.task_id: task for task in rows[SECTION_TASKS]}

        assert tasks["construction_job_1"].work_quantity_unit == "m3"
        assert rows[SECTION_RELATED][0].work_rates == {"m3": 100.0}

    def test_periodic_adapter_plans_shared_fleet_across_domains(self):
        from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter

        registry = FileRegistry()
        profile = registry.get_profile("agricultural-custom-services")
        plan = OrToolsPeriodicAdapter().plan(
            _shared_fleet_snapshot(),
            profile,
            {"domains": ["agricultural", "construction"]},
        )
        covered = {a.task_id for a in plan.assignments} | {
            u.task_id for u in plan.unassigned_tasks
        }

        assert covered == {"agri_order_1", "construction_job_1"}
        assert plan.assignments


class TestConstructionGenerator:
    def test_datasets_match_odcs_physical_fields(self, construction_dataset_dir):
        """Every generated dataset carries exactly its contract's columns."""
        from fl_op.contracts.odcs_loader import load_odcs_contract
        from fl_op.io import detect_format, get_codec, locate_source

        registry = FileRegistry()
        codec = get_codec(detect_format(construction_dataset_dir))
        for cid in registry.list_contracts():
            entry = registry.get_entry(cid)
            if entry.domain != "construction":
                continue
            rows = codec.read(
                locate_source(construction_dataset_dir, entry.source_file, codec)
            )
            assert rows, f"{cid}: generated dataset is empty"
            declared = set(
                load_odcs_contract(FileRegistry().root / entry.odcs_ref).field_names()
            )
            assert set(rows[0].keys()) == declared, cid


class TestConstructionSnapshot:
    def test_snapshot_carries_all_roles_and_entities(self, construction_plan):
        snapshot, _ = construction_plan
        roles = {role for asset in snapshot.assets for role in asset.roles}
        assert {"mobile-prime-mover", "implement", "operator"} <= roles
        location_types = {loc.location_type for loc in snapshot.locations}
        assert location_types == {"depot", "field"}
        assert len(snapshot.tasks) == _CONSTRUCTION_COUNTS["jobs"]

    def test_solver_rows_project_through_domain_neutral_tables(
        self, construction_dataset_dir, construction_domain
    ):
        from fl_op.snapshot import SnapshotBuilder
        from fl_op.solver.inputs import (
            SECTION_DEPOTS,
            SECTION_OPERATORS,
            SECTION_PRIME_MOVERS,
            SECTION_RELATED,
            SECTION_SITES,
            SECTION_TASKS,
            build_solver_inputs,
        )

        registry = FileRegistry()
        snapshot = SnapshotBuilder(registry).build(construction_dataset_dir)
        rows = build_solver_inputs(snapshot, registry)
        assert len(rows[SECTION_PRIME_MOVERS]) == _CONSTRUCTION_COUNTS["machines"]
        assert len(rows[SECTION_RELATED]) == _CONSTRUCTION_COUNTS["attachments"]
        assert len(rows[SECTION_OPERATORS]) == _CONSTRUCTION_COUNTS["machines"]
        assert len(rows[SECTION_DEPOTS]) == _CONSTRUCTION_COUNTS["yards"]
        assert len(rows[SECTION_SITES]) == _CONSTRUCTION_COUNTS["jobs"]
        assert len(rows[SECTION_TASKS]) == _CONSTRUCTION_COUNTS["jobs"]
        machine = rows[SECTION_PRIME_MOVERS][0]
        assert machine.asset_id.startswith("machine_")
        assert machine.rated_power > 0

    def test_volume_jobs_carry_native_quantity_and_rates(
        self, construction_dataset_dir, construction_domain
    ):
        """Earthworks-native m3 quantities and attachment work rates project
        through the canonical work-rate capability surface."""
        from fl_op.snapshot import SnapshotBuilder
        from fl_op.solver.inputs import (
            SECTION_RELATED,
            SECTION_TASKS,
            build_solver_inputs,
        )

        registry = FileRegistry()
        snapshot = SnapshotBuilder(registry).build(construction_dataset_dir)
        rows = build_solver_inputs(snapshot, registry)

        units = {t.work_quantity_unit for t in rows[SECTION_TASKS]}
        assert units <= {"m3", "ha"}
        volume_tasks = [t for t in rows[SECTION_TASKS] if t.work_quantity_unit == "m3"]
        assert volume_tasks, "expected volume-shaped jobs in the dataset"
        assert all(t.work_quantity > 0 for t in volume_tasks)

        rated = [r for r in rows[SECTION_RELATED] if r.work_rates]
        assert rated, "expected attachments declaring work rates"
        assert all(r.work_rates.get("m3", 0) > 0 for r in rated)


class TestConstructionPlanEndToEnd:
    def test_every_job_assigned_or_explained(self, construction_plan):
        snapshot, plan = construction_plan
        covered = {a.task_id for a in plan.assignments} | {
            u.task_id for u in plan.unassigned_tasks
        }
        assert covered == {t.task_id for t in snapshot.tasks}

    def test_assignments_bundle_construction_assets(self, construction_plan):
        _, plan = construction_plan
        assert plan.assignments, "construction plan should assign jobs"
        for assignment in plan.assignments:
            assert any(aid.startswith("machine_") for aid in assignment.asset_ids)
            assert any(aid.startswith("attachment_") for aid in assignment.asset_ids)

    def test_plan_conforms_to_output_contract(self, construction_plan):
        from fl_op.contracts.plan_contract import assert_plan_conforms

        _, plan = construction_plan
        assert_plan_conforms(plan)


class TestRoadsidePack:
    def test_domain_mappings_cover_canonical_model(self):
        from fl_op.contracts.validate import validate_domain

        report = validate_domain("roadside")
        assert report.ok, report.errors
        assert set(report.entities_covered) == {
            "asset",
            "location",
            "observation",
            "task",
        }

    def test_inspection_metric_codes_normalize_to_canonical(self):
        from fl_op.contracts.mapping_loader import load_mapping

        registry = FileRegistry()
        mapping = load_mapping(
            registry.root / "domains/roadside/mappings/inspection-rounds.mapping.yaml"
        )
        assert mapping.metric_codes["sign_condition"] == "health-status"
        assert mapping.metric_codes["battery_pct"] == "battery-level"

    def test_profile_monitoring_overrides_layer_per_asset_type(self):
        from fl_op.contracts.profile import load_profile

        profile = load_profile(
            FileRegistry().root / "domains/roadside/profile.yaml"
        )
        assert profile.metadata.id == "roadside-infrastructure"
        base = profile.monitoring
        assert base.batteryLowThresholdPct == 25.0
        radar = base.for_asset_type("speed_radar")
        assert radar.batteryLowThresholdPct == 35.0
        assert radar.serviceDeadlineDays == 2
        # Unspecified override fields inherit the base policy.
        assert radar.batteryForecastHorizonDays == base.batteryForecastHorizonDays

    def test_roadside_generator_writes_registered_sources(self, roadside_dataset_dir):
        from fl_op.io import detect_format, get_codec, locate_source

        registry = FileRegistry()
        codec = get_codec(detect_format(roadside_dataset_dir))
        for cid in registry.list_contracts():
            entry = registry.get_entry(cid)
            if entry.domain != "roadside":
                continue
            path = (
                roadside_dataset_dir / entry.source_file
                if entry.source_format == "jsonl"
                else locate_source(roadside_dataset_dir, entry.source_file, codec)
            )
            assert path.exists(), cid

    def test_roadside_snapshot_derives_dispatchable_service_tasks(
        self, roadside_plan
    ):
        from fl_op.solver.inputs import (
            SECTION_OPERATORS,
            SECTION_PRIME_MOVERS,
            SECTION_RELATED,
            SECTION_TASKS,
            build_solver_inputs,
        )

        snapshot, _ = roadside_plan
        service_tasks = [
            task for task in snapshot.tasks if task.source_ref.startswith("monitoring:")
        ]
        assert service_tasks, "inspection findings should derive service visits"
        assert all(task.operation_type == "EQUIPMENT_SERVICE" for task in service_tasks)

        registry = FileRegistry()
        rows = build_solver_inputs(snapshot, registry)
        assert len(rows[SECTION_PRIME_MOVERS]) == _ROADSIDE_COUNTS["vehicles"]
        assert len(rows[SECTION_RELATED]) == _ROADSIDE_COUNTS["kits"]
        assert len(rows[SECTION_OPERATORS]) == _ROADSIDE_COUNTS["vehicles"]
        assert len(rows[SECTION_TASKS]) == len(snapshot.tasks)

    def test_roadside_plan_dispatches_monitoring_visits(self, roadside_plan):
        snapshot, plan = roadside_plan
        service_task_ids = {t.task_id for t in snapshot.tasks}
        covered = {a.task_id for a in plan.assignments} | {
            u.task_id for u in plan.unassigned_tasks
        }
        assert covered == service_task_ids
        assert plan.assignments, "roadside service visits should be dispatchable"
        assert any(a.task_id.startswith("service-sign_") for a in plan.assignments)
