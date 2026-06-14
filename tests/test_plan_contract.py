"""Canonical plan output contract: declaration and payload validation."""

from datetime import datetime, timezone

import pytest

from fl_op.canonical.common import VersionDimensions
from fl_op.canonical.enums import CorrectiveActionType, PlanningMode, ReasonCode
from fl_op.canonical.plan import (
    Assignment,
    CorrectiveAction,
    MaterialReservation,
    Plan,
    UnassignedTask,
)
from fl_op.contracts.plan_contract import (
    _PLAN_BINDING_PATHS,
    assert_plan_conforms,
    validate_plan_payload,
)


def _ts() -> datetime:
    return datetime(2026, 6, 1, tzinfo=timezone.utc)


def _plan(**overrides) -> Plan:
    base = dict(
        plan_id="plan-periodic-abc",
        revision_id="rev-1",
        origin_plan_id="plan-periodic-abc",
        planning_mode=PlanningMode.PERIODIC,
        snapshot_id="snap-1",
        version_dimensions=VersionDimensions(optimization_profile_version="0.1.0"),
        adapter_id="ortools-periodic",
        adapter_version="0.1.0",
        generated_at=_ts(),
        effective_from=_ts(),
        assignments=[
            Assignment(
                assignment_id="a-1",
                task_id="t-1",
                bundle_id="bundle-1",
                asset_ids=["v0", "i0"],
                planned_start=_ts(),
                planned_finish=_ts(),
            )
        ],
        unassigned_tasks=[
            UnassignedTask(task_id="t-2", reason_code=ReasonCode.OPTIMIZATION_TRADEOFF)
        ],
        material_reservations=[
            MaterialReservation(
                reservation_id="res-1",
                task_id="t-1",
                material_type="fertilizer",
                inventory_location_ref="d0",
                quantity=120.0,
                canonical_unit="kg",
            )
        ],
        score={
            "optimization_objective": "cost",
            "total_estimated_margin_eur": 120.0,
            "n_dispatched": 1,
            "n_unassigned": 1,
            "n_clusters": 1,
        },
    )
    base.update(overrides)
    return Plan(**base)


def test_plan_entity_is_declared_in_canonical_model() -> None:
    from fl_op.contracts.canonical_model import load_canonical_model

    model = load_canonical_model()
    assert "plan" in model.entities()
    assert "plan.planId" in model.required_bindings("plan")
    # The output contract mirrors the input contracts: every declared field
    # references a known semantic term.
    for fld in model.fields_for("plan"):
        assert model.has_term(fld.semantic_term), fld.semantic_term


def test_every_contract_binding_has_a_payload_path() -> None:
    from fl_op.contracts.canonical_model import load_canonical_model

    model = load_canonical_model()
    declared = {f.binding for f in model.fields_for("plan")}
    assert declared <= set(_PLAN_BINDING_PATHS), (
        "unmapped plan bindings: " f"{sorted(declared - set(_PLAN_BINDING_PATHS))}"
    )


def test_complete_plan_payload_validates() -> None:
    payload = _plan().model_dump(mode="json", by_alias=True)
    assert validate_plan_payload(payload) == []


def test_missing_required_plan_field_is_reported() -> None:
    payload = _plan().model_dump(mode="json", by_alias=True)
    payload["snapshot_id"] = ""
    errors = validate_plan_payload(payload)
    assert any("plan.snapshotRef" in e for e in errors)


def test_missing_required_record_field_is_reported() -> None:
    payload = _plan().model_dump(mode="json", by_alias=True)
    payload["assignments"][0]["task_id"] = ""
    errors = validate_plan_payload(payload)
    assert any("plan.assignment.taskRef" in e for e in errors)


def test_corrective_action_contract_fields_are_validated() -> None:
    payload = _plan(
        corrective_actions=[
            CorrectiveAction(
                action=CorrectiveActionType.SERVICE_WITHDRAWN,
                task_id="t-service",
                detail="new readings cleared service need",
                evidence={"source": "observation"},
            )
        ]
    ).model_dump(mode="json", by_alias=True)

    assert validate_plan_payload(payload) == []
    payload["corrective_actions"][0]["task_id"] = ""
    errors = validate_plan_payload(payload)
    assert any("plan.correctiveAction.taskRef" in e for e in errors)


def test_assert_plan_conforms_raises_on_violation() -> None:
    plan = _plan(snapshot_id="")
    with pytest.raises(ValueError, match="canonical plan contract"):
        assert_plan_conforms(plan)


def test_adapter_built_plan_conforms(dataset_dir) -> None:
    """A real adapter-produced plan satisfies the output contract end to end."""
    from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
    from fl_op.contracts.registry import FileRegistry
    from fl_op.snapshot import SnapshotBuilder

    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(dataset_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("agricultural-custom-services")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)
    assert_plan_conforms(plan)


class TestPlanOutputSchemas:
    """Physical plan-output schemas let consumers validate plan artifacts
    without this codebase."""

    def test_generated_avro_round_trips_a_plan_payload(self):
        import io
        import json

        import fastavro

        from fl_op.contracts.plan_schema_gen import generate_plan_avro

        schema = fastavro.parse_schema(json.loads(generate_plan_avro()))
        payload = _plan().model_dump(mode="json", by_alias=True)

        buffer = io.BytesIO()
        fastavro.writer(buffer, schema, [payload])
        buffer.seek(0)
        decoded = list(fastavro.reader(buffer))
        assert len(decoded) == 1
        record = decoded[0]
        assert record["plan_id"] == "plan-periodic-abc"
        assert record["score"]["optimization_objective"] == "cost"
        assert record["quality_summary"]["n_findings"] == 0
        assert record["assignments"][0]["task_id"] == "t-1"
        assert record["unassigned_tasks"][0]["reason_code"] == (
            ReasonCode.OPTIMIZATION_TRADEOFF.value
        )
        assert record["material_reservations"][0]["quantity"] == 120.0

    def test_parquet_descriptor_covers_envelope_and_records(self):
        import json

        from fl_op.contracts.plan_schema_gen import generate_plan_parquet

        descriptor = json.loads(generate_plan_parquet())
        assert descriptor["contract"] == "canonical-plan"
        by_name = {f["name"]: f for f in descriptor["fields"]}
        assert by_name["plan_id"]["arrow_type"] == "large_string"
        assert by_name["score"]["arrow_type"].startswith("struct<")
        assert by_name["quality_summary"]["arrow_type"].startswith("struct<")
        assert by_name["assignments"]["arrow_type"].startswith("list<struct<")
        struct_fields = {
            f["name"] for f in by_name["material_reservations"]["struct_fields"]
        }
        assert {"reservation_id", "task_id", "quantity", "status"} <= struct_fields
        corrective_fields = {
            f["name"] for f in by_name["corrective_actions"]["struct_fields"]
        }
        assert {"action", "task_id", "evidence"} <= corrective_fields

    def test_contracts_generate_emits_plan_schema(self, tmp_path):
        from fl_op.contracts.schema_gen import run_generate

        assert run_generate("avro", out_dir=tmp_path)
        assert (tmp_path / "canonical-plan.avsc").exists()
