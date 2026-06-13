"""Snapshot quality artifacts: bundle feasibility summary and missing-dataset findings."""

import pathlib
import shutil

from fl_op.canonical.enums import PlanningMode
from fl_op.core.constants import MAPPING_VERSION
from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.snapshot.bundles import iter_bundles


def test_bundle_summary_counts_match_lazy_enumeration(
    dataset_dir: pathlib.Path,
) -> None:
    """The compact summary is exact: it equals the full lazy enumeration."""
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.PERIODIC)
    summary = snapshot.bundle_summary
    assert summary.n_prime_movers > 0
    assert summary.n_related_equipment > 0

    enumerated = list(iter_bundles(snapshot.assets, MAPPING_VERSION))
    assert summary.n_feasible_pairs == len(enumerated)
    assert len({b.bundle_id for b in enumerated}) == len(enumerated)


def test_bundle_summary_operation_counts_match_filtered_enumeration(
    dataset_dir: pathlib.Path,
) -> None:
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.PERIODIC)
    summary = snapshot.bundle_summary
    assert summary.pairs_by_operation, "expected per-operation pair counts"
    for operation, count in summary.pairs_by_operation.items():
        filtered = list(
            iter_bundles(snapshot.assets, MAPPING_VERSION, operation_type=operation)
        )
        assert len(filtered) == count, operation


def test_bundle_summary_exposes_demand_side_scarcity(
    dataset_dir: pathlib.Path,
) -> None:
    """The summary carries the order book's demand per operation and flags
    demanded operations whose feasible-pair supply is below the task count."""
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.PERIODIC)
    summary = snapshot.bundle_summary

    expected: dict[str, int] = {}
    for task in snapshot.tasks:
        if task.operation_type:
            expected[task.operation_type] = expected.get(task.operation_type, 0) + 1
    assert summary.tasks_by_operation == expected
    assert summary.scarce_operations == sorted(
        op
        for op, n_tasks in expected.items()
        if summary.pairs_by_operation.get(op, 0) < n_tasks
    )


def test_lazy_enumeration_filters_by_asset(dataset_dir: pathlib.Path) -> None:
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.PERIODIC)
    some_bundle = next(iter_bundles(snapshot.assets, MAPPING_VERSION))
    anchor = some_bundle.asset_ids[0]
    for bundle in iter_bundles(snapshot.assets, MAPPING_VERSION, asset_id=anchor):
        assert anchor in bundle.asset_ids


def test_missing_source_file_yields_warning_finding(
    dataset_dir: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    partial = tmp_path / "partial-dataset"
    shutil.copytree(dataset_dir, partial)
    for stale in partial.glob("sensors.*"):
        stale.unlink()

    snapshot = SnapshotBuilder().build(partial, PlanningMode.PERIODIC)
    dataset_findings = [
        f for f in snapshot.quality_findings
        if f.rule_id == "dq://dataset/source-file-missing"
    ]
    assert [f.entity_ref for f in dataset_findings] == ["sensors"]
    assert snapshot.quality_summary.n_findings >= 1
