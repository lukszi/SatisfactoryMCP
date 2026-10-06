"""The reference world the fixture-backed tests measure: its save, its node field, its plans.

docs/DEVELOPING.md ("Test suite") says how the projection is re-cut and why the field is a box.
"""

from __future__ import annotations

FIXTURE_SAVE = "Han Solo_280726-230847.sav"
FIXTURE_WORLD = "X2faPVKjX06VaRzClNv5KQ"

#: The node field the planner tests plan over: a bounding box, never a region name.
REFERENCE_FIELD = ("bbox:-649.64,-3140.10,2465.03,-1080.29",)
REFERENCE_SOURCES = list(REFERENCE_FIELD)

#: Maximum power out of the reference field, with every extractor clock on offer.
REFERENCE_MAX_MW_ARGS = {
    "objective": "max_mw",
    "sources": list(REFERENCE_FIELD),
    "exports": ["MW"],
    "extractor_clocks": [1.0, 1.5, 2.0, 2.5],
}

#: The stored arguments of the planner's ``spire-coast-full``, the decoupled three-module plan.
#: Its region selector is the stored plan's own and is kept on purpose.
SPIRE_COAST_FULL = {
    "objective": "max_mw",
    "sources": ["region:Spire Coast"],
    "exports": ["MW", "Plastic", "Rubber"],
    "export_minimums": {"Plastic": 600.0, "Rubber": 250.0},
    "only_free_nodes": False,
    "allow_sinks": True,
    "extractor_clocks": [1.0, 1.5, 2.0, 2.5],
    "machine_cost_mw": 5.0,
    "exclude_recipes": [
        "Turbofuel",
        "Alternate: Compacted Coal",
        "Coal-Powered Generator",
        "Alternate: Recycled Plastic",
        "Alternate: Recycled Rubber",
    ],
    "water_extractors": 64,
}

RIP = "Reinforced Iron Plate"

#: The smallest plan worth storing: five Reinforced Iron Plate a minute on the fewest machines.
FIVE_RIP_ARGS = {"objective": "min_machines", "exports": [RIP], "export_minimums": {RIP: 5}}
