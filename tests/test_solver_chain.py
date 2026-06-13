"""Solver-chain orchestration helpers."""

from fl_op.solver.chain import _scored_for_cluster_tasks


def test_scored_for_cluster_tasks_drops_pre_enforcement_candidates() -> None:
    scored = {
        "kept": [(10.0, 0, 0)],
        "material_excluded": [(20.0, 1, 1)],
    }
    clusters = [
        {
            "cluster_id": "c0",
            "task_ids": ["kept"],
        }
    ]

    assert _scored_for_cluster_tasks(scored, clusters) == {
        "kept": [(10.0, 0, 0)]
    }
