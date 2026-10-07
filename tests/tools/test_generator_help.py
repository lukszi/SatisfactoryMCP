"""``--help`` prints and exits before a generator reads or writes anything."""

from __future__ import annotations

import pytest

from tools import gen_resource_nodes


def test_help_does_not_regenerate_the_node_table(monkeypatch, capsys):
    def refuse() -> None:
        raise AssertionError("--help read the world table")

    monkeypatch.setattr(gen_resource_nodes, "load_world_node_table", refuse)
    with pytest.raises(SystemExit) as stopped:
        gen_resource_nodes.main(["--help"])
    assert stopped.value.code == 0
    assert "usage:" in capsys.readouterr().out
