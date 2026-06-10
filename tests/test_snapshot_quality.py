"""Snapshot quality artifacts: bundle diagnostics and missing-dataset findings."""

import pathlib
import shutil

from fl_op.canonical.enums import PlanningMode
from fl_op.core.constants import BUNDLE_GENERATION_CAP
from fl_op.snapshot.builder import SnapshotBuilder


def test_bundle_diagnostics_record_generation_completeness(
    dataset_dir: pathlib.Path,
) -> None:
    snapshot = SnapshotBuilder().build(dataset_dir, PlanningMode.PERIODIC)
    diag = snapshot.bundle_diagnostics
    assert diag.n_prime_movers > 0
    assert diag.n_related_equipment > 0
    assert diag.generation_cap == BUNDLE_GENERATION_CAP
    assert diag.n_generated == len(snapshot.bundles)
    assert diag.truncated == (len(snapshot.bundles) >= BUNDLE_GENERATION_CAP)


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
