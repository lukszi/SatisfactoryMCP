"""Phase 0a: every map generator writes ``_meta.provenance``, and the server can read it back.

The generators and the server share one table of current versions
(``core.gameassets.versions``); these tests hold the tools to it and read each sidecar's
block back through the same function the registry uses.
"""

from __future__ import annotations

import json

from satisfactory_mcp.core.gameassets import provenance, versions
from satisfactory_mcp.domain.maps import axes as ax
from tools import gen_map_image, gen_map_renders, gen_world_heightmap

GAME_RAW = {
    "Changelist": 502094,
    "BranchName": "++FactoryGame+rel-main-anniversary-2026",
    "GameVersion": "1.2.4.0",
}


def test_the_tools_take_their_versions_from_the_shared_table():
    assert gen_world_heightmap.GENERATOR_VERSION == versions.HEIGHTFIELD_GENERATOR_VERSION
    assert gen_world_heightmap.CAVES_VERSION == versions.CAVES_VERSION
    assert gen_map_renders.RECIPE == versions.RENDER_RECIPE_CURRENT
    assert gen_map_renders.RECIPE_KERNEL_ONLY == versions.RENDER_RECIPE_KERNEL_ONLY
    assert set(gen_map_renders.RECIPES) == set(versions.RENDER_RECIPES)
    assert set(gen_map_image.ENHANCE_RECIPES) == set(versions.ARTWORK_RECIPES)
    assert versions.READER_VERSIONS["cliff_geometry"] == versions.HEIGHTFIELD_GENERATOR_VERSION


def test_palettes_are_files_and_the_digest_is_their_content():
    for layer, style in gen_map_renders.LAYER_STYLES.items():
        assert style in versions.STYLES
        assert versions.STYLES[style]["layer"] == layer
        assert ax.LAYER_STYLE[layer] == style
        palette, digest = gen_map_renders.load_palette(style)
        assert digest.startswith("sha256:") and len(digest) == 71
        assert palette == json.loads(
            (gen_map_renders.PALETTE_DIR / f"{style}.json").read_text(encoding="utf-8")
        )
    # The constants the painters draw with are the file's numbers, not a copy of them.
    assert gen_map_renders.RAMP_STOPS.tolist() == gen_map_renders.TERRAIN_PALETTE["ramp_stops"]
    assert gen_map_renders.BIOME_COLOURS["Area_Swamp"] == tuple(
        gen_map_renders.SATELLITE_PALETTE["biome_colours"]["Area_Swamp"]
    )


def test_a_directory_digest_is_order_free_and_moves_with_any_file():
    one = provenance.files_digest({"a": "sha256:1", "b": "sha256:2"})
    assert one == provenance.files_digest({"b": "sha256:2", "a": "sha256:1"})
    assert one != provenance.files_digest({"a": "sha256:1", "b": "sha256:3"})
    assert provenance.sha256_hex(b"x", b"y") == provenance.sha256_hex(b"xy")


def test_a_build_number_reads_from_either_spelling():
    assert provenance.changelist(GAME_RAW) == 502094
    pin = "buildVersion 502094 (engine branch ++FactoryGame+rel-main), the installed build"
    assert provenance.changelist(pin) == 502094
    assert provenance.changelist("no build here") is None
    assert provenance.changelist({"Changelist": True}) is None


def test_a_render_sidecar_carries_the_block_and_the_registry_reads_it_back():
    block = provenance.provenance_block(
        GAME_RAW,
        {
            "heightfield": {"cl": 502094, "generator_version": 5, "planes": ["height"],
                            "digest": "sha256:aa"},
            "cliff_geometry": {"cl": 502094, "reader_version": 5},
        },
        {"family": "render", "recipe": 5, "version": 1, "label": "PCHIP", "sampler": "pchip",
         "two_regime": True, "size_px": 4096, "subsamples": 1},
        {"id": "terrain-hypsometric", "version": 1, "label": "terrain", "digest": "sha256:bb"},
    )  # fmt: skip
    sidecar = gen_map_renders.build_sidecar(
        layer="terrain",
        field_meta={"generator": "tools/gen_world_heightmap.py", "generator_version": 5},
        tiles={"count": 1},
        render={},
        extra={},
        provenance=block,
    )
    read = ax.axes_from_sidecar(json.loads(json.dumps(sidecar)))
    assert read["inferred"] is False
    assert read["game"] == {
        "cl": 502094,
        "branch": GAME_RAW["BranchName"],
        "game_version": "1.2.4.0",
    }
    assert read["renderer"]["recipe"] == 5 and read["style"]["digest"] == "sha256:bb"
    assert ax.display_name(read) == "terrain · PCHIP r5 · data 502094/hf v5"
    # And the existing keys are untouched: the change is additive.
    assert sidecar["_meta"]["recipe"] == gen_map_renders.RECIPE
    assert "staleness" in sidecar["_meta"]


def test_the_artwork_sidecar_names_its_sheet_and_its_cutting_recipe():
    block = gen_map_image.artwork_provenance(GAME_RAW, "sha256:cc", True, 8192)
    assert block["inputs"]["artwork_sheet"] == {
        "cl": 502094,
        "reader_version": versions.READER_VERSIONS["artwork_sheet"],
        "digest": "sha256:cc",
    }
    assert block["renderer"]["family"] == "artwork"
    assert block["renderer"]["recipe"] == gen_map_image.ENHANCE_RECIPE
    plain = gen_map_image.artwork_provenance(GAME_RAW, "sha256:cc", False, 8192)
    assert plain["renderer"]["recipe"] == 0
    assert ax.display_name({**plain, "inferred": False}) == "artwork · plain · data 502094"
