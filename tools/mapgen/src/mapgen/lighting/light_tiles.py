"""The lighting pyramid's tiles: their format, the work files, and the coarser levels.

Per tile, ``{z}/{x}_{y}.nrm.webp`` (lossless RGBA: east and south normal, sky view, land
weight) and ``{z}/{x}_{y}.hz.webp`` (an 8 x 8 grey atlas at half resolution, each cell in a
border of its edge texels: 32 faded ground horizons, then 32 crown horizons; lossless where a
band is folded in, else lossy). Every style reads the ground's; only a style that draws the
crowns adds theirs. docs/map/light-and-crowns.md section 29.
"""

from __future__ import annotations

import io
import warnings
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, TypeAlias, TypeVar

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.lighting.horizon import encode_horizon, normals
from mapgen.lighting.model import HZ_CELLS, HZ_GUTTER_PX
from mapgen.lighting.refold import path_elevations, refold
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX, tile_relpath

__all__ = [
    "HZ_LINEAR_SCALE",
    "HZ_QUALITY",
    "HZ_SUFFIX",
    "LEVEL_AHEAD",
    "LEVEL_TASK_TILES",
    "LINEAR_TO_HZ",
    "NRM_SUFFIX",
    "MapMode",
    "TileJob",
    "TileSet",
    "atlas_cells",
    "decode_linear",
    "downsample",
    "encode_linear",
    "encode_tiles",
    "exact_tiles",
    "hz_atlas",
    "level_layout",
    "level_strips",
    "normal_byte",
    "optional_array",
    "padded_window",
    "ring_rows",
    "tile_jobs",
    "upsampled",
    "work_array",
]

NRM_SUFFIX = ".nrm.webp"
HZ_SUFFIX = ".hz.webp"
#: The lossy horizon atlases' WebP quality: those with no band folded in (section 29).
HZ_QUALITY = 90
#: The lossless tiles' WebP effort: the same pixels as 4 in less time (section 29).
NRM_METHOD = 2
ATLAS_COLS = 8

#: Tiles of a level by ``(x, y)``: those whose horizon atlas is stored lossless.
TileSet: TypeAlias = frozenset[tuple[int, int]]

#: Stored horizons for the coarser levels, before encoding: degrees times this, as a byte.
HZ_LINEAR_SCALE = 2.8

#: Strips of a coarser level whose tiles may still be encoding while the next is computed,
#: and the tiles a task of the pool encodes.
LEVEL_AHEAD = 4
LEVEL_TASK_TILES = 4

ScalarT = TypeVar("ScalarT", bound=np.generic)
MapMode: TypeAlias = Literal["r", "r+", "w+", "c"]
_MemMap: TypeAlias = np.memmap[tuple[int, ...], np.dtype[np.generic]]


class Reducer(Protocol):
    """``np.mean`` or a NaN-skipping reduction, over the axes of a block of cells."""

    def __call__(
        self, a: NDArray[np.floating], /, axis: tuple[int, int]
    ) -> NDArray[np.floating]: ...


class TileJob(NamedTuple):
    """A tile to encode: its path stem, normal tile and horizon atlas, and whether the atlas
    is stored lossless."""

    stem: str
    nrm: U8Grid
    atlas: U8Grid
    exact: bool


def hz_atlas(hz_u8: U8Grid) -> U8Grid:
    """``(dirs, h, w)`` bytes as one grey image of ``ATLAS_COLS`` cells a row, each in a
    border of its own edge texels ``HZ_GUTTER_PX`` wide."""
    dirs, h, w = hz_u8.shape
    g = HZ_GUTTER_PX
    rows, sh, sw = -(-dirs // ATLAS_COLS), h + 2 * g, w + 2 * g
    out = np.zeros((rows * sh, ATLAS_COLS * sw), np.uint8)
    for k in range(dirs):
        r, c = divmod(k, ATLAS_COLS)
        out[r * sh : (r + 1) * sh, c * sw : (c + 1) * sw] = np.pad(hz_u8[k], g, mode="edge")
    return out


def atlas_cells(atlas: U8Grid, cells: int = HZ_CELLS) -> U8Grid:
    """``hz_atlas`` undone: the ``(cells, h, w)`` bytes inside their borders."""
    g = HZ_GUTTER_PX
    sh, sw = atlas.shape[0] // -(-cells // ATLAS_COLS), atlas.shape[1] // ATLAS_COLS
    found = []
    for k in range(cells):
        r, c = divmod(k, ATLAS_COLS)
        found.append(atlas[r * sh + g : (r + 1) * sh - g, c * sw + g : (c + 1) * sw - g])
    return np.stack(found)


def downsample(a: NDArray[np.floating], f: int = 2, how: Reducer = np.mean) -> NDArray[np.floating]:
    h, w = a.shape[0] // f, a.shape[1] // f
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN cell is empty, not an error
        return how(a[: h * f, : w * f].reshape(h, f, w, f, *a.shape[2:]), axis=(1, 3))


def upsampled(ringed: NDArray[np.floating]) -> F32Grid:
    """A half-resolution plane with a ring of one pixel past each edge at full resolution, each
    pixel interpolated from the four half-resolution pixels around its centre."""
    plane = np.asarray(ringed, np.float32)
    full: F32Grid = ndimage.zoom(plane, 2, order=1, mode="nearest", grid_mode=True)
    return full[2:-2, 2:-2]


def ring_rows(ringed: NDArray[np.floating], rows: slice) -> NDArray[np.floating]:
    """The half-resolution rows, ring included, that ``upsampled`` reads for ``rows``."""
    return ringed[rows.start // 2 : rows.stop // 2 + 2]


def normal_byte(component: F32Grid) -> U8Grid:
    """A normal's east or south component, -1 to 1, as the byte its tile stores."""
    return np.round((component * 0.5 + 0.5) * 255).astype(np.uint8)


def padded_window(
    arr: NDArray[np.number], r0: int, r1: int, c0: int, c1: int, fill: float | None = None
) -> F32Grid:
    """``arr[r0:r1, c0:c1]`` with whatever falls off the sheet edge-padded, or ``fill``."""
    n = arr.shape[0]
    a0, a1, b0, b1 = max(r0, 0), min(r1, n), max(c0, 0), min(c1, arr.shape[1])
    part = np.asarray(arr[a0:a1, b0:b1], np.float32)
    pads = ((a0 - r0, r1 - a1), (b0 - c0, c1 - b1))
    if fill is None:
        return np.pad(part, pads, mode="edge")
    return np.pad(part, pads, mode="constant", constant_values=fill)


def encode_tiles(jobs: Iterable[TileJob]) -> int:
    """Each job's two tiles written; returns the horizon atlases' bytes."""
    from PIL import Image

    written = 0
    for stem, nrm, atlas, exact in jobs:
        Path(stem).parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        Image.fromarray(nrm, "RGBA").save(buf, "WEBP", lossless=True, exact=True, method=NRM_METHOD)
        Path(stem + NRM_SUFFIX).write_bytes(buf.getvalue())
        buf = io.BytesIO()
        grey = Image.fromarray(atlas, "L").convert("RGB")
        if exact:
            grey.save(buf, "WEBP", lossless=True, exact=True, method=NRM_METHOD)
        else:
            grey.save(buf, "WEBP", quality=HZ_QUALITY, method=4)
        Path(stem + HZ_SUFFIX).write_bytes(buf.getvalue())
        written += len(buf.getvalue())
    return written


def exact_tiles(folded: BoolMask, tx0: int, ty0: int) -> TileSet:
    """The tiles of a half-resolution plane, the first at ``(tx0, ty0)``, where ``folded``
    holds a pixel: their atlases are stored lossless."""
    h = PYRAMID_TILE_PX // 2
    rows, cols = folded.shape[0] // h, folded.shape[1] // h
    any_in = folded[: rows * h, : cols * h].reshape(rows, h, cols, h).any(axis=(1, 3))
    return frozenset((tx0 + int(i), ty0 + int(j)) for j, i in zip(*np.nonzero(any_in), strict=True))


def tile_jobs(
    dest: Path, z: int, tx0: int, ty0: int, nrm: U8Grid, hz_u8: U8Grid, exact: TileSet
) -> Iterator[TileJob]:
    """Each tile's job, made as it is asked for; ``exact`` names the lossless atlases."""
    t, h = PYRAMID_TILE_PX, PYRAMID_TILE_PX // 2
    for j in range(nrm.shape[0] // t):
        for i in range(nrm.shape[1] // t):
            stem = str(dest / tile_relpath(z, tx0 + i, ty0 + j))[: -len(".png")]
            cell = np.ascontiguousarray(nrm[j * t : (j + 1) * t, i * t : (i + 1) * t])
            atlas = hz_atlas(hz_u8[:, j * h : (j + 1) * h, i * h : (i + 1) * h])
            yield TileJob(stem, cell, atlas, (tx0 + i, ty0 + j) in exact)


def work_array(
    work: Path, name: str, dtype: type[ScalarT], mode: MapMode = "r+"
) -> NDArray[ScalarT]:
    """The work file ``name``, mapped; it holds ``dtype``."""
    array = np.load(work / f"{name}.npy", mmap_mode=mode)
    if array.dtype != dtype:
        raise TypeError(f"{work / name}.npy holds {array.dtype}, not {np.dtype(dtype)}")
    return array


def optional_array(work: Path, name: str, dtype: type[ScalarT]) -> NDArray[ScalarT] | None:
    """``work_array`` read-only, or None where the run wrote no such file."""
    if not (work / f"{name}.npy").is_file():
        return None
    return work_array(work, name, dtype, "r")


def decode_linear(q: NDArray[np.integer]) -> F32Grid:
    """A coarser level's stored horizon byte back to degrees."""
    return np.asarray(q, np.float32) / np.float32(HZ_LINEAR_SCALE)


def encode_linear(deg: F32Grid) -> U8Grid:
    """Degrees as a coarser level stores them: ``HZ_LINEAR_SCALE`` a degree, as a byte."""
    return np.round(np.clip(deg, 0, 90) * HZ_LINEAR_SCALE).astype(np.uint8)


#: ``encode_horizon(decode_linear(q))`` for every byte: elementwise, so a lookup is exact.
LINEAR_TO_HZ = encode_horizon(decode_linear(np.arange(256, dtype=np.uint8)))


def level_layout(half: int) -> tuple[tuple[str, type[np.generic], tuple[int, ...]], ...]:
    """The four sources of a coarser level, for a level ``half`` px on a side: name, type,
    shape."""
    quarter = max(half // 2, 1)
    return (
        ("zh", np.float32, (half, half)),
        ("landh", np.uint8, (half, half)),
        ("svfh", np.uint8, (half, half)),
        ("hzq", np.uint8, (quarter, quarter, HZ_CELLS)),
    )


def level_strips(
    work: Path,
    dest: Path,
    level: int,
    spacing_m: float,
    pool: Executor,
    last: bool,
    exact: TileSet = frozenset(),
) -> int:
    """One coarser level from the sources below it, a strip of tile rows at a time; ``exact``
    names its tiles whose atlas is stored lossless.

    The pool encodes a strip's tiles while this process computes the strips after it.
    """
    z = work_array(work, "zh", np.float32, "r")
    land = work_array(work, "landh", np.uint8, "r")
    svf = work_array(work, "svfh", np.uint8, "r")
    hzq = work_array(work, "hzq", np.uint8, "r")
    size = z.shape[0]
    next_level: dict[str, _MemMap] = {}
    if not last:
        for name, dtype, shape in level_layout(size // 2):
            path = work / f"{name}.next.npy"
            next_level[name] = np.lib.format.open_memmap(path, "w+", dtype, shape)
    t = PYRAMID_TILE_PX
    rows = t
    count = 0
    pending: deque[list[Future[int]]] = deque()
    for r0 in range(0, size, rows):
        z_strip = padded_window(z, r0 - 1, r0 + rows + 1, -1, size + 1)
        nx, ny = normals(z_strip, spacing_m)
        nrm = np.stack(
            [normal_byte(nx), normal_byte(ny), svf[r0 : r0 + rows], land[r0 : r0 + rows]], -1
        )
        hz_u8 = np.moveaxis(LINEAR_TO_HZ[hzq[r0 // 2 : (r0 + rows) // 2]], -1, 0)
        jobs = list(tile_jobs(dest, level, 0, r0 // t, nrm, hz_u8, exact))
        count += len(jobs)
        tasks = [jobs[i : i + LEVEL_TASK_TILES] for i in range(0, len(jobs), LEVEL_TASK_TILES)]
        pending.append([pool.submit(encode_tiles, task) for task in tasks])
        del jobs, tasks
        if next_level:
            a, b = r0 // 2, (r0 + rows) // 2
            next_level["zh"][a:b] = downsample(np.asarray(z[r0 : r0 + rows]))
            next_level["landh"][a:b] = np.round(downsample(land[r0 : r0 + rows].astype(np.float32)))
            next_level["svfh"][a:b] = np.round(downsample(svf[r0 : r0 + rows].astype(np.float32)))
            hz_rows = decode_linear(hzq[r0 // 2 : (r0 + rows) // 2])
            # On the CPU: the run's own process opens no CUDA context (renders.md section 41).
            next_level["hzq"][r0 // 4 : (r0 + rows) // 4] = encode_linear(
                refold(hz_rows, path_elevations(), gpu=False)
            )
        while len(pending) > LEVEL_AHEAD:
            _wait(pending.popleft())
    while pending:
        _wait(pending.popleft())
    del z, land, svf, hzq
    for name in list(next_level):
        next_level.pop(name).flush()  # Windows replaces a file only once its last map is closed
        (work / f"{name}.next.npy").replace(work / f"{name}.npy")
    return count


def _wait(futures: Sequence[Future[int]]) -> None:
    for future in futures:
        future.result()
