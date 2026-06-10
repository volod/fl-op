"""Snapshot reproducibility, hash exclusions, and solver-payload projection."""

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.canonical.enums import PlanningMode
from fl_op.io import detect_format, get_codec, locate_source
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
from fl_op.solver.types import PrimeMoverRow, TaskRow

_EFFECTIVE = datetime(2026, 6, 5, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def builder() -> SnapshotBuilder:
    return SnapshotBuilder()


def test_snapshot_hash_is_reproducible(builder: SnapshotBuilder, dataset_dir: pathlib.Path) -> None:
    s1 = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    s2 = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    assert s1.snapshot_hash == s2.snapshot_hash
    assert s1.snapshot_hash  # non-empty


def test_hash_independent_of_generated_at_and_payload(
    builder: SnapshotBuilder, dataset_dir: pathlib.Path
) -> None:
    snap = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    content = snap.canonical_content()
    assert "solver_payload" not in content
    assert "generated_at" not in content
    assert "snapshot_id" not in content


def test_solver_inputs_have_all_sections(builder: SnapshotBuilder, dataset_dir: pathlib.Path) -> None:
    snap = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    rows = build_solver_inputs(snap)
    for section in (
        SECTION_PRIME_MOVERS, SECTION_RELATED, SECTION_OPERATORS,
        SECTION_SITES, SECTION_DEPOTS, SECTION_TASKS,
    ):
        assert section in rows, section
    assert len(rows[SECTION_TASKS]) == len(snap.tasks)


def test_projected_rows_use_canonical_keys_and_match_entities(
    builder: SnapshotBuilder, dataset_dir: pathlib.Path
) -> None:
    """Projected solver rows are typed by canonical fields and align 1:1 with entities."""
    snap = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    rows = build_solver_inputs(snap)

    # Task rows are typed TaskRows whose ids match the canonical tasks.
    task_ids_rows = {r.task_id for r in rows[SECTION_TASKS]}
    assert task_ids_rows == {t.task_id for t in snap.tasks}
    for r in rows[SECTION_TASKS]:
        assert isinstance(r, TaskRow)
        # Canonical fields are present; physical column names never leak through.
        assert hasattr(r, "operation_type") and hasattr(r, "revenue")
        assert not hasattr(r, "order_id") and not hasattr(r, "vehicle_id")

    # Prime-mover rows align with mobile-prime-mover assets and expose rated_power.
    prime_ids = {a.asset_id for a in snap.assets if "mobile-prime-mover" in a.roles}
    assert {r.asset_id for r in rows[SECTION_PRIME_MOVERS]} == prime_ids
    for r in rows[SECTION_PRIME_MOVERS]:
        assert isinstance(r, PrimeMoverRow)
        assert hasattr(r, "rated_power")
