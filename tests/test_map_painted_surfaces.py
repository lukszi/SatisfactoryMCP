"""The painted layer's game-colour additions: the bake, rock families, crowns over rock, the
Titan trees and the inland water floor.

docs/spatial-and-map.md section 30. Synthetic fixtures throughout: no install, no field.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")

from mapgen.cache import (  # noqa: E402
    DIRECT_CACHE_SIDECAR,
    DIRECT_COVERAGE_NAME,
    DIRECT_FAMILY_NAME,
    DIRECT_Z_NAME,
    cached_family,
    direct_cache_stamp,
)
from mapgen.gamedata import rockfamily  # noqa: E402
from mapgen.gamedata.bake import (  # noqa: E402
    BAKE_NAME,
    STAMP_INNER_M,
    STAMP_OUTER_M,
    bake_cell_origin,
    bake_have,
    demorton,
    fit_layer_table,
    oil_nodes,
    stamp_windows,
)
from mapgen.gamedata.frame import BOUNDS_M, ORIGIN_X_CM, ORIGIN_Y_CM  # noqa: E402
from mapgen.gamedata.paint import (  # noqa: E402
    component_origin,
)
from mapgen.gamedata.sweep import first_override  # noqa: E402
from mapgen.palette.painted import (  # noqa: E402
    PaintedGround,
    bake_table,
    canopy_over_rock,
    painted_colours,
    patch_stamps,
    rock_surface,
    sample_titan,
    srgb_to_linear,
    titan_over,
)
from mapgen.palette.styles import PAINTED_DIGEST, PAINTED_PALETTE, painted_style  # noqa: E402
from mapgen.terrain.rasters import (  # noqa: E402
    TITAN_LEAVES,
    TITAN_TRUNK,
    direct_placements,
    mesh_pass,
    rasterise_direct_band,
    reduce_source,
    titan_class,
)
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS  # noqa: E402
from satisfactory_mcp.domain.maps.axes import INPUT_NAMES  # noqa: E402
from satisfactory_mcp.domain.maps.presets import normalise  # noqa: E402
from satisfactory_mcp.domain.spatial import heightfield as hf  # noqa: E402

# ----------------------------------------------------------------------- the bake


def test_virtual_texture_tiles_are_in_morton_order():
    assert [demorton(k) for k in range(6)] == [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (3, 0)]
    assert demorton(63) == (7, 7)


def test_a_bake_cell_lands_where_the_landscape_component_of_the_same_section_does():
    assert bake_cell_origin(1, 1) == component_origin(508, 508)
    assert bake_cell_origin(2, 0) == (component_origin(1016, 0))
    rgb = np.zeros((2, 2, 3), np.uint8)
    rgb[0, 0] = (1, 1, 0)
    rgb[1, 1] = (90, 80, 70)
    assert bake_have(rgb).tolist() == [[False, False], [False, True]], "black is a hole"


def test_the_refit_recovers_each_layer_s_colour_from_a_mixed_bake():
    rng = np.random.default_rng(3)
    n = 400
    a = rng.uniform(0, 1, (n, n)).astype(np.float32)
    weights = {
        "A": np.round(a * 255).astype(np.uint8),
        "B": np.round((1 - a) * 255).astype(np.uint8),
        "Never": np.zeros((n, n), np.uint8),
    }
    truth = {"A": np.array([0.4, 0.2, 0.1]), "B": np.array([0.05, 0.3, 0.05])}
    linear = (weights["A"][..., None] * truth["A"] + weights["B"][..., None] * truth["B"]) / 255.0
    srgb = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
    bake = np.clip(np.round(srgb * 255), 0, 255).astype(np.uint8)
    table = {"A": [1.0, 1.0, 1.0], "B": [1.0, 1.0, 1.0], "Never": [0.7, 0.7, 0.7]}
    fit, stats = fit_layer_table(weights, table, bake)
    np.testing.assert_allclose(fit["A"], truth["A"], atol=0.01)
    np.testing.assert_allclose(fit["B"], truth["B"], atol=0.01)
    assert fit["Never"] == [0.7, 0.7, 0.7] and stats["kept"] == ["Never"]


def test_the_bake_table_is_only_offered_by_a_store_that_has_a_bake():
    meta = {"albedo_linear": {"layers_bake_fit": {"A": [0.1, 0.2, 0.3]}}, "files": {}}
    assert bake_table(meta) is None
    meta["files"]["bake.rgb.u8.z"] = {}
    np.testing.assert_allclose(bake_table(meta)["A"], [0.1, 0.2, 0.3])


SAND = (200, 180, 140)


def test_only_crude_oil_nodes_carry_a_stamp(tmp_path):
    table = tmp_path / "nodes.json"
    rows = [
        ("BP_ResourceNode_C", "Desc_LiquidOil_C", 26500.0, -194000.0),
        ("BP_FrackingSatellite_C", "Desc_LiquidOil_C", 0.0, 0.0),
        ("BP_ResourceNode_C", "Desc_Sulfur_C", 100.0, 100.0),
    ]
    nodes = [dict(zip(("class", "resource", "x", "y"), row, strict=True)) for row in rows]
    table.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    assert oil_nodes(table).tolist() == [[265.0, -1940.0]]
    assert oil_nodes(tmp_path / "absent.json").shape == (0, 2)
    assert len(oil_nodes()) > 0, "the committed node table has crude oil nodes"


def _stamped(n=64):
    """A sand bake with a dark 8 m stamp on a node at texel (32, 32) and one hole in it."""
    rgb = np.full((n, n, 3), SAND, np.uint8)
    yy, xx = np.mgrid[0:n, 0:n]
    metres = np.hypot(yy - 32, xx - 32)
    rgb[metres <= 8] = (40, 40, 32)
    rgb[30, 30] = 0
    node = np.array([[ORIGIN_X_CM / 100 + 32, ORIGIN_Y_CM / 100 + 32]])
    return rgb, node, metres


def test_the_stamp_window_hands_the_bake_back_between_the_two_radii():
    _rgb, node, _metres = _stamped()
    ((window, keep),) = stamp_windows(node, (64, 64))
    yy, xx = np.mgrid[window]
    metres = np.hypot(yy - 32, xx - 32)
    assert (keep[metres <= STAMP_INNER_M] == 0).all() and (keep[metres >= STAMP_OUTER_M] == 1).all()
    between = (metres > STAMP_INNER_M) & (metres < STAMP_OUTER_M)
    assert ((keep[between] > 0) & (keep[between] < 1)).all()
    assert list(stamp_windows(node + 1000.0, (64, 64))) == [], "a node off the grid is skipped"


def test_a_stamp_takes_the_paint_scaled_to_the_bake_around_it():
    rgb, node, metres = _stamped()
    ok = bake_have(rgb)
    paint = np.broadcast_to(srgb_to_linear(SAND) * np.float32(0.7), (64, 64, 3)).copy()
    replaced = patch_stamps(rgb, ok, paint, node)
    assert (rgb[30, 30] == 0).all(), "a hole stays a hole"
    np.testing.assert_allclose(rgb[ok], np.broadcast_to(SAND, rgb[ok].shape), atol=1)
    assert replaced == int((ok & (metres <= STAMP_INNER_M)).sum())


def test_the_painted_ground_patches_a_stamp_in_its_read_only_bake(tmp_path):
    rgb, node, _metres = _stamped()
    (tmp_path / BAKE_NAME).write_bytes(hf.encode_u8(rgb.reshape(64, -1)))
    paint = np.broadcast_to(srgb_to_linear(SAND), (64, 64, 3)).copy()
    for stamps, dark in ((None, True), (node, False)):
        ground = PaintedGround.__new__(PaintedGround)
        ground.meta = {"files": {BAKE_NAME: {"shape": [64, 64, 3], "kind": "u8"}}}
        ground.palette, ground.source, ground._stamps = {"have_blur_m": 2.0}, {}, stamps
        albedo, _have, _w = ground._bake(tmp_path, paint.copy(), np.ones((64, 64), bool))
        assert (albedo[32, 32].max() < 0.1) == dark
    assert ground.source["bake_stamps_patched"]["nodes"] == 1


# ----------------------------------------------------------------------- crowns


def test_trees_stand_over_rock_only_where_the_rock_is_below_their_crowns():
    g = np.full((1, 3, 3), 0.5, np.float32)
    ground = SimpleNamespace(crown=np.array([[300, 300, hf.NODATA]], np.int16),
                             canopy_rgb=np.array([0.0, 0.2, 0.0], np.float32))  # fmt: skip
    scene = {"z_m": np.array([[10.0, 40.0, 10.0]], np.float32)}
    canopy = np.full((1, 3, 1), 0.8, np.float32)
    rock = np.ones((1, 3, 1), np.float32)
    out = canopy_over_rock(g, canopy, rock, scene, ground, lambda plane: plane.astype(np.float32))
    assert out[0, 0, 1] == pytest.approx(0.5 * 0.2 + 0.2 * 0.8, abs=1e-6), (
        "crown at 30 m, rock at 10"
    )
    assert np.allclose(out[0, 1], 0.5), "a cliff top above the crown hides the tree"
    assert np.allclose(out[0, 2], 0.5), "no tree"
    ground.crown = None
    assert out is not g and canopy_over_rock(g, canopy, rock, scene, ground, None) is g


# ----------------------------------------------------------------------- rock families


class _View:
    def __init__(self, parent=None, imports=(), refs=None):
        self._parent = parent
        self.pkg = SimpleNamespace(imported_packages=list(imports))
        self._refs = refs or {}

    def props(self, _slot):
        return {"Parent": b"P"} if self._parent else {}

    def import_path(self, raw):
        return self._parent if raw == b"P" else self._refs.get(bytes(raw))


def test_a_material_walks_its_parents_to_its_cliff_family(monkeypatch):
    root = rockfamily.ROOT_DIR
    chain = {
        "/Game/X/CliffFlat_03_Forest": _View(parent="/Game/X/CliffFlat_01_Forest"),
        "/Game/X/CliffFlat_01_Forest": _View(parent=root + "Cliff_Forest"),
        "/Game/X/MI_Rocks_Moss": _View(parent="/Game/X/M_Rock"),
        "/Game/X/M_Rock": _View(),
        "/Game/X/SM_Rock": _View(imports=["/Game/X/PhysicalMaterial/PM", "/Game/X/Material/MI_C"]),
    }
    monkeypatch.setattr(rockfamily, "_view", lambda _s, _c, _i, package: chain.get(package))
    cache: dict = {}
    forest = rockfamily.FAMILIES.index("forest")
    assert rockfamily.family_of(None, None, None, "/Game/X/CliffFlat_03_Forest", cache) == forest
    assert rockfamily.family_of(None, None, None, "/Game/X/MI_Rocks_Moss", cache) == 0
    assert rockfamily.family_of(None, None, None, None, cache) == 0
    assert (
        rockfamily.mesh_material(None, None, None, "/Game/X/SM_Rock", {}) == "/Game/X/Material/MI_C"
    )


def test_desert_rock_roots_the_desert_family_and_reads_no_cliff_colour(monkeypatch):
    base = "/Game/FactoryGame/World/Environment/Rock/DesertRock/Material/"
    chain = {
        base + "MI_DesertRock_05_Vista": _View(parent=base + "MI_DesertRock_05"),
        base + "MI_DesertRock_05": _View(parent=base + "MI_DesertRock"),
        base + "MI_DesertRock": _View(parent="/Game/FactoryGame/World/Environment/Rock/M_Rock"),
    }
    monkeypatch.setattr(rockfamily, "_view", lambda _s, _c, _i, package: chain.get(package))
    desert = rockfamily.FAMILIES.index("desert")
    for leaf in ("MI_DesertRock_05_Vista", "MI_DesertRock_05", "MI_DesertRock"):
        assert rockfamily.family_of(None, None, None, base + leaf, {}) == desert
    opened: list[str] = []
    monkeypatch.setattr(rockfamily, "_view", lambda _s, _c, _i, package: opened.append(package))
    sources = rockfamily.family_sources(None, None, None)
    assert "desert" not in sources and "cliff" in sources, "the palette's target colours it"
    assert not any("DesertRock" in package for package in opened)


def test_an_override_wins_over_the_mesh_s_own_material(monkeypatch):
    monkeypatch.setattr(rockfamily, "mesh_material", lambda *a: rockfamily.ROOT_DIR + "Cliff_Sand")
    sweep = {
        "meshes": ["/Game/Rock/A"],
        "placements": np.zeros((3, 11)),
        "placement_materials": np.array([0, -1, 1], np.int32),
        "materials": [rockfamily.ROOT_DIR + "Cliff_Grass", "/Game/Unknown/MI"],
    }
    monkeypatch.setattr(rockfamily, "_view", lambda *a: None)
    codes = rockfamily.placement_families(None, None, None, sweep)
    names = [rockfamily.FAMILIES[c] for c in codes]
    assert names == ["grass", "sand", "none"]


def test_the_first_override_is_the_first_entry_that_names_a_material():
    view = _View(refs={b"\x02\0\0\0": "/Game/M/MI_A"})
    payload = (2).to_bytes(4, "little") + b"\0\0\0\0" + b"\x02\0\0\0"
    assert first_override(view, payload) == "/Game/M/MI_A"
    assert first_override(view, None) is None and first_override(view, b"\0\0\0\0") is None


def test_the_direct_pass_carries_each_placement_s_family_as_its_source():
    verts = np.array([[0, 0, 500], [100, 0, 500], [100, 100, 500], [0, 100, 500]], np.float32)
    tris = np.array([[0, 1, 2], [0, 2, 3]], np.int64)
    geometry = {"/World/Environment/Rock/Slab": (verts, tris)}
    x0, y0 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["y_min_m"] * 100
    sweep = {
        "meshes": ["/World/Environment/Rock/Slab"],
        "owners": ["RockActor_C"],
        "placements": np.array(
            [(0, 0, x0 + 1000, y0 + 1000, 0, 0, 0, 0, 1, 1, 1),
             (0, 0, x0 + 2000, y0 + 1000, 100, 0, 0, 0, 1, 1, 1)], np.float64),
    }  # fmt: skip
    prepared, _ = direct_placements(sweep, geometry, np.array([3, 5], np.uint8))
    assert [entry[8] for entry in prepared] == [3, 5]
    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / 32768
    z, source = rasterise_direct_band(prepared, geometry, x0, y0 + 1000, step, 4, 128, 1, True)
    family = reduce_source(z, source, 4, 128, 1)
    first, second = int(1050 / step), int(2050 / step)
    assert family[0, first] == 3 and family[0, second] == 5 and family[0, 0] == 0


def test_a_sub_sampled_texel_takes_the_family_of_its_highest_sample():
    z = np.array([[1.0, 5.0], [np.nan, 2.0]], np.float32)
    source = np.array([[2, 6], [0, 3]], np.uint16)
    assert reduce_source(z, source, 1, 1, 2).tolist() == [[6]]


def _rock_ground(flat_top=True):
    tint = np.ones((len(rockfamily.FAMILIES), 3), np.float32)
    top = np.zeros_like(tint)
    has = np.zeros(len(rockfamily.FAMILIES), np.float32)
    grass = rockfamily.FAMILIES.index("grass")
    tint[grass] = (0.5, 0.5, 0.5)
    top[grass] = (0.1, 0.2, 0.05)
    has[grass] = 1.0 if flat_top else 0.0
    return SimpleNamespace(
        rock_family=np.full((8, 8), grass, np.uint8), family_tint=tint, family_top=top,
        family_has_top=has, palette={"rock_top": {"up": [0.6, 0.85]}},
    )  # fmt: skip


def test_rock_takes_its_family_tint_and_its_top_layer_on_the_flat():
    rock = np.full((8, 8, 3), 0.4, np.float32)
    flat = {"z_m": np.zeros((8, 8), np.float32), "grid": (slice(0, 8), 0, 8, 0, 8, 0.25)}
    out = rock_surface(rock, flat, _rock_ground())
    np.testing.assert_allclose(out[4, 4], (0.1, 0.2, 0.05), atol=1e-5)
    wall = dict(flat, z_m=np.tile(np.arange(8, dtype=np.float32) * 2.0, (8, 1)))
    out = rock_surface(rock, wall, _rock_ground())
    # A face steeper than the ramp takes the tint and none of the top layer.
    np.testing.assert_allclose(out[4, 4], (0.2, 0.2, 0.2), atol=1e-5)
    none = _rock_ground()
    none.rock_family = None
    assert rock_surface(rock, flat, none) is rock


# ----------------------------------------------------------------------- the Titan trees


def test_titan_meshes_are_classed_trunk_and_leaves():
    base = "/Game/FactoryGame/World/Environment/Foliage/Trees/TitanTree/"
    assert titan_class(base + "SM_TitanTree_01") == TITAN_TRUNK
    assert titan_class(base + "SM_TitanTree_Leaves_02") == TITAN_LEAVES
    assert titan_class(base + "../Uppochner_01/SM_Uppochner_01") == 0


def test_the_coarse_tree_raster_is_read_bilinear_on_the_sheet():
    z = np.zeros((4, 4), np.float32)
    cls = np.zeros((4, 4), np.uint8)
    z[1:3, 1:3], cls[1:3, 1:3] = 3000.0, TITAN_LEAVES
    found = sample_titan((z, cls, 2, 0, 0), (0, 8, 0, 8))
    height, cover, nearest = found
    assert cover[4, 4] == pytest.approx(1.0) and height[4, 4] == pytest.approx(30.0)
    assert 0.0 < cover[2, 4] < 1.0, "the edge is soft"
    assert cover[0, 0] == 0.0 and nearest[4, 4] == TITAN_LEAVES
    assert sample_titan((z, np.zeros_like(cls), 2, 0, 0), (0, 8, 0, 8)) is None


def test_the_titan_trees_follow_the_style_toggle():
    out = np.full((8, 8, 3), 0.3, np.float32)
    z = np.zeros((4, 4), np.float32)
    cls = np.zeros((4, 4), np.uint8)
    z[:, :], cls[:, :] = 3000.0, TITAN_LEAVES
    palette = copy.deepcopy(PAINTED_PALETTE)
    leaves = np.array([0.07, 0.1, 0.03], np.float32)
    ground = SimpleNamespace(palette=palette, titan=(z, cls, 2, 0, 0),
                             titan_rgb={TITAN_LEAVES: leaves, TITAN_TRUNK: leaves})  # fmt: skip
    scene = {"z_m": np.zeros((8, 8), np.float32), "grid": (None, 0, 8, 0, 8, 0.25),
             "ndl_flat": np.float32(np.sin(np.deg2rad(45)))}  # fmt: skip
    drawn = titan_over(out, scene, ground)
    opacity = palette["titan_trees"]["opacity"]
    assert 0 < opacity < 1
    exposure = palette["exposure"] * palette["tone"]["gain"]
    expected = 0.3 * (1 - opacity) + leaves * exposure * opacity
    np.testing.assert_allclose(drawn[4, 4], expected, rtol=0.05)
    palette["titan_trees"]["opacity"] = 0
    assert titan_over(out, scene, ground) is out


def test_switching_the_titan_trees_off_is_a_style_of_its_own():
    on, on_digest = painted_style(False)
    off, off_digest = painted_style(True)
    assert on is PAINTED_PALETTE and on_digest == PAINTED_DIGEST
    assert off["titan_trees"]["opacity"] == 0 and off_digest != PAINTED_DIGEST
    assert PAINTED_PALETTE["titan_trees"]["opacity"] > 0, "the file is not changed"
    assert normalise("render", {})["titan_trees"] is True
    assert normalise("render", {"titan_trees": False})["titan_trees"] is False


def test_a_mesh_raster_is_reused_when_its_stamp_matches(tmp_path, capsys):
    calls = []

    def build():
        calls.append(1)
        return {"items": {}, "shapes": {}}, {"placements": {}}

    maps, source = mesh_pass(tmp_path / "titan.cache", 16, "b1", "titan_trees", build, "t", True)
    assert maps is not None and calls == [1] and "raster" in source["titan_trees"]
    again, source = mesh_pass(tmp_path / "titan.cache", 16, "b1", "titan_trees", build, "t", True)
    assert calls == [1] and "reused" in source["titan_trees"]
    assert again[0].shape == (16, 16)
    del maps, again


# ----------------------------------------------------------------------- caches, inputs


def test_the_direct_cache_carries_the_family_reader_and_its_plane(tmp_path):
    stamp = direct_cache_stamp(8, 1, "build 1")
    assert stamp["families"] == READER_VERSIONS["rock_families"]
    assert READER_VERSIONS["rock_families"] >= 2, "a plane without the desert family is a miss"
    np.zeros((8, 8), np.float32).tofile(tmp_path / DIRECT_Z_NAME)
    np.zeros((8, 8), np.uint8).tofile(tmp_path / DIRECT_COVERAGE_NAME)
    (tmp_path / DIRECT_CACHE_SIDECAR).write_text(json.dumps(stamp), encoding="utf-8")
    assert cached_family(tmp_path, stamp) is None, "no plane was written"
    np.full((8, 8), 3, np.uint8).tofile(tmp_path / DIRECT_FAMILY_NAME)
    plane = cached_family(tmp_path, stamp)
    assert plane is not None and int(plane[0, 0]) == 3
    del plane
    old = {k: v for k, v in stamp.items() if k != "families"}
    (tmp_path / DIRECT_CACHE_SIDECAR).write_text(json.dumps(old), encoding="utf-8")
    assert cached_family(tmp_path, stamp) is None, "a cache from before the families is a miss"


def test_the_new_readers_are_named_for_the_maps_tab():
    for reader in ("rock_families", "titan_trees"):
        assert READER_VERSIONS[reader] >= 1 and reader in INPUT_NAMES


# ----------------------------------------------------------------------- inland water


def _band_ground(floor):
    palette = copy.deepcopy(PAINTED_PALETTE)
    w = palette["water"]
    linear = lambda c: (np.asarray(c, np.float32) / 255) ** 2.2
    return SimpleNamespace(
        palette=palette,
        albedo=[np.full((2, 2), v, np.float32) for v in (0.3, 0.25, 0.2)],
        canopy=np.zeros((2, 2), np.float32), canopy_rgb=np.zeros(3, np.float32),
        rock=[np.zeros((2, 2), np.float32)] * 3, rock_family=None, crown=None, titan=None, carpet=None,
        mesh_rgb={}, seabed_coral=np.zeros(3, np.float32), ramp=(0.0, 1.0, np.linspace(0, 1, 5)),
        water={"k": np.asarray(w["k_per_m"], np.float32), "body": linear(w["body"]),
               "sky": np.zeros(3, np.float32), "deep": linear(w["deep"]),
               "deep_tau_m": np.float32(12.0), "bed": np.float32(0.8),
               "inland_floor": np.float32(floor)},
        opaque_water=[],
    )  # fmt: skip


def _shallow_scene(ocean):
    shape = (2, 2)
    return {
        "z_m": np.zeros(shape, np.float32), "borrow": np.ones(shape, np.float32),
        "ndl": np.full(shape, 0.7, np.float32), "ndl_flat": np.float32(0.7),
        "rock_weight": np.zeros(shape, np.float32),
        "water": {"cover": np.ones(shape, np.float32), "depth_m": np.full(shape, 0.05, np.float32),
                  "ocean": np.full(shape, ocean, np.float32), "edge": np.zeros(shape, np.float32),
                  "above_m": np.full(shape, np.inf, np.float32),
                  "below_m": np.full(shape, np.inf, np.float32)},
    }  # fmt: skip


def test_shallow_inland_water_keeps_a_floor_of_opacity_and_the_sea_does_not():
    ident = lambda plane: plane
    bare = painted_colours(_shallow_scene(0.0), _band_ground(0.0), ident, ident)
    floored = painted_colours(_shallow_scene(0.0), _band_ground(0.35), ident, ident)
    body = painted_colours(_shallow_scene(0.0), _band_ground(1.0), ident, ident)
    sea = painted_colours(_shallow_scene(1.0), _band_ground(0.35), ident, ident)
    gap = np.abs(floored - bare).max()
    assert gap > 10, "an ankle-deep pool still reads as water"
    lo, hi = np.minimum(bare, body) - 1e-3, np.maximum(bare, body) + 1e-3
    assert ((floored >= lo) & (floored <= hi)).all(), "between the bed and the water body"
    np.testing.assert_allclose(sea, bare, atol=1e-3)
