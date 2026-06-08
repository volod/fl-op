"""Snapshot reproducibility, hash exclusions, and solver-payload projection."""

import ast
import pathlib
from datetime import datetime, timezone

import pytest

from fl_op.canonical.enums import PlanningMode
from fl_op.io import detect_format, get_codec, locate_source
from fl_op.snapshot import SnapshotBuilder

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


def test_solver_payload_has_all_datasets(builder: SnapshotBuilder, dataset_dir: pathlib.Path) -> None:
    snap = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)
    for key in ("vehicles", "implements", "operators", "fields", "depots", "orders"):
        assert key in snap.solver_payload, key
    assert len(snap.solver_payload["orders"]) == len(snap.tasks)


def _norm(name: str, row: dict, drop_unbound: bool = True) -> dict:
    out = {}
    for k, v in row.items():
        if drop_unbound and k in ("contract_id_ref", "polygon"):
            continue
        if k in ("compatible_operations", "certified_operations"):
            out[k] = ast.literal_eval(v) if isinstance(v, str) else v
        elif k == "deadline":
            out[k] = datetime.fromisoformat(str(v))
        else:
            try:
                out[k] = float(v)
            except (ValueError, TypeError):
                out[k] = v
    return out


def test_golden_rows_match_source(builder: SnapshotBuilder, dataset_dir: pathlib.Path) -> None:
    """Reconstructed solver rows must equal source-loaded rows on every bound field."""
    snap = builder.build(dataset_dir, PlanningMode.PERIODIC, effective_at=_EFFECTIVE)

    codec = get_codec(detect_format(dataset_dir))
    dataset_names = ("vehicles", "implements", "orders", "fields", "depots", "operators")
    for dataset in dataset_names:
        source_rows = {
            list(r.values())[0]: _norm(dataset, r)
            for r in codec.read(locate_source(dataset_dir, f"{dataset}.csv", codec))
        }
        for prow in snap.solver_payload[dataset]:
            rid = list(prow.values())[0]
            crow = source_rows[rid]
            pnorm = _norm(dataset, {k: ("" if v is None else v) for k, v in prow.items()})
            for key, pval in pnorm.items():
                assert pval == crow.get(key), f"{dataset}/{rid}/{key}: {pval!r} != {crow.get(key)!r}"
