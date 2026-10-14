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

from mapgen.lighting import horizon as hz
from mapgen.lighting import model
from mapgen.lighting.light_tiles import atlas_cells
from mapgen.palette.lightparams import shader_light
from tests.support.paths import REPO_ROOT

LITLAYER_TS = REPO_ROOT / "src/satisfactory_mcp/interfaces/web/frontend/src/map/litlayer.ts"


def _flat_nrm(shape, nx=0.0):
    q = lambda v: np.uint8(round((v * 0.5 + 0.5) * 255))
    out = np.empty((*shape, 4), np.uint8)
    out[...] = (q(nx), q(0.0), 255, 255)
    return out


def _crowned_bake(tmp_path, with_crown=True):
    from mapgen.lighting.bake import bake_light
    from mapgen.lighting.stage import Surface

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
    degrees = hz.decode_horizon(atlas_cells(atlas))
    assert float(degrees[: hz.HORIZON_DIRS].max()) < 2.0, "flat ground has no ground horizon"
    assert float(degrees[hz.HORIZON_DIRS :].max()) > 30.0, "the crown shades beside it"
    without, _tiles, _s = _crowned_bake(tmp_path / "bare", with_crown=False)
    assert without["light"]["occluder_layers"] == []


def test_only_a_style_that_draws_the_crowns_is_shaded_by_them():
    from mapgen.render.draw.light import crown_layers

    assert crown_layers() == ["painted"]
    assert shader_light("painted")["crowns"] and not shader_light("terrain")["crowns"]
    colour = np.full((8, 8, 3), 150, np.uint8)
    cells = np.zeros((model.HZ_CELLS, 8, 8), np.float32)
    cells[model.CROWN_CELL : model.BAND_CELL] = 70.0  # over the folds: shaded whole
    hz_u8 = hz.encode_horizon(cells)
    low = (225.0, 20.0)
    for layer in ("terrain", "relief-dark"):
        lit = model.relight(colour, _flat_nrm((8, 8)), hz_u8, low, shader_light(layer))
        bare = model.relight(colour, _flat_nrm((8, 8)), None, low, shader_light(layer), False)
        np.testing.assert_array_equal(lit, bare)
    painted = shader_light("painted")
    shaded = model.relight(colour, _flat_nrm((8, 8)), hz_u8, low, painted)
    assert shaded.mean() < model.relight(colour, _flat_nrm((8, 8)), None, low, painted).mean() - 10


def test_the_baked_copy_takes_the_crown_term_only_for_a_crown_style():
    from mapgen.render.draw.light import relight_rows

    land = np.full((4, 4), 255, np.uint8)
    terms = np.zeros((4, 4, 4), np.uint8)
    terms[..., 0], terms[..., 1], terms[..., 2], terms[..., 3] = 255, 127, 20, 255
    colour = np.full((4, 4, 3), 140, np.uint8)
    sheets = {
        layer: relight_rows(colour, terms, land, shader_light(layer))
        for layer in ("terrain", "painted")
    }
    assert np.abs(sheets["terrain"].astype(int) - 140).max() <= 1
    assert sheets["painted"].max() < 120
    assert (colour == 140).all(), "the unlit rows are left as they were"


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


def test_the_shader_and_the_python_model_read_the_same_constants():
    source = LITLAYER_TS.read_text(encoding="utf-8")
    block = model.model_block()
    for key in ("dirs", "normalise_min_el", "shadow_soft_deg", "shadow_fill", "shadow_floor",
                "shadow_floor_knee", "hz_cells", "crown_cell", "hz_gutter", "canopy_cell",
                "canopy_floor_deg", "band_cell"):  # fmt: skip
        assert key in block, key
        assert f"model.{key}" in source, key
    assert "1.0-sh*(1.0-uFill)" in source and "params.crowns" in source
    assert "(cell+uGut+uv*uIn)" in source and "HZ_CELL_PX = 128;" in source, "the cell's border"
    assert block["hz_gutter"] == model.HZ_GUTTER_PX
    assert "1 - shade * (1 - SHADOW_FILL)" in inspect.getsource(model.shaded_direct)
    dirs = hz.HORIZON_DIRS
    assert (block["crown_cell"], block["canopy_cell"], block["band_cell"], block["ao_cell"]) == (
        dirs,
        2 * dirs,
        3 * dirs,
        4 * dirs,
    )
    assert block["hz_cells"] == block["ao_cell"] + 1 == model.HZ_CELLS
    assert json.loads(json.dumps(block)) == block
    assert math.isclose(block["shadow_soft_deg"], model.SHADOW_SOFT_DEG)
