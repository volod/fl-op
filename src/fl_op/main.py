"""Console-script entry point for fl-op."""

from fl_op.cli import INTERRUPTED_EXIT_CODE, cli, main, run_cli

_run_cli = run_cli

__all__ = ["cli", "main", "run_cli", "_run_cli", "INTERRUPTED_EXIT_CODE"]


if __name__ == "__main__":
    main()
