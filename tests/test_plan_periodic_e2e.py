"""End-to-end periodic planning: dataset -> snapshot -> adapter -> canonical Plan."""

import pathlib

import pytest

from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
from fl_op.canonical.enums import PlanningMode, PlanStatus, ReasonCode
from fl_op.contracts.registry import FileRegistry
from fl_op.snapshot import SnapshotBuilder


@pytest.fixture(scope="module")
def periodic_plan(dataset_dir: pathlib.Path):
    registry = FileRegistry()
    snapshot = SnapshotBuilder(registry).build(dataset_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("agricultural-custom-services")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)
    return snapshot, plan


def test_plan_has_canonical_envelope(periodic_plan) -> None:
    snapshot, plan = periodic_plan
    assert plan.planning_mode == PlanningMode.PERIODIC
    assert plan.snapshot_id == snapshot.snapshot_id
    assert plan.snapshot_hash == snapshot.snapshot_hash
    assert plan.status == PlanStatus.DRAFT
    assert plan.version_dimensions.optimization_profile_version


def test_every_task_is_assigned_or_explained(periodic_plan) -> None:
    snapshot, plan = periodic_plan
    covered = {a.task_id for a in plan.assignments} | {
        u.task_id for u in plan.unassigned_tasks
    }
    assert covered == {t.task_id for t in snapshot.tasks}


def test_unassigned_tasks_have_normalized_reason_codes(periodic_plan) -> None:
    _, plan = periodic_plan
    for u in plan.unassigned_tasks:
        assert isinstance(u.reason_code, ReasonCode)


def test_assignment_bundle_ids_are_deterministically_reproducible(periodic_plan) -> None:
    snapshot, plan = periodic_plan
    from fl_op.canonical.bundle import compute_bundle_id
    from fl_op.core.constants import MAPPING_VERSION

    # The snapshot materializes a bounded sample of bundles for inspection, but
    # every assignment's bundle id must be reproducible from its assets so any
    # consumer can recompute and cross-reference it (spec 18.4).
    assert snapshot.bundles, "snapshot should materialize operational bundles"
    for a in plan.assignments:
        assert a.bundle_id == compute_bundle_id(a.asset_ids, [], MAPPING_VERSION)


def test_plan_is_immutable(periodic_plan) -> None:
    from pydantic import ValidationError

    _, plan = periodic_plan
    with pytest.raises(ValidationError):
        plan.status = PlanStatus.PUBLISHED
