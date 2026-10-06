"""Tree shadows only where trees are drawn, and soft low-sun shadows that keep the relief.

docs/spatial-and-map.md section 29 and the mapgen README's "Tree shadows". Synthetic
fixtures throughout.
"""

from __future__ import annotations

import inspect
import json
import math

import numpy as np
import pytest

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.lighting import horizon as hz
from mapgen.lighting import model
from mapgen.lighting.occluders import sheet_crowns
from mapgen.palette.lightparams import shader_light
from satisfactory_mcp.domain.spatial import heightfield as hf
from tests.support.paths import REPO_ROOT

LITLAYER_TS = REPO_ROOT / "src/satisfactory_mcp/interfaces/web/frontend/src/map/litlayer.ts"


def _flat_nrm(shape, nx=0.0):
    q = lambda v: np.uint8(round((v * 0.5 + 0.5) * 255))
    out = np.empty((*shape, 4), np.uint8)
    out[...] = (q(nx), q(0.0), 255, 255)
    return out


def _crowned_bake(tmp_path, with_crown=True):
    from mapgen.lighting.stage import Surface, bake_light

    size = 512
    surface = Surface(tmp_path / "work", size)
    for top in range(0, size, 128):
        surface.put(top, np.zeros((128, size), np.float32), np.ones((128, size), np.float32))
    crown = np.full((size, size), np.nan, np.float32)
    crown[250:262, 250:262] = 60.0
    cover = np.where(np.isfinite(crown), 255, 0).astype(np.uint8)
    occluder = (crown, cover) if with_crown else None
    meta = bake_light(surface, tmp_path / "out", 1, occluder, progress=False,
                      occluder_layers=["painted"])  # fmt: skip
    return meta, tmp_path / "out" / "light" / "tiles", surface


def test_the_crowns_cast_into_their_own_cells_and_never_into_the_ground_s(tmp_path):
    from PIL import Image

    meta, tiles, _surface = _crowned_bake(tmp_path)
    assert meta["light"]["occluder_layers"] == ["painted"]
    atlas = np.asarray(Image.open(tiles / "1" / "1_1.hz.webp").convert("L"))
    cells = atlas.reshape(8, 128, 8, 128).transpose(0, 2, 1, 3).reshape(64, 128, 128)
    degrees = hz.decode_horizon(cells)
    assert float(degrees[: hz.HORIZON_DIRS].max()) < 2.0, "flat ground has no ground horizon"
    assert float(degrees[hz.HORIZON_DIRS :].max()) > 30.0, "the crown shades beside it"
    without, _tiles, _s = _crowned_bake(tmp_path / "bare", with_crown=False)
    assert without["light"]["occluder_layers"] == []


def test_only_a_style_that_draws_the_crowns_is_shaded_by_them():
    from mapgen.tiles.lit import crown_layers

    assert crown_layers() == ["painted"]
    assert shader_light("painted")["crowns"] and not shader_light("satellite")["crowns"]
    colour = np.full((8, 8, 3), 150, np.uint8)
    cells = np.zeros((model.HZ_CELLS, 8, 8), np.float32)
    cells[hz.HORIZON_DIRS :] = 60.0
    hz_u8 = hz.encode_horizon(cells)
    low = (225.0, 20.0)
    for layer in ("terrain", "satellite", "relief"):
        lit = model.relight(colour, _flat_nrm((8, 8)), hz_u8, low, shader_light(layer))
        bare = model.relight(colour, _flat_nrm((8, 8)), None, low, shader_light(layer), False)
        np.testing.assert_array_equal(lit, bare)
    painted = shader_light("painted")
    shaded = model.relight(colour, _flat_nrm((8, 8)), hz_u8, low, painted)
    assert shaded.mean() < model.relight(colour, _flat_nrm((8, 8)), None, low, painted).mean() - 10


def test_the_baked_copy_takes_the_crown_term_only_for_a_crown_style(tmp_path):
    from mapgen.lighting.stage import Surface
    from mapgen.tiles.lit import relight_in_place

    surface = Surface(tmp_path, 4)
    surface.land[:] = 255
    terms = np.zeros((4, 4, 3), np.uint8)
    terms[..., 0], terms[..., 1], terms[..., 2] = 255, 127, 20
    np.save(surface.path("terms"), terms)
    sheets = {layer: np.full((4, 4, 3), 140, np.uint8) for layer in ("terrain", "painted")}
    for layer, sheet in sheets.items():
        relight_in_place(sheet, surface, shader_light(layer))
    assert np.abs(sheets["terrain"].astype(int) - 140).max() <= 1
    assert sheets["painted"].max() < 120
    surface.close()


def _ground_cells(row: np.ndarray) -> np.ndarray:
    """One row of horizons, the same toward every direction: ``(HORIZON_DIRS, 1, n)``."""
    return np.repeat(np.asarray(row, np.float32)[None, None, :], hz.HORIZON_DIRS, axis=0)


def test_a_shadow_keeps_the_relief_under_it_and_its_edge_is_soft():
    sun = (270.0, 20.0)
    shadowed = _ground_cells([60.0])
    lit_side = model.direct_term(_flat_nrm((1, 1), -0.15), shadowed, sun)[0, 0]
    dark_side = model.direct_term(_flat_nrm((1, 1), 0.15), shadowed, sun)[0, 0]
    assert lit_side > dark_side > 0.0, "a slope facing the sun stays lighter inside a shadow"
    ramp = np.linspace(0.0, 40.0, 81, dtype=np.float32)
    direct = model.direct_term(_flat_nrm((1, 81)), _ground_cells(ramp), sun)[0]
    partial = (direct > direct.min() + 1e-3) & (direct < direct.max() - 1e-3)
    assert partial.sum() * 0.5 == pytest.approx(model.SHADOW_SOFT_DEG, abs=1.0)


def test_a_taller_blocker_throws_a_wider_penumbra_on_the_ground():
    sp = 2.0
    halo = hz.horizon_reach_px(sp)

    def penumbra_m(height):
        z = np.zeros((2 * halo + 4, 2 * halo + 200), np.float32)
        z[:, : halo + 40] = height
        row = hz.march_horizon(z, halo, 270.0, sp)[2]
        direct = model.direct_term(_flat_nrm((1, row.size)), _ground_cells(row), (270.0, 25.0))[0]
        lo, hi = direct.min(), direct.max()
        soft = (direct > lo + 0.05 * (hi - lo)) & (direct < hi - 0.05 * (hi - lo))
        return float(soft.sum() * sp)

    assert penumbra_m(30.0) > penumbra_m(8.0) > 0.0


def test_sheet_crowns_keeps_a_crown_s_area_and_its_mean_height():
    grid = {"x0_cm": BOUNDS_M["x_min_m"] * 100.0, "y0_cm": BOUNDS_M["y_min_m"] * 100.0,
            "spacing_cm": 100.0}  # fmt: skip
    top = np.full((96, 96), hf.NODATA, np.int16)
    yy, xx = np.mgrid[0:96, 0:96]
    disc = (yy - 48) ** 2 + (xx - 48) ** 2 <= 10**2
    top[disc] = 250
    size = int((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / 8.0)
    out = np.empty((size, size), np.float32)
    cover = np.zeros((size, size), np.uint8)

    sheet_crowns(top, grid, size, out, cover)

    step = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    area = cover.astype(np.float64).sum() / 255.0 * step * step
    assert area == pytest.approx(disc.sum(), rel=0.15), "no max filter: the footprint keeps"
    seen = cover > 0
    assert 0 < int((cover[seen] < 255).sum()) and np.nanmax(out) == pytest.approx(25.0, abs=0.01)
    assert np.isnan(out[~seen]).all()


def test_the_shader_and_the_python_model_read_the_same_constants():
    source = LITLAYER_TS.read_text(encoding="utf-8")
    block = model.model_block()
    for key in ("dirs", "normalise_min_el", "shadow_soft_deg", "shadow_fill", "shadow_floor",
                "shadow_floor_knee", "hz_cells", "crown_cell"):  # fmt: skip
        assert key in block, key
        assert f"m.{key}" in source or f"light.model.{key}" in source, key
    assert "1.0-sh*(1.0-uFill)" in source and "p.crowns" in source
    assert "1 - shade * (1 - SHADOW_FILL)" in inspect.getsource(model.direct_term)
    assert block["hz_cells"] == 2 * block["crown_cell"] == 2 * hz.HORIZON_DIRS
    assert json.loads(json.dumps(block)) == block
    assert math.isclose(block["shadow_soft_deg"], model.SHADOW_SOFT_DEG)
