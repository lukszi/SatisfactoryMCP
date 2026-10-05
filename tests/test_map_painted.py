"""Recipe 6 and the game-painted style: the ocean shore, the render-only meshes, the paint input.

docs/spatial-and-map.md section 27. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import json
import shutil
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from satisfactory_mcp.core.gameassets import provenance, versions  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402
from tools import gen_map_renders, gen_paint_layers, map_painted, map_shore  # noqa: E402

LEVEL = map_shore.OCEAN_LEVEL_M


def _beach(cols=200, slope=0.067, spacing=0.229):
    """A beach rising west to east through the ocean level at column 100."""
    x = (np.arange(cols) - 100) * spacing
    return np.tile((LEVEL + slope * x).astype(np.float32), (6, 1)), spacing


def test_the_coast_is_where_the_surface_crosses_the_ocean_level_in_one_pixel():
    z, spacing = _beach()
    terms = map_shore.shore_terms(z, spacing)
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
    assert len(np.flatnonzero((map_shore.shore_terms(steep, spacing)["cover"][3] % 1) > 0)) <= 2
    flat = np.full((4, 8), LEVEL - 0.001, np.float32)
    cover = map_shore.shore_terms(flat, 0.229)["cover"]
    assert np.isfinite(cover).all() and (cover == 1.0).all()


def _field(water_dm, grades, height_dm):
    return SimpleNamespace(
        _water_raster=lambda: water_dm,
        _water_quality_raster=lambda: grades,
        height=water_dm.shape[0],
        width=water_dm.shape[1],
        spacing_cm=100.0,
        _height_dm=height_dm,
    )


def test_the_crossing_reaches_only_near_measured_ocean_and_never_a_lake():
    n = 200
    water = np.full((n, n), hf.NODATA, np.int16)
    grades = np.zeros((n, n), np.uint8)
    water[:, :20] = round(LEVEL * hf.DM_PER_M)
    grades[:, :20] = hf.WATER_MEASURED
    water[:, 150:160] = 500
    grades[:, 150:160] = hf.WATER_MEASURED
    reach, meta = map_shore.ocean_reach(_field(water, grades, np.zeros((n, n), np.int16)))
    assert reach[:, : 20 + int(map_shore.OCEAN_REACH_M)].all()
    assert not reach[:, 20 + int(map_shore.OCEAN_REACH_M) + 1 :].any(), "the lake is untouched"
    assert meta["ocean_texels"] == 20 * n


def test_rivers_and_lakes_keep_recipe_5_exactly():
    rng = np.random.default_rng(1)
    shape = (5, 7)
    land = rng.uniform(0, 255, shape + (3,)).astype(np.float32)
    old_cover = rng.uniform(0, 1, shape).astype(np.float32)
    old_depth = rng.uniform(0, 1, shape).astype(np.float32)
    shade = rng.uniform(0.45, 1.0, shape).astype(np.float32)
    shore = map_shore.shore_terms(rng.uniform(-30, 10, shape).astype(np.float32), 0.229)
    water = map_shore.blend_water(
        np.zeros(shape, np.float32), old_cover, old_depth, shore, gen_map_renders.WATER_DEPTH_FULL_M
    )
    got = map_shore.water_composite(
        land,
        water,
        shade,
        gen_map_renders.TERRAIN_SHORE,
        gen_map_renders.WATER_SHALLOW,
        gen_map_renders.WATER_DEEP,
        gen_map_renders.WATER_SHADE_FLOOR,
        gen_map_renders.WATER_SHADE_RANGE,
    )
    recipe5 = gen_map_renders.water_over(
        land, old_depth, old_cover, shade, gen_map_renders.WATER_SHALLOW, gen_map_renders.WATER_DEEP
    )
    np.testing.assert_allclose(got, recipe5, rtol=1e-5, atol=1e-3)


def test_over_the_sea_the_line_shows_and_wet_ground_darkens():
    z, spacing = _beach()
    shape = z.shape
    land = np.full(shape + (3,), 200.0, np.float32)
    water = map_shore.blend_water(
        np.ones(shape, np.float32),
        np.zeros(shape, np.float32),
        np.zeros(shape, np.float32),
        map_shore.shore_terms(z, spacing),
        gen_map_renders.WATER_DEPTH_FULL_M,
    )
    optics = {"clarity_m": 0.45, "edge_alpha": 0.62, "wet_darken": 0.82, "stroke": 0.0}
    rgb = map_shore.water_composite(
        land,
        water,
        np.ones(shape, np.float32),
        optics,
        gen_map_renders.WATER_SHALLOW,
        gen_map_renders.WATER_DEEP,
        0.75,
        0.25,
    )
    assert (rgb[3, 150] == 200.0).all(), "dry beach untouched"
    just_wet = rgb[3, 98]
    assert np.abs(just_wet - 200.0).max() > 30, "a0 keeps the line visible on a flat beach"
    stroked = map_shore.water_composite(
        land,
        water,
        np.ones(shape, np.float32),
        {**optics, "stroke": 0.22},
        gen_map_renders.WATER_SHALLOW,
        gen_map_renders.WATER_DEEP,
        0.75,
        0.25,
    )
    assert (stroked[3, 100] < rgb[3, 100]).all() and (stroked[3, 150] == rgb[3, 150]).all()


def test_the_mesh_layer_only_ever_raises_the_ground():
    z = np.zeros((1, 5), np.float32)
    mesh_z = np.array([[-500.0, 50.0, 300.0, -1745.0, 0.0]], np.float32)
    cls = np.array([[1, 2, 3, 1, 0]], np.uint8)
    level = np.array([[np.nan, np.nan, np.nan, 2.0, np.nan]], np.float32)
    raised, weight, kept = map_shore.composite_meshes(
        z, mesh_z, cls, level, gen_map_renders.composite_top
    )
    assert (raised >= z - 1e-6).all(), "a mesh may raise the ground and never lower it"
    assert raised[0, 0] == pytest.approx(0.0, abs=0.13) and weight[0, 0] < 0.5
    assert raised[0, 2] == pytest.approx(3.0, abs=0.01) and weight[0, 2] == 1.0
    assert kept[0, 3] == 0, "a coral root deep under the water surface is not drawn"
    assert kept[0, 4] == 0 and raised[0, 4] == pytest.approx(0.0, abs=0.13)


def test_render_only_meshes_are_named_and_classed():
    coral = "/Game/FactoryGame/World/Environment/Foliage/Coral/SM_CoralTree_02"
    shell = "/Game/FactoryGame/World/Environment/UnderWater/Shells/SM_BigShell_01"
    pillar = "/Game/FactoryGame/World/Environment/Rock/Cliff/CliffPillar_03"
    assert map_shore.is_render_only_static(coral) and map_shore.is_render_only_static(pillar)
    assert not map_shore.is_render_only_static("/Game/FactoryGame/World/Environment/Rock/X")
    assert not map_shore.is_render_only_foliage(".../SM_SeaRock_06"), "already a top mesh"
    assert map_shore.mesh_class(coral) == map_shore.MESH_CORAL
    assert map_shore.mesh_class(shell) == map_shore.MESH_SHELL
    assert map_shore.mesh_class(pillar) == map_shore.MESH_ROCK


# ----------------------------------------------------------------------- the paint input


def test_a_weightmap_decodes_bgra_into_rgba_channel_order():
    raw = np.zeros((128, 128, 4), np.uint8)
    raw[..., 0], raw[..., 1], raw[..., 2], raw[..., 3] = 10, 20, 30, 40  # B G R A
    rgba = gen_paint_layers.weightmap_channels(raw.tobytes())
    assert rgba[0, 0].tolist() == [30, 20, 10, 40]


def test_components_land_on_the_heightfield_grid_and_clip_at_its_edge():
    row, col = gen_paint_layers.component_origin(508, 508)
    assert (row, col) == (3750, 3247), "section origin 508 is world (0, 0)"
    planes: dict = {}
    weight = np.full((128, 128), 255, np.uint8)
    gen_paint_layers.place(planes, -10, 90, {"Sand_LayerInfo": weight}, 200)
    gen_paint_layers.place(planes, 0, 0, {"LandscapeVisibilityLayerInfo": weight}, 200)
    assert set(planes) == {"Sand_LayerInfo"}, "visibility carries no colour"
    sand = planes["Sand_LayerInfo"]
    assert sand[:118, 90:200].all() and not sand[118:].any() and not sand[:, :90].any()


def test_layer_albedo_is_texture_times_vector_and_the_mix_is_normalised():
    table = gen_paint_layers.layer_albedo(
        {"A": ("t", None), "B": (None, "v"), "C": ("t", "v")},
        {"t": [0.5, 0.4, 0.2]},
        {"v": (0.5, 1.0, 2.0)},
    )
    assert table == {"A": [0.5, 0.4, 0.2], "B": [0.5, 1.0, 2.0], "C": [0.25, 0.4, 0.4]}
    weights = {"A": np.array([[255, 128, 0]], np.uint8), "B": np.array([[255, 128, 0]], np.uint8)}
    rgb, have = map_painted.mix_layers(
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
    table = map_painted.layer_table(meta, gen_map_renders.PAINTED_PALETTE)
    lightness = {k: map_painted.oklab(v)[0] for k, v in table.items()}
    assert lightness["WetSand_LayerInfo"] < lightness["Sand_LayerInfo"]


def test_a_solid_component_edge_is_blended_and_a_smooth_one_is_left_alone():
    rgb = np.zeros((64, 128, 3), np.float32)
    rgb[:, 64:] = 0.5
    palette = {"seam_jump": [0.03, 0.09], "seam_blend_m": 4.0}
    blended, count = map_painted.seam_blend(rgb, [(0, 0), (0, 64)], 64, palette)
    assert count > 0 and 0.05 < blended[32, 63, 0] < 0.45
    smooth = np.tile(np.linspace(0.1, 0.5, 128, dtype=np.float32)[None, :, None], (64, 1, 3))
    same, none = map_painted.seam_blend(smooth, [(0, 0), (0, 64)], 64, palette)
    assert none == 0 and np.allclose(same, smooth)


# ----------------------------------------------------------------------- the style file


def test_the_painted_style_is_a_file_whose_hash_is_its_digest(tmp_path, monkeypatch):
    style = gen_map_renders.LAYER_STYLES["painted"]
    assert versions.STYLES[style]["layer"] == "painted"
    palette, digest = gen_map_renders.load_palette(style)
    assert palette["id"] == style and digest == gen_map_renders.PAINTED_DIGEST
    canonical = json.dumps(palette, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert digest == provenance.sha256_hex(canonical)
    copy = tmp_path / "palettes"
    shutil.copytree(gen_map_renders.PALETTE_DIR, copy)
    monkeypatch.setattr(gen_map_renders, "PALETTE_DIR", copy)
    edited = dict(palette, chroma_gain=palette["chroma_gain"] + 0.01)
    (copy / f"{style}.json").write_text(json.dumps(edited, indent=4), encoding="utf-8")
    assert gen_map_renders.load_palette(style)[1] != digest, "an edit is a different style"
    (copy / f"{style}.json").write_text(json.dumps(palette), encoding="utf-8")
    assert gen_map_renders.load_palette(style)[1] == digest, "formatting is not"


def test_every_style_names_its_shore_optics_and_the_ocean_level_is_one_constant():
    for layer in gen_map_renders.LAYERS:
        assert "stroke" in gen_map_renders.SHORE_OPTICS[layer]
        assert gen_map_renders.SHORE_OPTICS[layer]["stroke"] == 0.0, "the stroke is off by default"
    assert map_shore.OCEAN_LEVEL_M == -17.0
    assert versions.RENDER_RECIPES[gen_map_renders.RECIPE]["label"] == "crisp shore"


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
