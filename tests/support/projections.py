"""Hand-built save projections: a few machines and the belts between them, nothing else."""

from __future__ import annotations

SMELTER = "Build_SmelterMk1_C_1"
ROD_A = "Build_ConstructorMk1_C_2"
ROD_B = "Build_ConstructorMk1_C_4"
OUTSIDER = "Build_ConstructorMk1_C_3"
BELT = "Build_ConveyorBeltMk1_C_50"
#: The machine set of ``smelter_feeding_two_rods``; ``OUTSIDER`` is belted on from outside it.
INSIDE = (SMELTER, ROD_A, ROD_B)

CONSTRUCTOR = "Build_ConstructorMk1_C_1"


def _machine(name, recipe, x, **extra):
    return {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [x, 0, 0],
        **extra,
    }


def smelter_feeding_two_rods(clock: float = 1.0, paused: bool = False) -> dict:
    """One smelter feeding two rod constructors, plus a machine belted on from outside.

    Balanced on purpose: 30 Iron Ingot/min made and 2 x 15 consumed, so Iron Ingot is an
    internal item rather than a surplus. ``clock`` and ``paused`` apply to ``ROD_A``.
    """
    actors = [SMELTER, ROD_A, ROD_B, OUTSIDER, BELT]
    index = {a: i for i, a in enumerate(actors)}
    return {
        "machines": [
            _machine(SMELTER, "Recipe_IngotIron_C", 0),
            _machine(ROD_A, "Recipe_IronRod_C", 1000, clock=clock, paused=paused),
            _machine(ROD_B, "Recipe_IronRod_C", 2000),
            _machine(OUTSIDER, "Recipe_IronPlate_C", 9000),
        ],
        "extractors": [],
        "generators": [],
        "graph": {
            "actors": actors,
            "roles": [],
            # The outsider is reached only THROUGH a belt, which is the normal shape.
            "material": [
                [index[SMELTER], index[ROD_A], 0, 0],
                [index[SMELTER], index[ROD_B], 0, 0],
                [index[ROD_A], index[BELT], 0, 0],
                [index[BELT], index[OUTSIDER], 0, 0],
            ],
            "power": [],
        },
    }


def smelter_belted_to_constructor(belts: int = 1) -> dict:
    """A smelter belted into a constructor, with the connector roles that orient it.

    More than one belt puts a belt-to-belt segment in the chain, which states no direction
    at either end.
    """
    run = [f"Build_ConveyorBeltMk1_C_{i}" for i in range(1, belts + 1)]
    actors = [SMELTER, *run, CONSTRUCTOR]
    roles = ["Output1", "Input0", "ConveyorAny0", "ConveyorAny1"]
    material = [[0, 1, 0, 2]]
    for i in range(1, belts):
        material.append([i, i + 1, 3, 2])
    material.append([belts, belts + 1, 3, 1])
    return {
        "header": {"save_identifier": "TEST-trace-seeds", "session_name": "t"},
        "machines": [
            {
                "instance": f"L:P.{SMELTER}",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [0, 0, 0],
            },
            {
                "instance": f"L:P.{CONSTRUCTOR}",
                "cls": "Build_ConstructorMk1_C",
                "recipe": "Recipe_IronRod_C",
                "pos": [800, 0, 0],
            },
        ],
        "extractors": [],
        "generators": [],
        "graph": {
            "actors": actors,
            "roles": roles,
            "material": material,
            "power": [],
        },
    }
