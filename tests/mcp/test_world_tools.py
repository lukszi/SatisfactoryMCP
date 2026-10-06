"""What the world-state tools answer, judged against the frozen fixture.

They read the newest save on the machine, so each test here points them at the committed
projection instead: the numbers below are that world's, and a machine with a different
save in it must not change them.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.select import select_machines
from satisfactory_mcp.interfaces.mcp.tools import world as world_tools

pytestmark = pytest.mark.integration


@pytest.fixture
def tools(state, use_world):
    use_world(state)
    return world_tools


# ------------------------------------------------------- picking up where you left off


def test_the_summary_says_what_the_player_was_last_working_on(tools):
    """mLastActiveSchematic and mLastUsedHardDriveID had no consumer at all, and they are
    the two facts an assistant resuming a session cannot derive from any count: the goal
    the HUB is tracking, and the drive analysed last."""
    out = tools.world_summary()
    assert "working_on=Logistics Mk.5" in out
    assert "last_hard_drive_analysed=36" in out


def test_a_save_naming_neither_prints_neither(state, game, use_world):
    """An empty continuity line is worse than no line: it reads as "nothing in progress"
    where the truth is that the save did not say."""
    from copy import deepcopy

    from satisfactory_mcp.domain.world.state import WorldState

    projection = deepcopy(state.projection)
    projection["progression"].pop("last_active_schematic", None)
    projection["research"].pop("last_used_hard_drive_id", None)
    bare = WorldState(projection=projection, game=game)
    use_world(bare)
    out = world_tools.world_summary()
    assert "working_on" not in out
    assert "last_hard_drive_analysed" not in out


# ------------------------------------------------------------------- sites


def test_a_site_row_carries_a_selector_the_other_tools_accept(tools, state, game):
    """A site had no identifier of any kind, so "plan around that cluster" meant reading a
    coordinate off one tool and inventing a radius for the next. The row now prints the
    selector, and this test hands it to the selector parser rather than eyeballing it.
    """
    out = tools.factory_sites(limit=3)
    assert "x,y,z(m)" in out and "selector" in out
    rows = [line.split("\t") for line in out.splitlines() if "\t" in line][1:]
    biggest = rows[0]
    selector = biggest[5]
    assert selector.startswith("near:") and "@" in selector
    hits = select_machines([selector], state)
    # The site holds 461 machines and a circle is not a cluster, so this is "most of it",
    # not "all of it" -- which is what the row's own note tells the reader to check.
    assert len(hits) > 0.9 * int(biggest[3])


def test_the_centroid_keeps_its_altitude(tools):
    """The z was computed and dropped at the last step, which made a site on a cliff and a
    site at sea level print the same row."""
    rows = [line for line in tools.factory_sites(limit=1).splitlines() if "\t" in line]
    x, y, z = rows[1].split("\t")[2].split(",")
    assert (int(x), int(y), int(z)) == (-674, -1446, 31)


def test_the_recipe_count_says_it_is_the_unlocked_one(tools, state):
    """It said "automatable recipes total" over the unlocked part recipes, well under the
    game's own count."""
    out = tools.unlocked_recipes()
    assert f"{len(state.unlocked_recipes('part'))} automatable recipes unlocked" in out
    assert "total" not in out.splitlines()[1]
