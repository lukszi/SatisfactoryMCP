"""The painted ground's threaded blurs, row blocks and lean colour split: bit for bit the serial
numpy they replace. docs/map/painted.md section 43. Synthetic fixtures only.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from mapgen.palette.painted import parallel
from mapgen.palette.painted.transfer import _layer, by_colour, layer_op


@pytest.mark.parametrize("step", [1, 4])
@pytest.mark.parametrize("sigma", [0.0, 1.5, 12.0])
def test_the_threaded_blur_is_gaussian_filter_bit_for_bit(sigma, step):
    rng = np.random.default_rng(7)
    rgb = rng.random((301, 257, 3), dtype=np.float32)
    plane = rgb[..., 1]  # a strided view, as the ground blurs its channels
    want = ndimage.gaussian_filter(plane, sigma)[::step, ::step]
    got = parallel.gaussian(plane, sigma, step)
    assert got.dtype == want.dtype
    assert np.array_equal(got.view(np.uint32), want.view(np.uint32))


def test_row_blocks_cover_every_row_once():
    hits = np.zeros(1100, np.int32)

    def work(block: slice) -> None:
        hits[block] += 1

    parallel.for_row_blocks(work, len(hits), 128)
    assert (hits == 1).all()


def _by_colour_sorted(lab, share, ops):
    """``by_colour`` as it was: every plane's median kept and the masses sorted."""
    present = [name for name, w in share.items() if w.any()]
    layers = sorted({_layer(name) for name in present})
    if len(layers) < 2:
        return dict(share)
    mass = np.zeros((len(layers), *lab.shape[:2]), np.float32)
    median = np.zeros((len(layers), *lab.shape[:2], 3), np.float32)
    for name in present:
        k = layers.index(_layer(name))
        mass[k] += share[name]
        median[k] += share[name][..., None] * ops[name].source
    median /= np.maximum(mass, np.float32(1e-12))[..., None]
    order = np.argsort(-mass, axis=0, kind="stable")[:2]
    w_i, w_j = np.take_along_axis(mass, order, 0)
    s_i, s_j = np.take_along_axis(median, order[..., None], 0)
    mixed = w_j > 0
    pair = np.maximum(w_i + w_j, np.float32(1e-12))
    along = s_i - s_j
    reach = along[..., 0] ** 2 + along[..., 1] ** 2 + along[..., 2] ** 2
    off = lab - s_j
    dot = off[..., 0] * along[..., 0] + off[..., 1] * along[..., 1] + off[..., 2] * along[..., 2]
    u = np.clip(dot / np.maximum(reach, np.float32(1e-12)), 0.0, 1.0)
    held = w_i / pair
    moved = (held + np.float32(4.0) * held * (1.0 - held) * (u - held)) * pair
    tiny = np.float32(1e-12)
    scale = np.ones_like(mass)
    np.put_along_axis(scale, order[:1], (moved / np.maximum(w_i, tiny))[None], 0)
    np.put_along_axis(scale, order[1:], ((pair - moved) / np.maximum(w_j, tiny))[None], 0)
    out = dict(share)
    for name in present:
        k = layers.index(_layer(name))
        out[name] = np.where(mixed, share[name] * scale[k], share[name]).astype(np.float32)
    return out


def test_the_lean_colour_split_matches_the_sorted_one_bit_for_bit():
    rng = np.random.default_rng(11)
    shape = (64, 80)
    lab = rng.random((*shape, 3), dtype=np.float32) * np.float32(0.3)
    names = ["Sand", "Grass", "Grass@0", "Cliff", "Forest"]
    # Weights in quarters, many zero: ties between layers and texels of one layer are common.
    raw = {name: rng.integers(0, 3, shape).astype(np.float32) * 64 for name in names}
    raw["Forest"][:] = 0  # a calibrated layer absent from the block
    total = np.maximum(sum(raw.values()), np.float32(1e-6))
    share = {name: w / total for name, w in raw.items()}
    ops = {
        name: layer_op(rng.random(3, dtype=np.float32) * 0.2, rng.random(3, dtype=np.float32) * 0.2)
        for name in names
    }
    want, got = _by_colour_sorted(lab, share, ops), by_colour(lab, share, ops)
    assert want.keys() == got.keys()
    for name in names:
        assert np.array_equal(got[name].view(np.uint32), want[name].view(np.uint32)), name
