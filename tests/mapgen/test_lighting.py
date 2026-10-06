"""The live-light bake: the sun path, the light terms, the model and the lighting pyramid.

Synthetic surfaces only; docs/spatial-and-map.md section 29 is the specification, and
tools/mapgen/README.md ("Light") says why the artwork borrow lends light and not ink.
"""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pytest

from mapgen.lighting import horizon as hz
from mapgen.lighting.hillshade import (
    SHADE_FLOOR,
    SHADE_RANGE,
    artwork_detail,
    flat_shade,
    hillshade,
)
from mapgen.lighting.model import (
    SHADOW_FLOOR,
    apply_terms,
    light_axis,
    model_block,
    relight,
)
from mapgen.lighting.sun import DEFAULT_SUN, MAP_NW_SUN, NOON_HOUR, game_sun
from mapgen.palette.lightparams import shader_light
from satisfactory_mcp.core.gameassets.versions import LIGHTS
from tests.support.paths import REPO_ROOT

SUN_TS = REPO_ROOT / "src/satisfactory_mcp/interfaces/web/frontend/src/map/sun.ts"


def test_game_noon_is_the_default_sun_and_the_page_agrees():
    az, el = game_sun(NOON_HOUR)
    assert abs(az - DEFAULT_SUN[0]) < 0.1 and abs(el - DEFAULT_SUN[1]) < 0.05
    morning, afternoon = game_sun(9.0), game_sun(16.0)
    assert 0 < afternoon[1] < el and 0 < morning[1] < el
    source = SUN_TS.read_text(encoding="utf-8")
    assert re.search(rf"NOON_HOUR = {NOON_HOUR};", source)
    assert f"MAP_NW: [number, number] = [{MAP_NW_SUN[0]:g}, {MAP_NW_SUN[1]:g}]" in source
    # The same rotator constants on both sides.
    for triple in ("rotator(0, 45, 25)", "rotator(55, 190, 0)", "-(30 + 15 * hour)"):
        assert triple in source


def _wall(n=400, halo=80, spacing=2.0, height=20.0, col=300):
    z = np.zeros((n, n), np.float32)
    z[:, col:] = height
    return z, halo, spacing


def test_a_wall_shadows_the_ground_by_its_faded_height_and_not_past_the_fade():
    z, halo, sp = _wall()
    east = hz.march_horizon(z, halo, 90.0, sp)
    row = east[100]
    # Core column j is sheet column j + halo; the wall starts at sheet column 300.
    for d_m in (10.0, 20.0, 60.0, 100.0, 140.0, 160.0):
        j = int(300 - d_m / sp) - halo
        expected = math.degrees(math.atan(20.0 * hz.fade_weight(d_m) / d_m))
        assert abs(row[j] - expected) < 1.0, (d_m, row[j], expected)
    assert row[int(300 - 160 / sp) - halo] == 0.0
    west = hz.march_horizon(z, halo, 270.0, sp)
    assert float(west[100, :100].max()) == 0.0


def test_flat_ground_sees_all_the_sky_and_faces_straight_up():
    z = np.full((80, 80), 5.0, np.float32)
    assert np.allclose(hz.sky_view(z, 24, 0.5), 1.0)
    nx, ny = hz.normals(z, 0.5)
    assert np.allclose(nx, 0) and np.allclose(ny, 0)
    assert hz.decode_horizon(hz.encode_horizon(np.array([0.0, 30.0, 90.0])))[1] == pytest.approx(
        30.0, abs=0.5
    )


def test_an_occluder_casts_and_a_floating_slab_casts_only_where_nothing_shows_beneath():
    n, halo, sp = 240, 80, 2.0
    z = np.zeros((n, n), np.float32)
    occluder = np.full((n, n), np.nan, np.float32)
    occluder[90:110, 120:125] = 15.0
    plain = hz.march_horizon(z, halo, 90.0, sp)
    treed = hz.march_horizon(z, halo, 90.0, sp, occluder=occluder)
    assert plain.max() == 0.0 and treed[20, 30] > 10.0
    lo = np.full((n, n), np.nan, np.float32)
    hi = np.full((n, n), np.nan, np.float32)
    lo[90:110, 120:125], hi[90:110, 120:125] = 12.0, 15.0
    arch = hz.march_horizon(z, halo, 90.0, sp, slabs=(z, lo, hi))
    assert arch[20, 30] == 0.0
    lo[90:110, 120:125] = 0.0
    rock = hz.march_horizon(z, halo, 90.0, sp, slabs=(z, lo, hi))
    assert rock[20, 30] > 10.0


def _nrm(z, sp, svf=1.0, land=1.0):
    nx, ny = hz.normals(np.pad(z, 1, mode="edge"), sp)
    q = lambda v: np.round((v * 0.5 + 0.5) * 255).astype(np.uint8)
    full = np.ones(nx.shape)
    return np.stack(
        [q(nx), q(ny), np.uint8(round(svf * 255)) * full, np.uint8(round(land * 255)) * full], -1
    ).astype(np.uint8)


def _hills(n=96, sp=1.0):
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    return (6 * np.sin(xx / 9.0) * np.cos(yy / 13.0)).astype(np.float32), sp


def test_terrain_relit_at_the_old_sun_without_shadow_or_sky_is_the_baked_hillshade():
    z, sp = _hills()
    rng = np.random.default_rng(3)
    ground = rng.uniform(60, 230, (*z.shape, 3)).astype(np.float32)
    shade = hillshade(np.pad(z, 1, mode="edge"), sp)[1:-1, 1:-1]
    baked = np.round(ground * shade[..., None]).astype(np.uint8)
    unlit = np.round(ground * flat_shade(z.shape)[..., None]).astype(np.uint8)
    out = relight(unlit, _nrm(z, sp), None, MAP_NW_SUN, shader_light("terrain"), False, False)
    clear = shade / flat_shade(z.shape) > 0.75  # away from the shadow floor's knee
    diff = np.abs(out.astype(int) - baked.astype(int))[clear]
    assert clear.mean() > 0.5 and diff.max() <= 2


def test_the_shadow_floor_keeps_the_darkest_light_at_036_to_040():
    grey = np.full((4, 4, 3), 200, np.uint8)
    dark = apply_terms(
        grey, np.zeros((4, 4)), np.zeros((4, 4)), np.ones((4, 4)), shader_light("terrain")
    )
    ratio = dark[0, 0, 0] / 200.0
    assert 0.35 <= ratio <= 0.40 and SHADOW_FLOOR <= ratio
    water = apply_terms(
        grey, np.zeros((4, 4)), np.zeros((4, 4)), np.zeros((4, 4)), shader_light("painted")
    )
    assert np.array_equal(water, grey)  # land weight 0: water stays unlit


def test_painted_relit_flat_is_its_unlit_colour_and_low_sun_casts():
    flat = np.zeros((40, 40), np.float32)
    colour = np.full((40, 40, 3), 150, np.uint8)
    lit = relight(
        colour, _nrm(flat, 1.0), None, (225.0, 45.0), shader_light("painted"), False, False
    )
    assert np.abs(lit.astype(int) - 150).max() <= 1
    shadowed = np.full((32, 40, 40), 60.0, np.float32)
    dark = relight(colour, _nrm(flat, 1.0), hz.encode_horizon(shadowed), (225.0, 20.0),
                   shader_light("painted"))  # fmt: skip
    assert dark.mean() < 120


def test_the_light_axis_is_versioned_and_digested():
    axis = light_axis()
    assert axis["id"] in LIGHTS and axis["version"] == LIGHTS[axis["id"]]["version"]
    assert axis["digest"].startswith("sha256:") and axis["default_sun"] == list(DEFAULT_SUN)
    assert model_block()["fade_m"] == list(hz.FADE_M)
    terrain = shader_light("terrain")
    assert terrain["space"] == "srgb"
    flat = SHADE_FLOOR + SHADE_RANGE * math.sin(math.radians(45.0))
    assert terrain["ambient"] == pytest.approx(SHADE_FLOOR / flat, abs=1e-6)
    assert shader_light("painted")["space"] == "linear"


def test_the_stage_and_an_unlit_install_write_what_the_server_serves(tmp_path):
    from PIL import Image

    from mapgen.lighting.stage import Surface, bake_light
    from mapgen.tiles.lit import UNLIT_DIR_NAME, UnlitRun

    size = 512
    run = UnlitRun(tmp_path / "cache", size)
    surface = run.surface_for()
    assert run.surface_for() is None  # only the first layer captures
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    z = (40 * np.exp(-((xx - 256) ** 2 + (yy - 256) ** 2) / 4000.0)).astype(np.float32)
    land = np.ones((size, size), np.float32)
    land[:, :64] = 0.0
    for top in range(0, size, 128):
        surface.put(top, z[top : top + 128], land[top : top + 128])
    sheet = np.full((size, size, 3), 128, np.uint8)
    stats, _dense, _ = run.install(sheet, Image, tmp_path / "out", "terrain", 1, 6, "r")
    root = tmp_path / "out" / "r"
    meta = json.loads((root / "light" / "meta.json").read_text(encoding="utf-8"))["_meta"]
    assert meta["tiles"]["max_z"] == 1 and meta["tiles"]["count"] == 5
    for name in ("0/0_0", "1/0_0", "1/1_1"):
        # An opaque tile is stored without alpha; a browser reads that as land everywhere.
        nrm = np.asarray(Image.open(root / "light" / "tiles" / f"{name}.nrm.webp").convert("RGBA"))
        assert nrm.shape == (256, 256, 4)
        atlas = np.asarray(Image.open(root / "light" / "tiles" / f"{name}.hz.webp"))
        assert atlas.shape[:2] == (1024, 1024)  # 8 x 8 cells: ground, then crown horizons
    nrm = np.asarray(Image.open(root / "light" / "tiles" / "1" / "0_0.nrm.webp").convert("RGBA"))
    assert nrm[200, 20, 3] == 0 and nrm[200, 200, 3] == 255
    coarse = np.asarray(Image.open(root / "light" / "tiles" / "0" / "0_0.nrm.webp").convert("RGBA"))
    assert coarse[100, 10, 3] == 0 and coarse[100, 100, 3] == 255
    assert stats["max_z"] == 1 and (root / "terrain" / UNLIT_DIR_NAME / "0" / "0_0.png").is_file()
    baked = np.asarray(Image.open(root / "terrain" / "tiles" / "1" / "0_0.png"))
    assert baked[20, 20].tolist() == [128, 128, 128]  # water: unlit either way
    sidecar = {"_meta": {"provenance": {}}}
    run.decorate(sidecar, "terrain")
    assert sidecar["_meta"]["light"]["dir"] == "../light"
    assert sidecar["_meta"]["provenance"]["light"]["id"] == "sun"
    run.close()
    assert not (tmp_path / "cache" / "light.cache").exists()
    assert isinstance(surface, Surface) and callable(bake_light)


# ------------------------------------------------- the artwork borrow


def test_the_borrow_drops_thin_ink_and_keeps_broad_shading():
    sheet = np.full((256, 256, 3), 150, np.uint8)
    sheet[:, 60:62] = 30  # a two-pixel dark stroke
    sheet[:, 100:102] = 250  # and a light one
    sheet[100:160, 150:210] = 100  # a broad darker plate

    detail, meta = artwork_detail(sheet)

    assert np.abs(detail[100:160, 56:66].astype(int)).max() < 12
    assert np.abs(detail[100:160, 96:106].astype(int)).max() < 12
    assert detail[130, 152] < -40 and meta["ink_closing_px"] > 2
