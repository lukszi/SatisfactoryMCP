"""Slabs: foundations grouped into platforms, and the table factory_map prints of them."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.select import select_machines
from tests.support.synthetic_factories import (
    slab_projection,
)

pytestmark = pytest.mark.integration


def test_stacked_floors_are_one_structure():
    """A multi-storey factory is one build. Without this the tor factory reads as three
    platforms that merely share a footprint."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    sx = build_structures(slab_projection())
    assert len(sx.slabs) == 2, [s.tiles for s in sx.slabs]
    upper = sx.slab_of["Build_FoundryMk1_C_2"]
    lower = sx.slab_of["Build_FoundryMk1_C_1"]
    assert upper == lower
    assert sx.slabs[upper].storeys > 1


def test_a_detached_platform_stays_detached():
    from satisfactory_mcp.domain.factories.structure import build_structures

    sx = build_structures(slab_projection())
    assert sx.slab_of["Build_SmelterMk1_C_3"] != sx.slab_of["Build_FoundryMk1_C_1"]


def test_ground_built_machines_belong_to_no_slab():
    """Two of the player's twelve factories are built straight on the ground, which is
    why slabs are a candidate signal and never the arbiter."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    sx = build_structures(slab_projection())
    assert "Build_SmelterMk1_C_4" not in sx.slab_of
    assert all("Build_SmelterMk1_C_4" not in g for g in sx.groups())


def test_no_structures_block_degrades_to_empty_rather_than_raising():
    """Projections from schema 6 and earlier have no structures at all."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    sx = build_structures({"machines": []})
    assert sx.slabs == [] and sx.slab_of == {}
    assert sx.groups() == []


def test_walls_bridge_slabs_only_when_chained():
    """Two platforms joined by a run of walls. Asking whether any SINGLE wall touches
    both finds nothing -- the real shape is slab -> wall -> wall -> slab. On the
    reference save that distinction is 0 joins against 4."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    left = [[0, 0, 0, 0], [0, 800, 0, 0]]
    right = [[0, 3200, 0, 0], [0, 4000, 0, 0]]
    walls = [[1, 1400, 0, 0], [1, 2000, 0, 0], [1, 2600, 0, 0]]
    classes = ["Build_Foundation_8x1_01_C", "Build_Wall_8x4_01_C"]
    machines = [
        {"instance": f"L:P.Build_FoundryMk1_C_{i}", "recipe": "Recipe_IngotSteel_C", "pos": p}
        for i, p in enumerate([[0, 0, 100], [4000, 0, 100]])
    ]
    base = {"machines": machines, "extractors": [], "generators": []}

    apart = build_structures(
        {**base, "structures": {"classes": classes, "instances": [*left, *right]}}
    )
    assert len(apart.slabs) == 2

    joined = build_structures(
        {**base, "structures": {"classes": classes, "instances": [*left, *right, *walls]}}
    )
    assert len(joined.slabs) == 1, "a run of walls is a structural connection"
    assert len(set(joined.slab_of.values())) == 1


def test_a_slab_records_the_box_its_tiles_occupy_not_one_invented_from_the_centre():
    """`centre` is the tile MEAN and sits wherever the tiles are dense, so
    `centre +- extent/2` puts corners on an L-shaped platform that do not exist. The
    reference user reconstructed a bare platform's box from nine describe_location
    probes by hand; `bbox` is that box, stored, from the tiles themselves."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    arm_x = [[0, x * 800, 0, 0] for x in range(5)]
    arm_y = [[0, 0, y * 800, 0] for y in range(1, 5)]
    sx = build_structures(
        {
            "structures": {
                "classes": ["Build_Foundation_8x1_01_C"],
                "instances": [*arm_x, *arm_y],
            },
            "machines": [],
            "extractors": [],
            "generators": [],
        }
    )
    slab = sx.slabs[0]
    assert slab.bbox == (0, 0, 3200, 3200)
    # The mean leans into the dense corner; the box does not follow it.
    assert slab.centre[0] < (slab.bbox[0] + slab.bbox[2]) / 2


def test_factory_map_lists_bare_platforms_and_summarises_pads_by_a_stated_threshold(
    game, monkeypatch
):
    """show=slabs listed only slabs CARRYING machines, so a bare 1,901-foundation
    platform -- the most important object in that user's build -- was invisible. Bare
    platforms are now rows with extent, bbox and elevation; helper pads below the
    threshold are one summary line that names the threshold, so the omission is a
    known one."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    platform = [[0, 40000 + (i % 4) * 800, 100000 + (i // 4) * 800, 1600] for i in range(16)]
    pad = [[0, -50000, -50000, 0]]
    carrying = [[0, x * 800, 0, 0] for x in range(3)]
    projection = {
        "header": {"save_identifier": "TEST-bare-slabs", "session_name": "t"},
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [*platform, *pad, *carrying],
        },
        "machines": [
            {
                "instance": "L:P.Build_SmelterMk1_C_1",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [800, 0, 100],
            }
        ],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    monkeypatch.setattr(ftools, "_state", lambda save=None, world=None, as_of=None: st)

    out = ftools.factory_map(show="slabs")
    assert "## bare platforms (no machines): 2, 17 tiles" in out
    # The 16-tile platform is a row: its extent, box and elevation, in metres.
    assert "16\t412,1012\t24x24m\t400,1000..424,1024\t16" in out
    # The single-tile pad is only the summary line, and the threshold is stated.
    assert "plus 1 pad(s) under 12 tiles (1 tiles total)" in out
    assert "-500,-500" not in out


def test_a_bare_platform_answers_a_slab_selector_instead_of_refusing_it(game, monkeypatch):
    """factory_map lists bare platforms by index and the selector that index feeds
    refused exactly that case, so the table added to retire a nine-probe workflow
    dead-ended into an error. A poured platform is a place; the answer is to describe it.
    """
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    platform = [[0, 40000 + (i % 4) * 800, 100000 + (i // 4) * 800, 0] for i in range(16)]
    carrying = [[0, x * 800, 0, 0] for x in range(3)]
    projection = {
        "header": {"save_identifier": "TEST-bare-selector", "session_name": "t"},
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [*platform, *carrying],
        },
        "machines": [
            {
                "instance": "L:P.Build_SmelterMk1_C_1",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [800, 0, 100],
            }
        ],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    assert st.structures.machines_on(0) == [], "slab 0 is the big empty one"
    assert select_machines(["slab:0"], st) == []

    monkeypatch.setattr(ftools, "_state", lambda save=None, world=None, as_of=None: st)
    out = ftools.select_machines(["slab:0"])
    assert "nothing stands on this platform yet" in out
    assert "tiles=16" in out
    # The occupied one still answers the same question with what is standing on it.
    assert "1x Smelter" in ftools.select_machines(["slab:1"])


def test_an_occupied_slab_reports_the_shape_a_bare_one_does(game, monkeypatch):
    """The half of the table you can already build against was the half with no
    footprint: bare platforms got a bounding box, a z span and a storey count, and a
    platform carrying machines got a mean and a width. Both are places on a map."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

    deck = [[0, x * 800, 0, 0] for x in range(4)]
    upper = [[0, x * 800, 0, 400] for x in range(4)]
    projection = {
        "header": {"save_identifier": "TEST-occupied-slab", "session_name": "t"},
        "structures": {"classes": ["Build_Foundation_8x1_01_C"], "instances": [*deck, *upper]},
        "machines": [
            {
                "instance": "L:P.Build_SmelterMk1_C_1",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [800, 0, 100],
            }
        ],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    monkeypatch.setattr(ftools, "_state", lambda save=None, world=None, as_of=None: st)

    out = ftools.factory_map(show="slabs")
    assert "extent\tbbox(m)\tz(m)\tfloors\tlabels" in out
    # One 8-tile pour over two decks: 24 m of box, 4 m of climb, two storeys.
    assert "0,0..24,0\t0..4\t2" in out
    assert "8 tiles" in out, "the census on the heading comes from Structures.summary()"


def test_slab_selector_uses_the_index_factory_map_prints():
    """Slabs are numbered by tile count; groups() is ordered by machine count. Indexing
    into the wrong one silently returns a different platform -- it once re-anchored the
    speedwire factory onto the aluminium site."""
    from satisfactory_mcp.domain.factories.structure import build_structures

    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            # A big platform carrying one machine, and a small one carrying three.
            "instances": [[0, x * 800, 0, 0] for x in range(6)]
            + [[0, 40000 + x * 800, 0, 0] for x in range(2)],
        },
        "machines": [
            {
                "instance": "L:P.Build_FoundryMk1_C_1",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [0, 0, 100],
            },
            *[
                {
                    "instance": f"L:P.Build_SmelterMk1_C_{i}",
                    "recipe": "Recipe_IngotIron_C",
                    "pos": [40000 + i * 100, 0, 100],
                }
                for i in range(3)
            ],
        ],
        "extractors": [],
        "generators": [],
    }
    sx = build_structures(projection)
    big, small = sx.slabs[0], sx.slabs[1]
    assert big.tiles > small.tiles
    # groups() puts the 3-machine platform first, so the two orderings disagree here.
    assert len(sx.groups()[0]) == 3
    assert len(sx.machines_on(0)) == 1, "slab 0 is the one with the most TILES"
