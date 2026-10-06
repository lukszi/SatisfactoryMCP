"""A hand-built world of factories, shaped around the two failures factory detection fixes.

* a **grown-together base**: everything belt-connected, so material components cannot tell a
  steel site from an iron site;
* an **over-collecting product**: Concrete made in three places, one of which is "the
  concrete setup".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

from satisfactory_mcp.domain.factories.labels import LabelStore
from satisfactory_mcp.domain.factories.select import select_machines

# Two sites 800 m apart, wired into one belt web and one power grid.
STEEL = [f"Build_FoundryMk1_C_{100 + i}" for i in range(4)]
IRON = [f"Build_SmelterMk1_C_{200 + i}" for i in range(4)]
STEEL_CONCRETE = [f"Build_ConstructorMk1_C_{300 + i}" for i in range(3)]
BASE_CONCRETE = ["Build_ConstructorMk1_C_400"]
ORPHAN = "Build_AssemblerMk1_C_500"  # built, wired to nothing at all
POLE = "Build_PowerPole_C_600"
TOWER = "Build_PowerTower_C_700"
OUTPOST = ["Build_SmelterMk1_C_800"]
OUTPOST_POLE = "Build_PowerPole_C_810"


def machine_at(name: str, recipe: str, x: float, y: float) -> dict:
    return {
        "instance": f"Persistence_Level:PersistentLevel.{name}",
        "recipe": recipe,
        "pos": [x, y, 0.0],
    }


def two_site_projection() -> dict:
    """The steel and iron sites, the stray concrete, an orphan, and an outpost by tower."""
    machines = []
    for i, m in enumerate(STEEL):
        machines.append(machine_at(m, "Recipe_IngotSteel_C", -100_000 + i * 1_000, -120_000))
    for i, m in enumerate(IRON):
        machines.append(machine_at(m, "Recipe_IngotIron_C", -20_000 + i * 1_000, -120_000))
    for i, m in enumerate(STEEL_CONCRETE):
        machines.append(machine_at(m, "Recipe_Concrete_C", -100_000 + i * 1_000, -121_000))
    machines.append(machine_at(BASE_CONCRETE[0], "Recipe_Concrete_C", -20_000, -121_000))
    machines.append(machine_at(ORPHAN, "Recipe_Wire_C", -20_000, -122_000))
    machines.append(machine_at(OUTPOST[0], "Recipe_IngotIron_C", 500_000, 500_000))

    actors = [*STEEL, *IRON, *STEEL_CONCRETE, *BASE_CONCRETE, POLE, TOWER, *OUTPOST, OUTPOST_POLE]
    index = {a: i for i, a in enumerate(actors)}
    roles = ["Output1", "Input1"]

    # One belt web spanning both sites: this is what defeats material components.
    chain = [*STEEL, *STEEL_CONCRETE, *IRON, *BASE_CONCRETE]
    material = [[index[a], index[b], 0, 1] for a, b in pairwise(chain)]

    # One pole feeds everything in the base; a tower reaches the outpost's own pole.
    power = [[index[m], index[POLE]] for m in chain]
    power += [[index[POLE], index[TOWER]], [index[TOWER], index[OUTPOST_POLE]]]
    power += [[index[OUTPOST[0]], index[OUTPOST_POLE]]]

    return {
        "header": {"save_identifier": "TESTWORLD", "session_name": "Test"},
        "machines": machines,
        "extractors": [],
        "generators": [],
        "graph": {"actors": actors, "roles": roles, "material": material, "power": power},
    }


def slab_projection() -> dict:
    """Two platforms sharing a footprint at different heights, plus a detached one."""
    tiles = []
    for gx in range(3):
        for gy in range(3):
            tiles.append([0, gx * 800, gy * 800, 0])  # ground floor
            tiles.append([0, gx * 800, gy * 800, 1200])  # upper floor, 12 m up
    for gx in range(2):  # detached, 40 m east
        tiles.append([0, 4000 + gx * 800, 0, 0])
    ramp = [1, 3200, 0, 600]  # bridges nothing on its own
    return {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C", "Build_Ramp_8x4_01_C"],
            "instances": [*tiles, ramp],
        },
        "machines": [
            {
                "instance": "L:P.Build_FoundryMk1_C_1",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [800, 800, 100],
            },
            {
                "instance": "L:P.Build_FoundryMk1_C_2",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [800, 800, 1300],
            },
            {
                "instance": "L:P.Build_SmelterMk1_C_3",
                "recipe": "Recipe_IngotIron_C",
                "pos": [4000, 0, 100],
            },
            {
                "instance": "L:P.Build_SmelterMk1_C_4",
                "recipe": "Recipe_IngotIron_C",
                "pos": [90000, 90000, 0],
            },  # out in a field, on no foundation
        ],
        "extractors": [],
        "generators": [],
    }


@dataclass
class SelectorWorld:
    """The facets ``select_machines`` reads, without a save behind them.

    A facet no term reaches is left None so that reaching it fails loudly.
    """

    graph: object
    game: object = None
    projection: dict = field(default_factory=dict)
    labels: object = None
    structures: object = None
    proposals: object = None
    plans: object = None
    conduit_runs: tuple = ()

    def player_position(self):
        return None


def select_terms(terms, graph, game, projection, store=None, **kw):
    """``select_machines`` over the synthetic world, with an empty label store by default."""
    store = store if store is not None else LabelStore(world_id="TESTWORLD")
    return select_machines(terms, SelectorWorld(graph, game, projection, store), **kw)
