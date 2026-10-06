"""The region and node generators print an ``--out`` path outside the repository as given.

``Path.relative_to`` raises for such a path, and both generators print it AFTER the table is
written -- the region generator also in its pin refusal, which then ended in a traceback
instead of the refusal and exit code 3.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.support.paths import REPO_ROOT
from tools import gen_region_names, gen_world_resource_nodes

GENERATORS = [gen_region_names, gen_world_resource_nodes]
NAMES = ["gen_region_names", "gen_world_resource_nodes"]


def test_a_table_pinned_to_another_build_outside_the_repository_is_refused(tmp_path, capsys):
    dest = tmp_path / "r.json"
    dest.write_text(json.dumps({"_meta": {"game_version_pinned": "other"}}), encoding="utf-8")

    assert gen_region_names.check_existing_pin(dest, "mine") is False
    out = capsys.readouterr().out
    assert str(dest) in out and "Pass --force" in out


@pytest.mark.parametrize("generator", GENERATORS, ids=NAMES)
def test_a_path_outside_the_repository_is_shown_as_given(generator, tmp_path):
    assert generator._shown(tmp_path / "x") == tmp_path / "x"
    assert generator._shown(Path("x.json")) == Path("x.json")


@pytest.mark.parametrize("generator", GENERATORS, ids=NAMES)
def test_a_path_inside_the_repository_is_shown_relative_to_it(generator):
    assert generator._shown(REPO_ROOT / "data" / "x.json") == Path("data/x.json")
