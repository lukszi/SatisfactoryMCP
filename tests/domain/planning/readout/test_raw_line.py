"""The plan's ``raw:`` line says what it takes from the world.

It printed only raw drawn from outside the plan, so a plan whose miners cover the ore read
as taking nothing.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.world.state import WorldState

pytestmark = pytest.mark.integration


def test_the_raw_line_lists_what_the_extractors_draw(use_world, projection, game):
    use_world(lambda: WorldState(projection=projection, game=game))
    out = srv.plan_factory(
        objective="min_machines", exports=["Motor"], export_minimums={"Motor": 5}
    )
    assert "\nraw: Iron Ore=110  Coal=75  Copper Ore=35\n" in out
