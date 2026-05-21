import click
import pytest

from fl_op.main import INTERRUPTED_EXIT_CODE, _run_cli


def test_keyboard_interrupt_exits_130(capsys):
    @click.command()
    def command():
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc_info:
        _run_cli(command, args=[])

    assert exc_info.value.code == INTERRUPTED_EXIT_CODE
    assert "Interrupted: pipeline stopped" in capsys.readouterr().err
