"""The painted ground's full-grid work spread over threads, bit for bit the serial result.

A Gaussian blur is one 1-D pass per axis, every line on its own, and the ground's per-texel
steps work a block of rows at a time; cutting the lines or the blocks among threads changes no
bit. docs/map/painted.md section 43.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise

import numpy as np
from scipy import ndimage

from mapgen.palette.painted.shapes import FloatGrid

__all__ = ["BLOCK_ROWS", "BLOCK_THREADS", "BLUR_THREADS", "for_row_blocks", "gaussian"]

#: The threads a blur's lines are cut among, at most; each holds only its lines.
BLUR_THREADS = 8

#: The row blocks worked at once, at most: a transfer block holds about 0.5 GB of temporaries.
BLOCK_THREADS = 4

#: The rows of one block of the per-texel steps.
BLOCK_ROWS = 512


def _threads(cap: int) -> int:
    return max(1, min(cap, os.cpu_count() or 1))


def _spans(length: int, parts: int) -> list[slice]:
    edges = np.linspace(0, length, parts + 1).astype(int)
    return [slice(a, b) for a, b in pairwise(edges) if b > a]


def _run(work: Callable[[slice], None], spans: list[slice], cap: int) -> None:
    threads = min(_threads(cap), len(spans))
    if threads <= 1:
        for span in spans:
            work(span)
        return
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="paint") as pool:
        for future in [pool.submit(work, span) for span in spans]:
            future.result()


def gaussian(plane: FloatGrid, sigma: float, step: int = 1) -> FloatGrid:
    """``ndimage.gaussian_filter(plane, sigma)[::step, ::step]`` for a 2-D float plane.

    The column pass runs on spans of columns, then the row pass on spans of the rows kept;
    each pass keeps the input's dtype in between, as ``gaussian_filter`` does.
    """
    if sigma <= 1e-15:  # gaussian_filter's own cut: no axis is blurred
        return plane[::step, ::step].copy()
    cols = plane.shape[1]
    columns = np.empty(plane.shape, plane.dtype)
    parts = _threads(BLUR_THREADS)

    def down(span: slice) -> None:
        ndimage.gaussian_filter1d(plane[:, span], sigma, axis=0, output=columns[:, span])

    _run(down, _spans(cols, parts), parts)
    kept = columns[::step]
    out = np.empty(kept.shape, plane.dtype)

    def across(span: slice) -> None:
        ndimage.gaussian_filter1d(kept[span], sigma, axis=1, output=out[span])

    _run(across, _spans(kept.shape[0], parts), parts)
    return out[:, ::step]


def for_row_blocks(work: Callable[[slice], None], rows: int, block_rows: int = BLOCK_ROWS) -> None:
    """``work`` on each block of ``block_rows`` rows, ``BLOCK_THREADS`` at a time; each call
    writes only its own block's rows."""
    blocks = [slice(start, start + block_rows) for start in range(0, rows, block_rows)]
    _run(work, blocks, BLOCK_THREADS)
