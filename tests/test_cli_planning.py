"""Smoke tests for the contracts/snapshot/plan command wiring."""

import pathlib

from click.testing import CliRunner

from fl_op.canonical.enums import PlanningMode
from fl_op.main import cli
from fl_op.planning.contracts import run_contracts_validate
from fl_op.snapshot.builder import SnapshotBuilder


def test_contracts_validate_command_exits_zero() -> None:
    result = CliRunner().invoke(cli, ["contracts", "validate"])
    assert result.exit_code == 0, result.output


def test_run_contracts_validate_returns_true() -> None:
    assert run_contracts_validate(persist=False) is True


def test_snapshot_build_runner_logic(dataset_dir: pathlib.Path) -> None:
    # Exercise the snapshot stage directly (CLI path guard rejects tmp dirs).
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.ROLLING)
    assert snapshot.planning_mode == PlanningMode.ROLLING
    assert snapshot.snapshot_hash
    assert snapshot.tasks
