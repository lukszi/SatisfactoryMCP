"""The painters' numba kernels give the bits of the numpy they replace: the crown stamps.

docs/map/renders.md section 41. Synthetic fixtures: no install, no field. Each comparison runs
one call twice, ``MAPGEN_KERNELS=numpy`` then the kernels, and compares the bytes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable

import numpy as np
import pytest

from mapgen import jit
from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD
from mapgen.terrain.crown_stamp import CrownSet, sprite_levels, stamp_crowns
from tests.support.paths import REPO_ROOT

needs_numba = pytest.mark.skipif(jit._numba() is None, reason="numba is not installed")


def _bits(value: object) -> object:
    if isinstance(value, dict):
        return {key: _bits(plane) for key, plane in value.items()}
    assert isinstance(value, np.ndarray)
    return value.dtype, value.shape, value.tobytes()


def _same(monkeypatch: pytest.MonkeyPatch, call: Callable[[], object]) -> object:
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    reference = call()
    monkeypatch.delenv(jit.KERNEL_SWITCH)
    assert jit.kernels_on()
    compiled = call()
    assert _bits(compiled) == _bits(reference)
    return compiled


# ------------------------------------------------------------------------------- switch


def test_the_reference_never_loads_numba():
    code = (
        "import os, sys\n"
        "os.environ['MAPGEN_KERNELS'] = 'numpy'\n"
        "import numpy as np\n"
        "from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD\n"
        "from mapgen.terrain.crown_stamp import CrownSet, stamp_crowns\n"
        "tree = np.zeros(1, CROWN_RECORD)\n"
        "tree['scale'] = tree['scale_z'] = tree['axis_z'] = 1.0\n"
        "one = np.ones(1, np.float32)\n"
        "crowns = CrownSet(tree, [[np.ones((5, 5, 6), np.float32)]], [(-25.0, -25.0)], "
        "one * 80, one, one)\n"
        "centres = np.arange(-5.0, 5.0) * 20.0\n"
        "assert stamp_crowns(crowns, centres, centres, 20.0)['cover'].any()\n"
        "print(sorted(m for m in sys.modules if m == 'numba' or m.endswith('.kernels')))\n"
    )
    paths = [str(REPO_ROOT / "src"), str(REPO_ROOT / "tools" / "mapgen" / "src"), str(REPO_ROOT)]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(paths)}
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          env=env, timeout=120, check=False)  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "[]"


@needs_numba
def test_every_painter_kernel_releases_the_gil_and_caches_on_disk():
    from numba.core.caching import NullCache

    from mapgen.terrain import kernels as terrain

    for kernel in (terrain.stamp,):
        assert kernel.targetoptions["nogil"] is True
        assert not isinstance(kernel._cache, NullCache)


# ---------------------------------------------------------------------------- the crowns


def _sprite(rng: np.random.Generator, side: int) -> dict:
    cover = rng.integers(0, 256, (side, side + 3)).astype(np.uint8)
    cover[rng.random(cover.shape) < 0.3] = 0
    return {"cover": cover, "slot": rng.integers(0, 3, cover.shape).astype(np.uint8),
            "top_cm": rng.integers(300, 2500, cover.shape).astype(np.int16)}  # fmt: skip


def _crowns(seed: int, trees: int = 400) -> CrownSet:
    """Species of three sizes and trees of every scale, yaw and lean over a 60 m square."""
    rng = np.random.default_rng(seed)
    colours = [[0.1, 0.3, 0.05], None, [0.2, 0.25, 0.1]]
    sprites = [_sprite(rng, side) for side in (9, 40, 70)]
    levels = [sprite_levels(sprite, colours) for sprite in sprites]
    origins = [(-0.5 * 12.5 * s["cover"].shape[1], -0.4 * 12.5 * s["cover"].shape[0])
               for s in sprites]  # fmt: skip
    records = np.zeros(trees, CROWN_RECORD)
    records["x"], records["y"] = rng.uniform(-3000.0, 3000.0, (2, trees))
    records["z"] = rng.uniform(-500.0, 2000.0, trees)
    records["yaw"] = rng.uniform(0.0, 360.0, trees)
    records["scale"] = rng.choice([0.25, 0.7, 1.0, 1.9, 4.0], trees) * rng.uniform(0.9, 1.1, trees)
    records["scale_z"] = rng.uniform(0.6, 1.6, trees)
    lean = rng.uniform(0.0, 0.4, (2, trees))
    records["axis_x"], records["axis_y"] = lean
    records["axis_z"] = np.sqrt(1.0 - (lean**2).sum(0))
    records["species"] = rng.integers(0, len(sprites), trees)
    reach = np.array([np.hypot(*s["cover"].shape) * 12.5 for s in sprites], np.float32)
    mid = np.array([800.0, 1200.0, 1500.0], np.float32)
    top = np.array([2500.0, 2500.0, 2500.0], np.float32)
    return CrownSet(records, levels, origins, reach, mid, top, ["a", "b", "c"])


@needs_numba
@pytest.mark.parametrize("size", [2048, 8192, 32768])
def test_the_crown_stamps_are_the_reference_bit_for_bit(monkeypatch, size):
    crowns = _crowns(seed=size)
    step = 7500.0 * 100.0 / size
    centres = (np.arange(-150, 150, dtype=np.float64) + 0.5) * step
    stamped = _same(monkeypatch, lambda: stamp_crowns(crowns, centres, centres[40:220], step))
    assert stamped["cover"].any(), "the band holds trees"
    _same(monkeypatch, lambda: stamp_crowns(crowns, centres[7:], centres[5:9], step))


@needs_numba
def test_the_stamps_follow_mips_replaced_after_the_first_stamp(monkeypatch):
    crowns = _crowns(seed=4)
    step = 7500.0 * 100.0 / 32768
    centres = (np.arange(-200, 200, dtype=np.float64) + 0.5) * step
    first = _same(monkeypatch, lambda: stamp_crowns(crowns, centres, centres, step))
    crowns.levels = [[level * np.float32(0.5) for level in mips] for mips in crowns.levels]
    again = _same(monkeypatch, lambda: stamp_crowns(crowns, centres, centres, step))
    assert not np.array_equal(again["rgb"], first["rgb"])


def test_float32_pixel_centres_take_the_reference(monkeypatch):
    """The kernel reproduces float64 centres; with others the reference stamps."""
    monkeypatch.delenv(jit.KERNEL_SWITCH, raising=False)
    crowns = _crowns(seed=5, trees=60)
    centres = (np.arange(-100, 100) + 0.5).astype(np.float32) * np.float32(30.0)
    got = stamp_crowns(crowns, centres, centres, 30.0)
    monkeypatch.setenv(jit.KERNEL_SWITCH, jit.REFERENCE)
    assert _bits(got) == _bits(stamp_crowns(crowns, centres, centres, 30.0))

