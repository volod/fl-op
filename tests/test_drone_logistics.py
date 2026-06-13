"""Drone logistics domain smoke coverage."""

import os
import pathlib

from fl_op.canonical.enums import PlanningMode
from fl_op.contracts.registry import FileRegistry
from fl_op.data.domain_generators import GenerationRequest, run_domain_generator


def test_drone_logistics_small_plan_uses_ugv_and_uav(tmp_path: pathlib.Path) -> None:
    """Default drone domain data plans both aerial and ground deliveries."""
    from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
    from fl_op.snapshot import SnapshotBuilder

    registry = FileRegistry(root=pathlib.Path.cwd() / "contracts")
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        out_dir = run_domain_generator(
            "drone_logistics",
            GenerationRequest(
                vehicles=8,
                implements=16,
                orders=12,
                depots=3,
                seed=42,
                fmt="csv",
            ),
            registry=registry,
        )
        assert out_dir is not None
        out_dir = (tmp_path / out_dir).resolve()
    finally:
        os.chdir(orig_cwd)

    snapshot = SnapshotBuilder(registry).build(out_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("drone-logistics")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)

    assignments = plan.assignments
    assert assignments
    task_modes = {
        "UAV" if assignment.task_id.endswith("-UAV") else "UGV"
        for assignment in assignments
    }
    asset_modes = {
        "UAV" if assignment.asset_ids[0].startswith("UAV") else "UGV"
        for assignment in assignments
    }
    assert {"UGV", "UAV"} <= task_modes
    assert task_modes == asset_modes
    for assignment in assignments:
        if assignment.task_id.endswith("-UAV"):
            assert assignment.asset_ids[0].startswith("UAV")
        if assignment.task_id.endswith("-UGV"):
            assert assignment.asset_ids[0].startswith("UGV")
