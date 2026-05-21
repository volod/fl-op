"""Schedule analysis command implementation."""

from fl_op.solver.analysis.artifacts import load_solve_artifacts
from fl_op.solver.analysis.console import print_analysis
from fl_op.solver.analysis.metrics import build_schedule_stats


def run_analyse(schedule_dir: str) -> None:
    """Read solve artifacts and pretty-print schedule/resource statistics."""
    artifacts = load_solve_artifacts(schedule_dir)
    stats = build_schedule_stats(artifacts)
    print_analysis(artifacts, stats)

