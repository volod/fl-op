"""Drone logistics domain smoke coverage."""

import csv
import json
import os
import pathlib
from copy import deepcopy
from datetime import datetime, timezone

from fl_op.canonical.enums import PlanningMode
from fl_op.contracts.registry import FileRegistry
from fl_op.data.domain_generators import GenerationRequest, run_domain_generator
from fl_op.stream.driver import StreamDriver
from fl_op.stream.source import ExecutionEvent
from fl_op.tuning.solver_profile import solver_parameters_for_profile


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

    manifest = json.loads((out_dir / "drone-scenarios.json").read_text())
    assert manifest["coverage_complete"], manifest["missing_scenarios"]
    assert set(manifest["required_scenarios"]) == set(manifest["scenarios"])
    assert all(
        item["status"] == "covered"
        for item in manifest["scenarios"].values()
    )
    events = [
        json.loads(line)
        for line in (out_dir / "scenario-events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert {
        "task.started",
        "order.created",
        "order.cancelled",
        "inventory.adjusted",
        "forecast.updated",
        "asset.unavailable",
        "entity.corrected",
    } <= {event["event_type"] for event in events}
    # Each trigger is stamped with a true arrival time at or after its observed
    # time, so any event-derived series orders by ingestion across restarts.
    for event in events:
        assert event["ingested_at"] >= event["observed_at"]
    metadata = json.loads((out_dir / "metadata.json").read_text())["run_metadata"]
    assert metadata["tuning"]["ugv_share"] == 0.6
    assert metadata["tuning"]["cluster_target_size"] == 36
    assert metadata["tuning"]["lns_time_limit_s"] == 1
    assert metadata["tuning"]["rolling_instability_penalty"] == 1400
    with (out_dir / "ugvs.csv").open(newline="") as fh:
        ugv = next(csv.DictReader(fh))
    assert ugv["energy_resource_type"] == "electricity"
    assert ugv["energy_unit"] == "kWh"
    assert float(ugv["battery_capacity_kwh"]) > 0
    with (out_dir / "prices.csv").open(newline="") as fh:
        price_rows = list(csv.DictReader(fh))
    assert any(
        row["rate_type"] == "electricity" and row["per_unit"] == "kWh"
        for row in price_rows
    )

    snapshot = SnapshotBuilder(registry).build(out_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("drone-logistics")
    assert profile.weatherPolicy.maxWindMs == 11.0
    assert profile.weatherPolicy.maxRainMmPerH == 2.5

    params = solver_parameters_for_profile(profile)
    assert params.cluster_target_size == 36
    assert params.cluster_solve_time_limit_s == 75
    assert params.lns_time_limit_s == 1
    assert params.rolling_change_penalty == 1400

    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)

    kpis = plan.score["drone_logistics_kpis"]
    assert {
        "fill_rate_pct",
        "on_time_rate_pct",
        "delivery_margin_eur",
        "mode_split",
        "ugv_utilization_pct",
        "uav_utilization_pct",
        "support_team_utilization_pct",
        "unassigned_reasons",
        "energy_or_fuel_equivalent_usage",
        "rolling_churn_pct",
        "weather_blocked_uav_tasks",
        "no_fly_exclusion_count",
    } <= set(kpis)
    assert {"UGV", "UAV"} <= set(kpis["mode_split"])
    assert kpis["energy_or_fuel_equivalent_usage"]["electricity_kwh"] > 0
    assert kpis["weather_blocked_uav_tasks"] >= 1
    assert kpis["no_fly_exclusion_count"] >= 1

    # 3D airspace deconfliction places every aerial flight in a corridor and
    # every modelled conflict is either separated or counted as residual.
    airspace = kpis["airspace_deconfliction"]
    assert airspace["n_aerial_flights"] >= 1
    assert airspace["corridors_used"] >= 1
    assert airspace["corridors_used"] <= airspace["corridors_available"]
    # Conflicts are resolved by corridor (vertical) plus temporal separation;
    # the three buckets partition every modelled conflict.
    assert (
        airspace["n_corridor_separated_pairs"]
        + airspace["n_time_separated_pairs"]
        + airspace["n_residual_conflict_pairs"]
        == airspace["n_conflict_pairs"]
    )
    assert (
        airspace["n_deconflicted_pairs"] + airspace["n_residual_conflict_pairs"]
        == airspace["n_conflict_pairs"]
    )
    assert airspace["total_deconfliction_delay_s"] >= 0
    assert all(
        flight["deconfliction_delay_s"] >= 0 for flight in airspace["flights"]
    )
    assert len(airspace["flights"]) == airspace["n_aerial_flights"]

    # Charging-queue scheduling replenishes every used asset's spent energy at
    # its home hub, bounded by per-hub charging-bay capacity, and reports each
    # asset's recharge turnaround (queue wait + charge time).
    charging = kpis["charging_schedule"]
    assert charging["n_charging_sessions"] >= 1
    assert charging["n_hubs_with_charging"] >= 1
    assert charging["total_energy_charged_kwh"] > 0
    assert charging["n_queued_sessions"] >= 0
    assert charging["max_turnaround_s"] >= 0
    assert charging["n_turnaround_at_risk"] >= 0
    for hub_report in charging["hub_utilization"].values():
        assert hub_report["slots"] >= 1
        assert hub_report["n_sessions"] >= 1
    for session in charging["sessions"]:
        assert session["turnaround_s"] >= session["wait_s"]

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


def test_airspace_holds_retime_dispatch_within_deadlines(
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """A single corridor forces temporal holds that re-time dispatch safely."""
    from fl_op.adapters.ortools_periodic import OrToolsPeriodicAdapter
    from fl_op.planning import airspace
    from fl_op.snapshot import SnapshotBuilder

    # One corridor forces same-corridor conflicts into the time dimension.
    monkeypatch.setattr(airspace, "AIRSPACE_CORRIDOR_COUNT", 1)

    registry = FileRegistry(root=pathlib.Path.cwd() / "contracts")
    orig_cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        out_dir = run_domain_generator(
            "drone_logistics",
            GenerationRequest(
                vehicles=16, implements=16, orders=28, depots=2, seed=7, fmt="csv"
            ),
            registry=registry,
        )
        out_dir = (tmp_path / out_dir).resolve()
    finally:
        os.chdir(orig_cwd)

    snapshot = SnapshotBuilder(registry).build(out_dir, PlanningMode.PERIODIC)
    profile = registry.get_profile("drone-logistics")
    plan = OrToolsPeriodicAdapter().plan(snapshot, profile)

    airspace_kpis = plan.score["drone_logistics_kpis"]["airspace_deconfliction"]
    assert airspace_kpis["holds_applied_to_dispatch"] is True
    # With one corridor and several concurrent flights, at least one is held.
    assert airspace_kpis["n_flights_held"] >= 1
    assert airspace_kpis["total_deconfliction_delay_s"] > 0

    # Every held flight's re-timed dispatch still respects its deadline.
    held = {
        flight["task_id"]
        for flight in airspace_kpis["flights"]
        if flight["deconfliction_delay_s"] > 0
    }
    assert held
    tasks = snapshot.task_index()
    for assignment in plan.assignments:
        if assignment.task_id in held:
            deadline = tasks[assignment.task_id].deadline
            if deadline is not None:
                assert assignment.planned_finish <= deadline


def test_drone_logistics_rolling_demo_events_change_plan(
    tmp_path: pathlib.Path,
) -> None:
    """Drone rolling demo events must visibly change a revision."""
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

    effective_at = datetime(2026, 6, 5, 6, 0, tzinfo=timezone.utc)
    sources = SnapshotBuilder(registry).load_sources(out_dir)
    driver = StreamDriver(registry)
    baseline = driver.initial_revision(deepcopy(sources), effective_at=effective_at)
    assert len(baseline.plan.assignments) >= 2

    started_task = baseline.plan.assignments[0].task_id
    unavailable_asset = baseline.plan.assignments[-1].asset_ids[0]
    events = [
        ExecutionEvent(
            "drone-demo-started",
            "task.started",
            effective_at.isoformat(),
            started_task,
            {},
        ),
        ExecutionEvent(
            "drone-demo-asset-outage",
            "asset.unavailable",
            effective_at.isoformat(),
            unavailable_asset,
            {},
        ),
    ]
    result = driver.run(sources, events, effective_at=effective_at, convergence_window_s=0)

    assert len(result.revisions) == 3
    changed = sum(
        revision.plan.score.get("n_changed_after_freeze", 0)
        for revision in result.revisions[1:]
    )
    unassigned_delta = (
        len(result.revisions[-1].plan.unassigned_tasks)
        - len(result.revisions[0].plan.unassigned_tasks)
    )
    assert changed > 0 or unassigned_delta > 0
