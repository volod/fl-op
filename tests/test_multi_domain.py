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
        assert FileRegistry().active_domain == "agricultural"

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
