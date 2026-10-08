"""The crowns stamped on the GPU give the reference's bits, and a crown's box holds every pixel
its sprite reaches.

docs/map/renders.md section 41, "The draw on the GPU". Synthetic sprites and trees. The kernel
tests skip, saying why, on a machine without numba, CuPy or a CUDA device.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen import jit
from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD
from mapgen.terrain.crown_stamp import CrownSet, Placements, crown_placements, stamp_placed
from tests.support.crown_sprites import draw_atlas, random_sprite

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")


@pytest.fixture(scope="module")
def device() -> None:
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)


def _crowns(seed: int, trees: int, opacity: float = 1.0) -> CrownSet:
    """Species of three sizes and trees of every scale, yaw and lean over a 60 m square."""
    rng = np.random.default_rng(seed)
    atlas = draw_atlas([random_sprite(rng, side, side + 5) for side in (7, 33, 61)])
    records = np.zeros(trees, CROWN_RECORD)
    records["x"], records["y"] = rng.uniform(-3000.0, 3000.0, (2, trees))
    records["z"] = rng.uniform(-500.0, 2000.0, trees)
    records["yaw"] = rng.uniform(0.0, 360.0, trees)
    records["scale"] = rng.choice([0.3, 0.8, 1.0, 2.2, 4.5], trees) * rng.uniform(0.9, 1.1, trees)
    records["scale_z"] = rng.uniform(0.6, 1.6, trees)
    lean = rng.uniform(0.0, 0.4, (2, trees))
    records["axis_x"], records["axis_y"] = lean
    records["axis_z"] = np.sqrt(1.0 - (lean**2).sum(0))
    records["species"] = rng.integers(0, 3, trees)
    return CrownSet(records, atlas, opacity)


def _centres(size: int, lo: int, hi: int) -> np.ndarray:
    step = 7500.0 * 100.0 / size
    return (np.arange(lo, hi, dtype=np.float64) + 0.5) * step


def test_a_crowns_box_holds_every_pixel_its_sprite_reaches():
    crowns = _crowns(3, 60)
    x, y = _centres(8192, -200, 200), _centres(8192, -200, 200)
    placed = crown_placements(crowns, x, y, 7500.0 * 100.0 / 8192)
    band = np.array([[0, len(y), 0, len(x)]], np.int64)
    for k in range(len(placed.tile)):
        one = Placements(*(np.asarray(part)[k : k + 1] for part in placed))
        free = stamp_placed(crowns.atlas.atlas, one._replace(box=band), (x, y))
        rows, cols = np.nonzero(free["cover"])
        r0, r1, c0, c1 = placed.box[k]
        assert np.all((rows >= r0) & (rows < r1) & (cols >= c0) & (cols < c1)), k


@pytest.mark.usefixtures("device")
@pytest.mark.parametrize(
    ("size", "trees", "opacity", "rows", "cols"),
    [(2048, 400, 1.0, (-150, 150), (-150, 150)), (32768, 400, 0.8, (-300, -20), (-90, 310)),
     (8192, 1, 1.0, (-4, 3), (-5, 2)), (8192, 0, 1.0, (0, 8), (0, 8))],
)  # fmt: skip
def test_crowns_stamped_on_the_device_are_the_reference_s(size, trees, opacity, rows, cols):
    from mapgen.render.gpu.crowns import stamp_crowns

    crowns = _crowns(size + trees, trees, opacity)
    x, y = _centres(size, *cols), _centres(size, *rows)
    placed = crown_placements(crowns, x, y, 7500.0 * 100.0 / size)
    want = stamp_placed(crowns.atlas.atlas, placed, (x, y))
    got = stamp_crowns(crowns.atlas, placed, (x, y))
    assert got is not None
    for name, plane in want.items():
        assert got[name].dtype == plane.dtype and got[name].tobytes() == plane.tobytes(), name
    if trees > 1:
        assert want["cover"].any() and np.isfinite(want["top_cm"]).any()


@pytest.mark.usefixtures("device")
def test_the_device_follows_an_atlas_the_calibration_moved():
    from mapgen.render.gpu.crowns import stamp_crowns

    crowns = _crowns(9, 200)
    x = y = _centres(8192, -150, 150)
    placed = crown_placements(crowns, x, y, 7500.0 * 100.0 / 8192)
    first = stamp_crowns(crowns.atlas, placed, (x, y))
    crowns.levels = [[level * np.float32(0.5) for level in mips] for mips in crowns.levels]
    again = stamp_crowns(crowns.atlas, placed, (x, y))
    want = stamp_placed(crowns.atlas.atlas, placed, (x, y))
    assert first is not None and again is not None
    assert again["rgb"].tobytes() == want["rgb"].tobytes() != first["rgb"].tobytes()
