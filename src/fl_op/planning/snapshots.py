"""Snapshot-build planning command implementation."""

import logging
import pathlib
from datetime import datetime
from typing import Optional

from fl_op.canonical.enums import PlanningMode
from fl_op.core.constants import ARTIFACT_SCHEMA_VERSION
from fl_op.core.paths import DATA_ROOT
from fl_op.planning.artifacts import model_json, run_timestamp, write_json
from fl_op.snapshot.builder import SnapshotBuilder

logger = logging.getLogger(__name__)


def run_snapshot_build(
    data_dir: str,
    mode: str = "periodic",
    effective_at: Optional[str] = None,
) -> pathlib.Path:
    """Build an immutable planning snapshot and write it under .data/snapshot/."""
    planning_mode = PlanningMode(mode)
    eff = datetime.fromisoformat(effective_at) if effective_at else None
    snapshot = SnapshotBuilder().build(data_dir, planning_mode, eff)

    out_dir = DATA_ROOT / "snapshot" / run_timestamp()
    write_json(
        {"schema_version": ARTIFACT_SCHEMA_VERSION, **model_json(snapshot)},
        out_dir / "snapshot.json",
    )
    logger.info(
        "Snapshot %s (hash %s) -> %s",
        snapshot.snapshot_id,
        snapshot.snapshot_hash[:12],
        out_dir,
    )
    return out_dir
