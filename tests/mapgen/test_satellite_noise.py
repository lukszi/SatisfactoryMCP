"""The satellite style's noise is read between its cells, never as blocks (renders.md §17)."""

from __future__ import annotations

import numpy as np

from mapgen.palette.styles import NOISE_SEED, noise_fields
from mapgen.terrain.sample import sample_noise

SIZE = 32768


def _nearest(fields, rows, cols, size):
    """The noise as it was read before: each pixel takes its cell's value."""
    out = np.ones((len(rows), len(cols)), np.float32)
    for field, amount in fields:
        side = field.shape[0]
        v, u = ((i + 0.5) * side / size for i in (rows, cols))
        out += amount * field[np.ix_(v.astype(np.int64) % side, u.astype(np.int64) % side)]
    return out


def test_the_noise_has_no_step_at_a_cell_edge():
    fields = noise_fields(NOISE_SEED)
    rows, cols = np.arange(10_000, 10_400), np.arange(0, 600)
    noise, blocks = sample_noise(fields, rows, cols, SIZE), _nearest(fields, rows, cols, SIZE)
    for axis in (0, 1):
        step, block_step = (np.abs(np.diff(a, axis=axis)).max() for a in (noise, blocks))
        assert step < 0.1 * block_step, "a 7.3 m cell's edge is no longer a step"
    assert abs(float(noise.std()) - float(blocks.std())) < 0.2 * float(blocks.std())


def test_the_noise_holds_each_cell_value_at_its_centre_and_wraps():
    field = np.random.default_rng(3).standard_normal((8, 8)).astype(np.float32)
    noise = sample_noise([(field, 1.0)], np.arange(128), np.arange(128), 128) - 1.0
    span = float(np.ptp(field))
    # 16 px a cell: cell (2, 5)'s centre lies between pixels 39 and 40, and 87 and 88
    assert abs(float(noise[39:41, 87:89].mean()) - float(field[2, 5])) < 0.01 * span
    # the last pixel and the first both lie between the last cell and the first
    assert np.abs(noise[127] - noise[0]).max() < 0.1 * span
    assert np.abs(noise[:, 127] - noise[:, 0]).max() < 0.1 * span


def test_the_noise_is_the_same_in_pieces():
    fields = noise_fields(NOISE_SEED)
    rows, cols = np.arange(200, 260), np.arange(300, 900)
    whole = sample_noise(fields, rows, cols, SIZE)
    parts = [sample_noise(fields, rows, cols[a : a + 64], SIZE) for a in range(0, 600, 64)]
    assert np.array_equal(whole, np.concatenate(parts, axis=1))
