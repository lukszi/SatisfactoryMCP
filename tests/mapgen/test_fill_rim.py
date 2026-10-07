"""The fill past the artwork's world rim is left empty (docs/map/renders.md section 26)."""

from __future__ import annotations

import numpy as np

from mapgen.terrain.emptied import RIM_REACH_TEXELS, void_past_rim
from mapgen.terrain.fill import SOURCE_RASTER, SOURCE_RIM, SOURCE_ROCK, SOURCE_SEAM, fill_field
from satisfactory_mcp.core.arrays import BoolMask
from satisfactory_mcp.domain.spatial import heightfield as hf

N = 120


def _artwork_void() -> BoolMask:
    """The artwork's void on a small grid: the world on the west, the black past its rim on
    the east, a light rim line between, the sheet's thin dark frame round the rest, a pit in
    the world and a dark stroke that meets the void through a gap in the rim line."""
    void = np.zeros((N, N), bool)
    void[:, 80:] = True  # past the rim
    void[:, 78:80] = False  # the rim's light line
    void[0:3, :] = void[-3:, :] = void[:, 0:3] = True  # the sheet's frame
    void[40:60, 30:50] = True  # a pit
    void[90:92, 20:80] = True  # a dark stroke ...
    void[90:92, 78:80] = True  # ... through a gap in the rim line
    return void


def test_the_void_past_the_rim_is_the_thick_void_at_the_grid_edge():
    void = _artwork_void()
    got = void_past_rim(void)
    assert got[10:110, 80:].all(), "the black past the rim"
    assert not got[:, 3:78][~void[:, 3:78]].any(), "never the world the artwork draws"
    assert not got[40:60, 30:50].any(), "a pit is not past the rim"
    assert not got[5:110, 0:3].any() and not got[0:3, 5:70].any(), "nor the sheet's frame"
    reached = np.nonzero(got[90])[0]
    assert reached.min() >= 80 - RIM_REACH_TEXELS, "a stroke is reached a few texels at most"


def _fill(void: BoolMask | None):
    prov = np.full((N, N), hf.PROV_FILL, np.uint8)
    prov[:, :20] = hf.PROV_LANDSCAPE
    prov[60:70, 100:110] = hf.PROV_CLIFF_DIRECT  # a rock standing in the void
    land = np.full((N, N), 50, np.float32)
    height = np.where(prov == hf.PROV_FILL, 30, land).astype(np.int16)
    ground = np.where(prov == hf.PROV_LANDSCAPE, land, hf.NODATA).astype(np.float32)
    ground[prov == hf.PROV_FILL] = 30
    return fill_field(
        ground_dm=ground,
        height_dm=height,
        prov=prov,
        water_quality=np.zeros((N, N), np.uint8),
        water_dm=np.full((N, N), hf.NODATA, np.int16),
        raster_m=np.full((8, 8), 2.0, np.float32),
        raster_ok=np.ones((8, 8), bool),
        field_origin_cm=(0.0, 0.0),
        spacing_cm=100.0,
        raster_box_cm=(-50.0, N * 100.0 - 50.0, -50.0, N * 100.0 - 50.0),
        nodata=hf.NODATA,
        fill_value=hf.PROV_FILL,
        rock_values=hf.PROV_CLIFF_VALUES,
        void=void,
    )


def test_the_fill_past_the_rim_is_left_empty_and_the_rest_is_kept():
    heights, ground, source, meta = _fill(_artwork_void())
    past = np.zeros((N, N), bool)
    past[10:110, 80:] = True
    past[60:70, 100:110] = False
    assert (heights[past] == hf.NODATA).all() and (ground[past] == hf.NODATA).all()
    assert (source[past] == SOURCE_RIM).all()
    assert (source[60:70, 100:110] == SOURCE_ROCK).all(), "a rock in the void is kept"
    drawn = ~_artwork_void()[10:110, 20:78]
    assert np.isin(source[10:110, 20:78][drawn], (SOURCE_RASTER, SOURCE_SEAM)).all()
    assert np.isin(source[40:60, 30:50], (SOURCE_RASTER, SOURCE_SEAM)).all(), "a pit's fill"
    rim = meta["past_the_rim"]
    assert isinstance(rim, dict) and rim["texels"] == int((source == SOURCE_RIM).sum())


def test_without_the_artwork_nothing_is_clipped():
    heights, _ground, source, meta = _fill(None)
    assert not (source == SOURCE_RIM).any() and (heights[:, 20:] != hf.NODATA).all()
    rim = meta["past_the_rim"]
    assert isinstance(rim, dict) and rim["texels"] == 0
