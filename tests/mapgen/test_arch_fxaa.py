"""FXAA on the arches only: nothing else moves, and a band is antialiased as the sheet is.

docs/map/light-and-crowns.md section 29, "FXAA on the arches". Synthetic sheets throughout.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from mapgen.render.draw import archaa
from mapgen.render.draw.stream import RenderStream

SIZE = 128


def _sheet():
    """Stair-stepped diagonals over noise: edges FXAA finds, an arch over a third of them."""
    rng = np.random.default_rng(4)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE]
    light = ((xx + 2 * yy) // 7 % 2 == 0)[..., None]
    rgb = np.where(light, [200, 190, 170], [60, 70, 50]).astype(int)
    rgb = np.clip(rgb + rng.integers(-3, 4, rgb.shape), 0, 255).astype(np.uint8)
    cover = np.zeros((SIZE, SIZE), np.uint8)
    cover[30:90, 40:70] = 1
    return rgb, cover


def test_only_the_arches_and_their_edge_move():
    rgb, cover = _sheet()
    out = archaa.arch_fxaa(rgb, cover, slice(0, SIZE))
    moved = (out != rgb).any(-1)
    assert moved.any(), "an arch's stair steps are antialiased"
    assert not (moved & ~archaa.arch_mask(cover)).any(), "nothing outside the grown arch moves"
    assert np.array_equal(archaa.arch_fxaa(rgb, np.zeros_like(cover), slice(0, SIZE)), rgb)


def test_a_band_with_its_halo_is_antialiased_as_the_whole_sheet():
    rgb, cover = _sheet()
    whole = archaa.arch_fxaa(rgb, cover, slice(0, SIZE))
    halo = archaa.FXAA_HALO
    for top in range(0, SIZE, 32):
        r0, r1 = max(top - halo, 0), min(top + 32 + halo, SIZE)
        band = archaa.arch_fxaa(rgb[r0:r1], cover[r0:r1], slice(top - r0, top + 32 - r0))
        assert np.array_equal(band, whole[top : top + 32]), top


def test_the_luma_is_summed_in_one_fixed_order():
    rgb = np.random.default_rng(2).random((5, 7, 3), dtype=np.float32)
    want = rgb[..., 0] * np.float32(0.299) + rgb[..., 1] * np.float32(0.587)
    want = want + rgb[..., 2] * np.float32(0.114)
    assert archaa._luma(rgb).tobytes() == want.tobytes()


class _Cutter:
    """``TileStream``'s stand-in: each sheet the rows it was handed, transformed."""

    def __init__(self):
        self.sheets: dict[int, list[np.ndarray]] = {}

    def sheet(self, out_dir, px, trees, dense=None):
        key = len(self.sheets)
        self.sheets[key] = []
        return key

    def put(self, sheet, rows, transform=None):
        self.sheets[sheet].append(rows if transform is None else transform(rows))


def test_the_stream_antialiases_each_band_with_its_neighbours_rows(tmp_path: Path):
    rgb, cover = _sheet()
    cutter = _Cutter()
    stream = RenderStream(cutter, ("terrain",), (tmp_path, "r"), SIZE, 7, None, cover)
    for top in range(0, SIZE, 32):
        stream.put(top, {"terrain": rgb[top : top + 32]})
    stream.finish()
    (lit,) = [rows for rows in cutter.sheets.values() if rows]
    assert np.array_equal(np.concatenate(lit), archaa.arch_fxaa(rgb, cover, slice(0, SIZE)))
