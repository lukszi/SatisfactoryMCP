"""The ``mapgen`` entry point: its command table, and that choosing nothing imports nothing heavy."""

from __future__ import annotations

import os
import subprocess
import sys

from mapgen import cli


def test_commands_are_the_documented_set():
    assert set(cli.COMMANDS) == {
        "heightmap",
        "caves",
        "rocks",
        "renders",
        "artwork",
        "paint",
        "check-fill",
    }


def test_unknown_command_is_refused(capsys):
    assert cli.main(["no-such-command"]) == 2
    assert "unknown command" in capsys.readouterr().out


def test_no_command_prints_usage(capsys):
    assert cli.main([]) == 2
    assert cli.main(["--help"]) == 0
    assert "usage: python -m mapgen" in capsys.readouterr().out


def test_entry_point_stays_stdlib_only():
    code = "import sys, mapgen.cli, mapgen.__main__; print('numpy' in sys.modules)"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    )
    assert out.stdout.strip() == "False"
