"""Recipe 6 and the game-painted style: the ocean shore, the render-only meshes, the paint input.

docs/spatial-and-map.md section 27. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from mapgen.colour import oklab
from mapgen.commands.renders import LAYERS
from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.ground.landscape_albedo import layer_albedo
from mapgen.gamedata.ground.paint_store import META_NAME
from mapgen.gamedata.ground.weightmaps import (
    component_origin,
    place_component_layers,
    weightmap_channels,
)
from mapgen.gamedata.water.bodies import WATER_BODIES_NAME
from mapgen.lighting.hillshade import WATER_SHADE_FLOOR, WATER_SHADE_RANGE
from mapgen.palette import styles
from mapgen.palette.painted.albedo import (
    layer_table,
    load_paint_meta,
    load_water_bodies,
    mix_layers,
    seam_blend,
)
from mapgen.palette.styles import (
    LAYER_STYLES,
    PAINTED_DIGEST,
    PAINTED_PALETTE,
    PALETTE_DIR,
    SHORE_OPTICS,
    TERRAIN_SHORE,
    WATER_DEEP,
    WATER_SHALLOW,
    load_palette,
)
from mapgen.palette.water.shore import (
    OCEAN_LEVEL_M,
    OCEAN_REACH_M,
    add_foam,
    blend_water,
    composite_meshes,
    ocean_reach,
    shore_terms,
    water_composite,
    wet_band,
)
from mapgen.palette.water.surface import WATER_DEPTH_FULL_M, water_over
from mapgen.render.lift import composite_top
from mapgen.terrain.render_meshes import (
    MESH_CORAL,
    MESH_ROCK,
    MESH_SHELL,
    is_render_only_foliage,
    is_render_only_static,
    mesh_class,
)
from mapgen.terrain.sample import sample_plain, taps_footprint, taps_linear
from mapgen.tiles.recipes import RECIPE
from satisfactory_mcp.core.gameassets import provenance, versions
from satisfactory_mcp.domain.spatial import heightfield as hf

LEVEL = OCEAN_LEVEL_M


def _beach(cols=200, slope=0.067, spacing=0.229):
    """A beach rising west to east through the ocean level at column 100."""
    x = (np.arange(cols) - 100) * spacing
    return np.tile((LEVEL + slope * x).astype(np.float32), (6, 1)), spacing


def test_the_coast_is_where_the_surface_crosses_the_ocean_level_in_one_pixel():
    z, spacing = _beach()
    terms = shore_terms(z, spacing)
    cover = terms["cover"][3]
    assert cover[100] == pytest.approx(0.5, abs=0.02)
    assert (np.diff(cover) <= 1e-6).all(), "water thins monotonically up the beach"
    partial = np.flatnonzero((cover > 0.01) & (cover < 0.99))
    assert len(partial) <= 2, "the edge is one pixel wide, not a 13 m smear"
    assert cover[:95].min() == 1.0 and cover[106:].max() == 0.0
    # The depth fade: metres of water, zero on land.
    assert terms["depth_m"][3, 0] == pytest.approx(100 * spacing * 0.067, rel=1e-3)
    assert terms["depth_m"][3, 150] == 0.0


def test_a_steep_shore_is_still_one_pixel_and_a_flat_one_does_not_blow_up():
    steep, spacing = _beach(slope=3.0)
    assert len(np.flatnonzero((shore_terms(steep, spacing)["cover"][3] % 1) > 0)) <= 2
    flat = np.full((4, 8), LEVEL - 0.001, np.float32)
    cover = shore_terms(flat, 0.229)["cover"]
    assert np.isfinite(cover).all() and (cover == 1.0).all()


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        water_raster=lambda: water_dm,
        water_quality_raster=lambda: grades,
        height=water_dm.shape[0],
        width=water_dm.shape[1],
        spacing_cm=100.0,
        height_dm=height_dm,
    )


def test_the_crossing_reaches_only_near_measured_ocean_and_never_a_lake():
    n = 200
    water = np.full((n, n), hf.NODATA, np.int16)
    grades = np.zeros((n, n), np.uint8)
    water[:, :20] = round(LEVEL * hf.DM_PER_M)
    grades[:, :20] = hf.WATER_MEASURED
    water[:, 150:160] = 500
    grades[:, 150:160] = hf.WATER_MEASURED
    reach, meta = ocean_reach(_field(water, grades, np.zeros((n, n), np.int16)))
    assert reach[:, : 20 + int(OCEAN_REACH_M)].all()
    assert not reach[:, 20 + int(OCEAN_REACH_M) + 1 :].any(), "the lake is untouched"
    assert meta["ocean_texels"] == 20 * n
    grades[:, 20:30] = hf.WATER_LEVEL_ONLY
    reach, _ = ocean_reach(_field(water, grades, np.zeros((n, n), np.int16)))
    assert not reach[:, 20:30].any(), "level-only water keeps recipe 5: its raster is the surface"


def test_rivers_and_lakes_keep_recipe_5_exactly():
    rng = np.random.default_rng(1)
    shape = (5, 7)
    land = rng.uniform(0, 255, shape + (3,)).astype(np.float32)
    old_cover = rng.uniform(0, 1, shape).astype(np.float32)
    old_depth = rng.uniform(0, 1, shape).astype(np.float32)
    shade = rng.uniform(0.45, 1.0, shape).astype(np.float32)
    shore = shore_terms(rng.uniform(-30, 10, shape).astype(np.float32), 0.229)
    water = blend_water(
        np.zeros(shape, np.float32), old_cover, old_depth, shore, WATER_DEPTH_FULL_M
    )
    got = water_composite(
        land,
        water,
        shade,
        TERRAIN_SHORE,
        WATER_SHALLOW,
        WATER_DEEP,
        WATER_SHADE_FLOOR,
        WATER_SHADE_RANGE,
    )
    recipe5 = water_over(land, old_depth, old_cover, shade, WATER_SHALLOW, WATER_DEEP)
    np.testing.assert_allclose(got, recipe5, rtol=1e-5, atol=1e-3)


def test_over_the_sea_the_line_shows_and_wet_ground_darkens():
    z, spacing = _beach()
    shape = z.shape
    land = np.full(shape + (3,), 200.0, np.float32)
    water = blend_water(
        np.ones(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
        shore_terms(z, spacing),
        WATER_DEPTH_FULL_M,
    )
    optics = {"clarity_m": 0.45, "edge_alpha": 0.62, "wet_darken": 0.82, "stroke": 0.0}
    rgb = water_composite(
        land,
        water,
        np.ones(shape, np.float32),
        optics,
        WATER_SHALLOW,
        WATER_DEEP,
        0.75,
        0.25,
    )
    assert (rgb[3, 150] == 200.0).all(), "dry beach untouched"
    just_wet = rgb[3, 98]
    assert np.abs(just_wet - 200.0).max() > 30, "a0 keeps the line visible on a flat beach"
    stroked = water_composite(
        land,
        water,
        np.ones(shape, np.float32),
        {**optics, "stroke": 0.22},
        WATER_SHALLOW,
        WATER_DEEP,
        0.75,
        0.25,
    )
    assert (stroked[3, 100] < rgb[3, 100]).all() and (stroked[3, 150] == rgb[3, 150]).all()


def test_the_mesh_layer_only_ever_raises_the_ground():
    z = np.zeros((1, 5), np.float32)
    mesh_z = np.array([[-500.0, 50.0, 300.0, -1745.0, 0.0]], np.float32)
    cls = np.array([[1, 2, 3, 1, 0]], np.uint8)
    level = np.array([[np.nan, np.nan, np.nan, 2.0, np.nan]], np.float32)
    raised, weight, kept = composite_meshes(z, mesh_z, cls, level, composite_top)
    assert (raised >= z - 1e-6).all(), "a mesh may raise the ground and never lower it"
    assert raised[0, 0] == pytest.approx(0.0, abs=0.13) and weight[0, 0] < 0.5
    assert raised[0, 2] == pytest.approx(3.0, abs=0.01) and weight[0, 2] == 1.0
    assert kept[0, 3] == 0, "a coral root deep under the water surface is not drawn"
    assert kept[0, 4] == 0 and raised[0, 4] == pytest.approx(0.0, abs=0.13)


def test_render_only_meshes_are_named_and_classed():
    coral = "/Game/FactoryGame/World/Environment/Foliage/Coral/SM_CoralTree_02"
    shell = "/Game/FactoryGame/World/Environment/UnderWater/Shells/SM_BigShell_01"
    pillar = "/Game/FactoryGame/World/Environment/Rock/Cliff/CliffPillar_03"
    assert is_render_only_static(coral) and is_render_only_static(pillar)
    assert not is_render_only_static("/Game/FactoryGame/World/Environment/Rock/X")
    assert not is_render_only_foliage(".../SM_SeaRock_06"), "already a top mesh"
    assert mesh_class(coral) == MESH_CORAL
    assert mesh_class(shell) == MESH_SHELL
    assert mesh_class(pillar) == MESH_ROCK


# ----------------------------------------------------------------------- the paint input


def test_a_weightmap_decodes_bgra_into_rgba_channel_order():
    raw = np.zeros((128, 128, 4), np.uint8)
    raw[..., 0], raw[..., 1], raw[..., 2], raw[..., 3] = 10, 20, 30, 40  # B G R A
    rgba = weightmap_channels(raw.tobytes())
    assert rgba[0, 0].tolist() == [30, 20, 10, 40]


def test_components_land_on_the_heightfield_grid_and_clip_at_its_edge():
    row, col = component_origin(508, 508)
    assert (row, col) == (3750, 3247), "section origin 508 is world (0, 0)"
    planes: dict = {}
    weight = np.full((128, 128), 255, np.uint8)
    place_component_layers(planes, -10, 90, {"Sand_LayerInfo": weight}, 200)
    place_component_layers(planes, 0, 0, {"LandscapeVisibilityLayerInfo": weight}, 200)
    assert set(planes) == {"Sand_LayerInfo"}, "visibility carries no colour"
    sand = planes["Sand_LayerInfo"]
    assert sand[:118, 90:200].all() and not sand[118:].any() and not sand[:, :90].any()


def test_layer_albedo_is_texture_times_vector_and_the_mix_is_normalised():
    table = layer_albedo(
        {"A": ("t", None), "B": (None, "v"), "C": ("t", "v")},
        {"t": [0.5, 0.4, 0.2]},
        {"v": (0.5, 1.0, 2.0)},
    )
    assert table == {"A": [0.5, 0.4, 0.2], "B": [0.5, 1.0, 2.0], "C": [0.25, 0.4, 0.4]}
    weights = {"A": np.array([[255, 128, 0]], np.uint8), "B": np.array([[255, 128, 0]], np.uint8)}
    rgb, have = mix_layers(
        weights, {k: np.asarray(v, np.float32) for k, v in table.items()}, (1, 3)
    )
    np.testing.assert_allclose(rgb[0, 0], [0.5, 0.7, 1.1], rtol=1e-5)
    np.testing.assert_allclose(rgb[0, 1], rgb[0, 0], rtol=1e-5)
    assert have.tolist() == [[True, True, False]]


def test_wet_sand_is_darker_than_sand_after_the_palette():
    meta = {
        "albedo_linear": {
            "layers": {
                "Sand_LayerInfo": [0.484, 0.345, 0.209],
                "WetSand_LayerInfo": [0.422, 0.378, 0.336],
            }
        }
    }
    table = layer_table(meta, PAINTED_PALETTE)
    lightness = {k: oklab(v)[0] for k, v in table.items()}
    assert lightness["WetSand_LayerInfo"] < lightness["Sand_LayerInfo"]


def test_a_solid_component_edge_is_blended_and_a_smooth_one_is_left_alone():
    rgb = np.zeros((64, 128, 3), np.float32)
    rgb[:, 64:] = 0.5
    palette = {"seam_jump": [0.03, 0.09], "seam_blend_m": 4.0}
    blended, count = seam_blend(rgb, [(0, 0), (0, 64)], 64, palette)
    assert count > 0 and 0.05 < blended[32, 63, 0] < 0.45
    smooth = np.tile(np.linspace(0.1, 0.5, 128, dtype=np.float32)[None, :, None], (64, 1, 3))
    same, none = seam_blend(smooth, [(0, 0), (0, 64)], 64, palette)
    assert none == 0 and np.allclose(same, smooth)


def test_a_store_whose_files_hold_no_json_object_is_no_store_or_refused(tmp_path):
    assert load_paint_meta(tmp_path) is None, "no meta.json"
    (tmp_path / META_NAME).write_text("[1, 2]", encoding="utf-8")
    assert load_paint_meta(tmp_path) is None, "a meta.json that is no object"
    (tmp_path / META_NAME).write_text(json.dumps({"files": {WATER_BODIES_NAME: {}}}), "utf-8")
    meta = load_paint_meta(tmp_path)
    assert meta is not None
    (tmp_path / WATER_BODIES_NAME).write_text("[]", encoding="utf-8")
    with pytest.raises(TypeError, match="holds no JSON object"):
        load_water_bodies(tmp_path, meta)


# ----------------------------------------------------------------------- the style file


def test_the_painted_style_is_a_file_whose_hash_is_its_digest(tmp_path, monkeypatch):
    style = LAYER_STYLES["painted"]
    assert versions.STYLES[style]["layer"] == "painted"
    palette, digest = load_palette(style)
    assert palette["id"] == style and digest == PAINTED_DIGEST
    canonical = json.dumps(palette, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert digest == provenance.sha256_hex(canonical)
    copy = tmp_path / "palettes"
    shutil.copytree(PALETTE_DIR, copy)
    monkeypatch.setattr(styles, "PALETTE_DIR", copy)
    edited = dict(palette, chroma_gain=palette["chroma_gain"] + 0.01)
    (copy / f"{style}.json").write_text(json.dumps(edited, indent=4), encoding="utf-8")
    assert load_palette(style)[1] != digest, "an edit is a different style"
    (copy / f"{style}.json").write_text(json.dumps(palette), encoding="utf-8")
    assert load_palette(style)[1] == digest, "formatting is not"


def test_every_style_names_its_shore_optics_and_the_ocean_level_is_one_constant():
    for layer in LAYERS:
        assert "stroke" in SHORE_OPTICS[layer]
        assert SHORE_OPTICS[layer]["stroke"] == 0.0, "the stroke is off by default"
    assert OCEAN_LEVEL_M == -17.0
    assert versions.RENDER_RECIPES[6]["label"] == "crisp shore"
    assert versions.RENDER_RECIPES[RECIPE]["label"] == "river splines"


def test_a_fresh_pyramid_rename_waits_out_a_brief_lock(tmp_path, monkeypatch):
    from pathlib import Path

    from satisfactory_mcp.core.gameassets import pyramid

    staging, final = tmp_path / "tiles.incoming", tmp_path / "tiles"
    staging.mkdir()
    real, calls = Path.rename, []

    def flaky(self, target):
        calls.append(target)
        if len(calls) < 3:
            raise PermissionError("held by a scanner")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    monkeypatch.setattr(pyramid, "RENAME_PAUSE_S", 0.0)
    pyramid.swap_into_place(staging, final, tmp_path / "tiles.retired")
    assert final.is_dir() and len(calls) == 3


def test_foam_is_a_line_and_the_wet_band_sits_just_above_it():
    z, spacing = _beach(slope=0.01)
    shape = z.shape
    ones, zeros = np.ones(shape, np.float32), np.zeros(shape, np.float32)
    water = blend_water(ones, zeros, zeros, shore_terms(z, spacing), 40.0)
    foam = {"strength": 0.4, "max_depth_m": 0.12, "width_m": 1.0, "white": 1.0}
    grey = np.full(shape + (3,), 0.3, np.float32)
    foamed = add_foam(grey, water, foam, np.float32(1.0))[3, :, 0]
    lit = np.flatnonzero(foamed > 0.301)
    assert 0 < len(lit) <= 6, "a line of about a metre, not the whole flat sandbar"
    banded = wet_band(grey, water, {"m": 3.0, "tint": [0.5, 0.5, 0.5]})[3, :, 0]
    assert banded[101] < 0.3 and banded[100 + int(3.0 / spacing) + 2] == pytest.approx(0.3)


# ------------------------------------------------- the ground on a sheet coarser than the grid


def test_a_pixel_no_wider_than_a_texel_samples_bilinear_and_a_wider_one_its_footprint():
    position = np.array([0.0, 3.7, 10.25, 49.0])
    for width in (0.229, 0.9155, 1.0):
        got, want = taps_footprint(position, width, 50), taps_linear(position, 50)
        assert all(np.array_equal(a, b) for a, b in zip(got, want, strict=True))
    index, weight = taps_footprint(position, 7500 / 2048, 50)
    assert np.allclose(weight.sum(axis=0), 1.0) and index.min() >= 0 and index.max() <= 49
    ramp = np.tile(np.arange(50, dtype=np.float32), (2, 1))
    rows = taps_linear(np.zeros(1), 2)
    got = sample_plain(ramp, (rows, (index, weight)))[0]
    assert np.allclose(got[1:3], position[1:3], atol=0.05), "a slope keeps its value"


def test_a_trail_narrower_than_the_pixel_is_drawn_at_every_phase_not_as_dots():
    width = 7500 / 2048
    trail = np.zeros((1, 400), np.float32)
    trail[0, 200] = 1.0  # a path one texel wide
    rows = taps_linear(np.zeros(1), 1)
    seen = {"point": [], "footprint": []}
    for phase in np.linspace(0.0, width, 9, endpoint=False):
        position = 150.0 + phase + np.arange(30) * width
        seen["point"].append(sample_plain(trail, (rows, taps_linear(position, 400))).sum())
        footprint = taps_footprint(position, width, 400)
        seen["footprint"].append(sample_plain(trail, (rows, footprint)).sum())
    assert np.ptp(seen["point"]) > 0.5, "one sample a pixel: there at one phase, gone at another"
    assert np.allclose(seen["footprint"], 1.0 / width, atol=1e-5)


def test_the_painted_layer_samples_its_ground_over_each_pixel_s_footprint(monkeypatch):
    from mapgen.render import compose, painting

    n = 400  # texels over the frame, 18.75 m each
    spacing_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / n
    height = np.full((n, n), 100, np.int16)
    field = SimpleNamespace(
        height_dm=height, provenance_plane=np.ones((n, n), np.uint8), water_raster=lambda: None,
        water_quality_raster=lambda: None, x0_cm=BOUNDS_M["x_min_m"] * 100 + spacing_cm / 2,
        y0_cm=BOUNDS_M["y_min_m"] * 100 + spacing_cm / 2, spacing_cm=spacing_cm, width=n, height=n,
    )  # fmt: skip
    stripes = np.tile((np.arange(n) % 4 == 0).astype(np.float32), (n, 1))
    seen: dict = {}

    def grab(scene, ground, sample, sample_rock):
        seen.setdefault(len(scene["z_m"][0]), []).append(sample(stripes))
        return np.zeros(scene["z_m"].shape + (3,), np.float32)

    monkeypatch.setattr(painting, "painted_colours", grab)
    ground = SimpleNamespace(rock=[np.zeros((n // 4, n // 4), np.float32)], crowns=None,
                             water_optics=lambda taps, river=None: None)  # fmt: skip
    borrow = (np.broadcast_to(np.int8(0), (8192, 8192)), np.zeros((n, n), np.uint8))
    for size in (n // 4, n):
        compose.render_layer("painted", field, None, 1, borrow, size, False,
                             height_dm=height.astype(np.float32), painted=ground)  # fmt: skip
    coarse, fine = (np.concatenate(seen[size]) for size in (n // 4, n))
    assert np.allclose(coarse[2:-2, 2:-2], 0.25, atol=1e-5), "four texels a pixel: their mean"
    assert set(np.unique(fine[2:-2, 2:-2]).round(4)) <= {0.0, 1.0}, "a texel a pixel: as before"
