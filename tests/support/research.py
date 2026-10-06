"""The fixture world with a MAM research tree still shut, for the spoiler and gate tests."""

from __future__ import annotations

import copy

from satisfactory_mcp.domain.world.state import WorldState
from tests.support.web import client_over

ALIEN_TECH_TREE = "BPD_ResearchTree_AlienTech_C"


def alien_tree_shut_client(projection, game):
    """A client over the fixture world with the alien-tech research tree still shut."""
    shut = copy.deepcopy(projection)
    trees = shut["research"]["unlocked_trees"]
    shut["research"]["unlocked_trees"] = [t for t in trees if t != ALIEN_TECH_TREE]
    return client_over(WorldState(projection=shut, game=game), game)
