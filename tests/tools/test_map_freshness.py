"""The axes rules: stale is outdated DATA; a newer renderer or palette is only an offer.

docs/maps_contract.md §3. Pure functions over dicts, so every rule is one small case.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gameassets import versions
from satisfactory_mcp.domain.maps import axes as ax


def _axes(
    recipe=versions.RENDER_RECIPE_CURRENT,
    hf=5,
    cl=502094,
    digest="sha256:hf",
    style_version=versions.STYLES["terrain-hypsometric"]["version"],
    inputs=None,
):
    return {
        "schema": 1,
        "game": {"cl": cl},
        "inputs": inputs
        if inputs is not None
        else {
            "heightfield": {"cl": cl, "generator_version": hf, "planes": [], "digest": digest},
            "artwork_sheet": {"cl": cl, "reader_version": 1, "digest": "sha256:a"},
        },
        "renderer": {"family": "render", "recipe": recipe, "version": 1,
                     "label": versions.RENDER_RECIPES.get(recipe, {}).get("label", "?"),
                     "size_px": 32768},
        "style": {"id": "terrain-hypsometric", "version": style_version, "label": "terrain"},
        "inferred": False,
    }  # fmt: skip


def _now(game=502094, hf=5, digest="sha256:hf", planes=None):
    files = planes if planes is not None else list(ax.PLANE_FILES.values())
    return {
        "game_cl": game,
        "inputs": {
            "heightfield": {"version": hf, "cl": 502094, "digest": digest, "planes": files},
            "caves": None,
            "rocks": None,
            "paint": None,
        },
        "readers": dict(versions.READER_VERSIONS),
    }


def test_a_current_map_is_neither_stale_nor_offered_anything():
    got = ax.freshness(_axes(), _now())
    assert got == {"stale": [], "rerender": None, "restyle": False, "incomplete": False}


def test_a_newer_game_build_makes_every_map_drawn_from_the_old_one_stale():
    got = ax.freshness(_axes(), _now(game=503001))
    assert got["stale"] == [{"axis": "game", "text": "older game build (502094 → 503001)"}]


def test_a_newer_heightfield_is_stale_data_and_says_which_versions():
    got = ax.freshness(_axes(hf=3), _now(hf=5))
    assert {"axis": "heightfield", "text": "newer heightfield (v3 → v5)"} in got["stale"]


def test_a_changed_digest_is_stale_only_when_both_sides_have_one():
    assert ax.freshness(_axes(digest="sha256:old"), _now())["stale"] == [
        {"axis": "heightfield", "text": "heightfield changed"}
    ]
    missing = ax.freshness(_axes(digest=None), _now())
    assert missing["stale"] == [] and missing["incomplete"] is True
    assert ax.freshness(_axes(), _now(digest=None))["stale"] == []


def test_a_newer_reader_of_an_install_input_is_stale_data(monkeypatch):
    now = _now()
    now["readers"]["artwork_sheet"] = 2
    got = ax.freshness(_axes(), now)
    assert got["stale"] == [
        {"axis": "artwork_sheet", "text": "newer artwork sheet reader (v1 → v2)"}
    ]


def test_a_newer_renderer_is_an_offer_and_never_stale():
    got = ax.freshness(_axes(recipe=4), _now())
    assert got["stale"] == []
    assert got["rerender"] == {
        "recipe": versions.RENDER_RECIPE_CURRENT,
        "label": "river splines r7",
        "needs": [],
        "text": "newer renderer: river splines r7",
    }


def test_the_offer_names_the_input_to_rebuild_first():
    got = ax.freshness(_axes(recipe=3, hf=3), _now(hf=3, planes=["height.i16.z"]))
    assert got["rerender"]["needs"] == ["heightfield"]
    assert got["rerender"]["text"].endswith("after rebuilding the heightfield")


def test_a_newer_palette_is_an_offer(monkeypatch):
    monkeypatch.setitem(
        versions.STYLES,
        "terrain-hypsometric",
        {**versions.STYLES["terrain-hypsometric"], "version": 3},
    )
    got = ax.freshness(_axes(style_version=2), _now())
    assert got["restyle"] is True and got["stale"] == []


def test_a_legacy_render_sidecar_is_read_into_axes_and_flagged_inferred():
    sidecar = {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": "satellite",
            "recipe": 3,
            "sources": {
                "heightfield": {
                    "generator_version": 3,
                    "game_version_pinned": "buildVersion 502094 (engine branch x), the installed build",
                },
                "artwork_detail": {},
                "biome_raster": {},
                "cliff_geometry": {"raster": {}},
            },
            "render": {
                "width_px": 32768,
                "two_regime": {"enabled": True, "subsamples_per_axis": 1},
            },
        }
    }
    got = ax.axes_from_sidecar(sidecar)
    assert got["inferred"] is True
    assert got["style"]["id"] == "satellite-biome"
    assert set(got["inputs"]) == {"heightfield", "artwork_sheet", "biome_raster", "cliff_geometry"}
    assert ax.display_name(got) == "satellite · two-regime r3 · data 502094/hf v3"
    verdict = ax.freshness(got, _now())
    assert [s["axis"] for s in verdict["stale"]] == ["heightfield"]
    assert verdict["rerender"] is None, "nothing draws the satellite style now"
    assert verdict["incomplete"] is True


@pytest.mark.parametrize("style", sorted(versions.RETIRED_STYLES))
def test_a_retired_style_is_stale_as_ever_but_offered_nothing(style):
    retired = {**_axes(recipe=4, hf=3), "style": {"id": style, "version": 1}}
    got = ax.freshness(retired, _now())
    assert [s["axis"] for s in got["stale"]] == ["heightfield"]
    assert got["rerender"] is None and got["restyle"] is False


def test_a_legacy_artwork_sidecar_reads_its_build_and_enhancement():
    sidecar = {
        "_meta": {
            "generator": "tools/gen_map_image.py",
            "sources": {"map_slices": {"game_version_raw": {"Changelist": 502094}}},
            "tiles": {"enhanced": True, "enhancement": {"recipe": 2}},
        }
    }
    got = ax.axes_from_sidecar(sidecar, "artwork")
    assert ax.display_name(got) == "artwork · ESRGAN r2 · data 502094"
    assert ax.freshness(got, _now())["rerender"] is None


def test_older_data_drawn_with_a_newer_renderer_sorts_below_newer_data():
    old_data_new_renderer = _axes(recipe=5, hf=3, cl=500000)
    new_data_old_renderer = _axes(recipe=4, hf=5, cl=502094)
    order = sorted(
        [("a", old_data_new_renderer), ("b", new_data_old_renderer)],
        key=lambda pair: ax.sort_key(pair[1], pair[0]),
    )
    assert [ident for ident, _ in order] == ["b", "a"]


def test_two_types_that_differ_only_by_size_are_told_apart_by_it():
    small = _axes()
    small["renderer"]["size_px"] = 4096
    assert ax.display_name(small, with_size=True).endswith("· 4096 px")


@pytest.mark.parametrize(
    "taken, expected",
    [
        (set(), "terrain-r5-502094"),
        ({"terrain-r5-502094"}, "terrain-r5-502094-2"),
        ({"terrain-r5-502094", "terrain-r5-502094-2"}, "terrain-r5-502094-3"),
    ],
)
def test_ids_are_derived_and_never_collide(taken, expected):
    got = ax.derive_id("terrain", 5, 502094, taken)
    assert got == expected and ax.ID_SHAPE.match(got)
