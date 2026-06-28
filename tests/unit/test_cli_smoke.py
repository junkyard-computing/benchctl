"""M0 smoke: the CLI is importable, reports its version, and exits cleanly."""

import benchctl
from benchctl.cli import main


def test_version_flag_prints_version_and_exits_zero(capsys):
    rc = main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert benchctl.__version__ in out


def test_help_flag_exits_zero(capsys):
    rc = main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "benchctl" in out


def test_no_args_prints_usage_and_is_nonzero(capsys):
    rc = main([])
    assert rc != 0
