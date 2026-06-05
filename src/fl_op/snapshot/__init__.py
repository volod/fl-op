"""Immutable planning-snapshot construction and the solver-payload bridge."""

from fl_op.snapshot.builder import SnapshotBuilder
from fl_op.snapshot.hashing import compute_snapshot_hash
from fl_op.snapshot.payload import to_solver_rows

__all__ = ["SnapshotBuilder", "compute_snapshot_hash", "to_solver_rows"]
