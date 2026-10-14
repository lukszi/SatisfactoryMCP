"""``compose_field``: rock past the landscape's frame replaces the fill it agrees with."""

from __future__ import annotations

import numpy
import pytest

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.terrain.heightfield import field
from satisfactory_mcp.domain.spatial import heightfield as hf

GRID = 8


@pytest.fixture
def small_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    """An 8 x 8 output grid whose fill raster is read one texel to one texel."""
    monkeypatch.setattr(field, "GRID_PX", GRID)
    monkeypatch.setattr(
        field, "fill_raster_indices", lambda: (numpy.arange(GRID), numpy.arange(GRID))
    )


def _frame(rows: int) -> dict[str, object]:
    """A landscape of ``rows`` rows at 10 m across the top of the grid, all of it good."""
    z_cm = numpy.full((rows, GRID), 1000.0, numpy.float32)
    return {
        "z_cm": z_cm,
        "good": numpy.ones_like(z_cm, bool),
        "width": GRID,
        "height": rows,
        "x0_cm": ORIGIN_X_CM,
        "y0_cm": ORIGIN_Y_CM,
        "scale_cm": SPACING_CM,
    }


def test_rock_past_the_frame_takes_the_fill_it_stands_on(small_grid: None) -> None:
    """Past the frame's last row a rock at or over the fill, or under it by less than
    ``BEYOND_FRAME_BELOW_FILL_M``, is the ground; one deeper (the seabed under the sea the
    fill holds) and one over no fill keep the fill and the no-data."""
    fill = numpy.full((GRID, GRID), -1630.0)
    valid = numpy.ones((GRID, GRID), bool)
    valid[7, 7] = False
    cliff = numpy.full((GRID, GRID), numpy.nan, numpy.float32)
    cliff[:, 0] = 500.0  # a rock under the landscape's 10 m: the landscape keeps it
    cliff[4:, 1] = -1500.0  # over the fill: rock
    cliff[4:, 2] = -1630.0 - 150.0  # 1.5 m under it: rock
    cliff[4:, 3] = -1630.0 - 5000.0  # the seabed 50 m down: fill
    cliff[7, 7] = 0.0  # no fill to agree with: no data
    cliffs = {"z_cm": cliff, "density": numpy.full((GRID, GRID), 9, numpy.uint32)}

    got = field.compose_field(_frame(4), cliffs, fill, valid)  # type: ignore[arg-type]

    prov, height = got["prov"], got["height_dm"]
    assert (prov[:4] == hf.PROV_LANDSCAPE).all() and (height[:4] == 100).all()
    assert (prov[4:, 1] == hf.PROV_CLIFF_DIRECT).all() and (height[4:, 1] == -150).all()
    assert (prov[4:, 2] == hf.PROV_CLIFF_DIRECT).all() and (height[4:, 2] == -178).all()
    assert (prov[4:, 3] == hf.PROV_FILL).all() and (height[4:, 3] == -163).all()
    assert prov[7, 7] == hf.PROV_NODATA and height[7, 7] == hf.NODATA
