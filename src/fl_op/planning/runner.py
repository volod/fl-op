"""Compatibility facade for planning CLI command implementations.

The implementation is split across helper modules under ``fl_op.planning``.
This module keeps the historical import path stable for the CLI and tests.
"""

from fl_op.planning.contracts import (
    run_canonical_validate,
    run_contracts_validate,
    run_domain_validate,
)
from fl_op.planning.demo import generate_demo_events, run_demo
from fl_op.planning.demo_summary import print_demo_summary
from fl_op.planning.plans import run_plan_periodic, run_plan_rolling
from fl_op.planning.snapshots import run_snapshot_build

__all__ = [
    "run_canonical_validate",
    "run_domain_validate",
    "run_contracts_validate",
    "run_snapshot_build",
    "run_plan_periodic",
    "run_plan_rolling",
    "generate_demo_events",
    "run_demo",
    "print_demo_summary",
]
