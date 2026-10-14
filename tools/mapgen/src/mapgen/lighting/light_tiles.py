"""The lighting pyramid's tiles: their format, the work files, and the coarser levels.

Per tile, ``{z}/{x}_{y}.nrm.webp`` (lossless RGBA: east and south normal, sky view, land
weight) and ``{z}/{x}_{y}.hz.webp`` (a grey atlas of 8 cells a row at half resolution, each
cell in a border of its edge texels: 32 faded ground horizons, 32 of the trees' shade, 32 of
the ground's horizons on the canopy top, 32 of the bands' shade, then the trees' ambient
occlusion; at a higher quality where shade is folded in). Every style reads the ground's; only a style that draws the trees adds theirs.
docs/map/light-and-crowns.md section 29.
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
from mapgen.lighting.model import HORIZON_CELLS, HZ_CELLS, HZ_GUTTER_PX
from mapgen.lighting.refold import path_elevations, refold
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U8Grid
from satisfactory_mcp.core.gameassets.pyramid import PYRAMID_TILE_PX, tile_relpath

__all__ = [
    "DETAIL_SCALE",
    "HZ_FOLDED_QUALITY",
    "HZ_LINEAR_SCALE",
    "HZ_QUALITY",
    "HZ_SUFFIX",
    "LEVEL_AHEAD",
    "LEVEL_PIECE_TILES",
    "LEVEL_TASK_TILES",
    "LINEAR_TO_HZ",
    "NRM_SUFFIX",
    "CoarseLevel",
    "LevelSources",
    "MapMode",
    "TileJob",
    "TileSet",
    "atlas_cells",
    "decode_linear",
    "detail_window",
    "downsample",
    "encode_linear",
    "encode_tiles",
    "folded_tiles",
    "hz_atlas",
    "hz_webp",
    "level_layout",
    "level_strips",
    "normal_byte",
    "open_sources",
    "optional_array",
    "padded_window",
    "ring_rows",
    "tile_jobs",
    "upsampled",
    "with_detail",
    "work_array",
]

NRM_SUFFIX = ".nrm.webp"
HZ_SUFFIX = ".hz.webp"
#: The horizon atlases' WebP quality, and that of an atlas with a band folded in, which sits
#: inside the shader's soft edge where a codec's error is the whole shade (section 29).
HZ_QUALITY = 90
HZ_FOLDED_QUALITY = 95
#: The lossless tiles' WebP effort: the same pixels as 4 in less time (section 29).
NRM_METHOD = 2
ATLAS_COLS = 8
#: The ground's detail normal in the work file ``detail``: a component of -1 to 1 as a byte
#: of -127 to 127 (docs/map/painted.md section 30, "The layers' own textures").
DETAIL_SCALE = 127.0

#: Tiles of a level by ``(x, y)``: those whose horizon atlas holds a folded band.
TileSet: TypeAlias = frozenset[tuple[int, int]]

#: Stored horizons for the coarser levels, before encoding: degrees times this, as a byte.
HZ_LINEAR_SCALE = 2.8

#: Pieces of a coarser level's strip whose tiles may still be encoding while the next is
#: computed, the tiles of a piece, and the tiles a task of the pool encodes.
LEVEL_AHEAD = 4
LEVEL_PIECE_TILES = 16
LEVEL_TASK_TILES = 4
#: Quarter rows refolded at a time. Pieces and these bound the arrays, not the bytes.
REFOLD_ROWS = 32

ScalarT = TypeVar("ScalarT", bound=np.generic)
MapMode: TypeAlias = Literal["r", "r+", "w+", "c"]


class Reducer(Protocol):
    """``np.mean`` or a NaN-skipping reduction, over the axes of a block of cells."""

    def __call__(
        self, a: NDArray[np.floating], /, axis: tuple[int, int]
    ) -> NDArray[np.floating]: ...


class TileJob(NamedTuple):
    """A tile to encode: its path stem, normal tile and horizon atlas, and whether a band is
    folded into the atlas."""

    stem: str
    nrm: U8Grid
    atlas: U8Grid
    folded: bool


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
    found: list[U8Grid] = []
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


def detail_window(work: Path, r0: int, c0: int, side: int) -> NDArray[np.int8] | None:
    """The ground's detail normal over a block, as bytes; None where the run drew none."""
    plane = optional_array(work, "detail", np.int8)
    return None if plane is None else np.asarray(plane[r0 : r0 + side, c0 : c0 + side])


def with_detail(
    east: F32Grid, south: F32Grid, detail: NDArray[np.int8] | None
) -> tuple[F32Grid, F32Grid]:
    """A normal with the ground's detail normal (east and south over ``DETAIL_SCALE``) added
    to its slope, renormalised: the textures' bumps on the drawn ground."""
    if detail is None:
        return east, south
    scale = np.float32(DETAIL_SCALE)
    tilt_e = east + detail[..., 0].astype(np.float32) / scale
    tilt_s = south + detail[..., 1].astype(np.float32) / scale
    up = np.sqrt(np.clip(np.float32(1.0) - east * east - south * south, 0.0, 1.0))
    length = np.sqrt(tilt_e * tilt_e + tilt_s * tilt_s + up * up)
    return (tilt_e / length).astype(np.float32), (tilt_s / length).astype(np.float32)


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


def hz_webp(atlas: U8Grid, folded: bool) -> bytes:
    """A horizon atlas as its tile stores it: WebP at ``HZ_QUALITY``, or at
    ``HZ_FOLDED_QUALITY`` where a band is folded in."""
    from PIL import Image

    buf = io.BytesIO()
    quality = HZ_FOLDED_QUALITY if folded else HZ_QUALITY
    Image.fromarray(atlas, "L").convert("RGB").save(buf, "WEBP", quality=quality, method=4)
    return buf.getvalue()


def encode_tiles(jobs: Iterable[TileJob]) -> int:
    """Each job's two tiles written; returns the horizon atlases' bytes."""
    from PIL import Image

    written = 0
    for stem, nrm, atlas, folded in jobs:
        Path(stem).parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        Image.fromarray(nrm, "RGBA").save(buf, "WEBP", lossless=True, exact=True, method=NRM_METHOD)
        Path(stem + NRM_SUFFIX).write_bytes(buf.getvalue())
        data = hz_webp(atlas, folded)
        Path(stem + HZ_SUFFIX).write_bytes(data)
        written += len(data)
    return written


def folded_tiles(folded: BoolMask, tx0: int, ty0: int) -> TileSet:
    """The tiles of a half-resolution plane, the first at ``(tx0, ty0)``, where ``folded``
    holds a pixel."""
    h = PYRAMID_TILE_PX // 2
    rows, cols = folded.shape[0] // h, folded.shape[1] // h
    any_in = folded[: rows * h, : cols * h].reshape(rows, h, cols, h).any(axis=(1, 3))
    return frozenset((tx0 + int(i), ty0 + int(j)) for j, i in zip(*np.nonzero(any_in), strict=True))


def tile_jobs(
    dest: Path, z: int, tx0: int, ty0: int, nrm: U8Grid, hz_u8: U8Grid, folded: TileSet
) -> Iterator[TileJob]:
    """Each tile's job, made as it is asked for; ``folded`` names the atlases with a band."""
    t, h = PYRAMID_TILE_PX, PYRAMID_TILE_PX // 2
    for j in range(nrm.shape[0] // t):
        for i in range(nrm.shape[1] // t):
            stem = str(dest / tile_relpath(z, tx0 + i, ty0 + j))[: -len(".png")]
            cell = np.ascontiguousarray(nrm[j * t : (j + 1) * t, i * t : (i + 1) * t])
            atlas = hz_atlas(hz_u8[:, j * h : (j + 1) * h, i * h : (i + 1) * h])
            yield TileJob(stem, cell, atlas, (tx0 + i, ty0 + j) in folded)


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


class LevelSources(NamedTuple):
    """A coarser level's four sources (``level_layout``): heights, land, sky view, horizons."""

    z: NDArray[np.float32]
    land: NDArray[np.uint8]
    svf: NDArray[np.uint8]
    hzq: NDArray[np.uint8]


def open_sources(work: Path, suffix: str = "") -> LevelSources:
    """The sources in ``work``'s files ``{name}{suffix}.npy``, read-only."""
    z, land, svf, hzq = (f"{name}{suffix}" for name, _, _ in level_layout(2))
    return LevelSources(
        work_array(work, z, np.float32, "r"),
        work_array(work, land, np.uint8, "r"),
        work_array(work, svf, np.uint8, "r"),
        work_array(work, hzq, np.uint8, "r"),
    )


def _create_sources(work: Path, suffix: str, half: int) -> LevelSources:
    made = [
        np.lib.format.open_memmap(work / f"{name}{suffix}.npy", "w+", dtype, shape)
        for name, dtype, shape in level_layout(half)
    ]
    return LevelSources(*made)


class CoarseLevel:
    """One coarser level, a strip of tile rows at a time as its sources' rows come in.

    Each strip writes its rows of the level below, in the files ``{name}{below}.npy`` made at
    the first strip; ``below`` None for the last level.
    """

    def __init__(
        self,
        sources: LevelSources,
        dest: Path,
        level: int,
        spacing_m: float,
        work: Path,
        below: str | None,
    ) -> None:
        self.sources, self.dest, self.level, self.spacing_m = sources, dest, level, spacing_m
        self.work, self.below_suffix = work, below
        self.below: LevelSources | None = None
        self.size = sources.z.shape[0]
        self.r0 = self.tiles = 0

    def ready(self, rows: int) -> bool:
        """Whether the next strip can run once its sources hold ``rows`` rows: its own and
        the row past it that its normals read."""
        return self.r0 < self.size and rows >= min(self.r0 + PYRAMID_TILE_PX + 1, self.size)

    @property
    def written(self) -> int:
        """The rows of the level below written so far."""
        return min(self.r0, self.size) // 2

    def strip(
        self,
        pool: Executor,
        pending: deque[list[Future[int]]],
        folded: TileSet,
        ahead: int | None = None,
    ) -> None:
        """The next strip, ``LEVEL_PIECE_TILES`` tiles at a time: each piece's tiles to
        ``pool``, ``LEVEL_TASK_TILES`` to a task, while at most ``ahead`` (``LEVEL_AHEAD``)
        pieces wait in ``pending``; ``folded`` names its tiles above a tile with a folded
        band."""
        ahead = LEVEL_AHEAD if ahead is None else ahead
        if self.below is None and self.below_suffix is not None:
            self.below = _create_sources(self.work, self.below_suffix, self.size // 2)
        piece = LEVEL_PIECE_TILES * PYRAMID_TILE_PX
        for c0 in range(0, self.size, piece):
            jobs = self._piece(c0, min(c0 + piece, self.size), folded)
            self.tiles += len(jobs)
            tasks = [jobs[i : i + LEVEL_TASK_TILES] for i in range(0, len(jobs), LEVEL_TASK_TILES)]
            pending.append([pool.submit(encode_tiles, task) for task in tasks])
            del jobs, tasks
            while len(pending) > ahead:
                _wait(pending.popleft())
        self.r0 += PYRAMID_TILE_PX

    def _piece(self, c0: int, c1: int, folded: TileSet) -> list[TileJob]:
        """Columns ``[c0, c1)`` of the strip: its tile jobs, and the level below's texels."""
        src, t, r0 = self.sources, PYRAMID_TILE_PX, self.r0
        r1 = r0 + t
        nx, ny = normals(padded_window(src.z, r0 - 1, r1 + 1, c0 - 1, c1 + 1), self.spacing_m)
        svf, land = src.svf[r0:r1, c0:c1], src.land[r0:r1, c0:c1]
        nrm = np.stack([normal_byte(nx), normal_byte(ny), svf, land], -1)
        quarter = np.array(src.hzq[r0 // 2 : r1 // 2, c0 // 2 : c1 // 2])
        hz_u8 = np.moveaxis(_atlas_bytes(quarter), -1, 0)
        jobs = list(tile_jobs(self.dest, self.level, c0 // t, r0 // t, nrm, hz_u8, folded))
        below = self.below
        if below is not None:
            half = (slice(r0 // 2, r1 // 2), slice(c0 // 2, c1 // 2))
            below.z[half] = downsample(np.array(src.z[r0:r1, c0:c1]))
            below.land[half] = np.round(downsample(land.astype(np.float32)))
            below.svf[half] = np.round(downsample(svf.astype(np.float32)))
            below.hzq[r0 // 4 : r1 // 4, c0 // 4 : c1 // 4] = _coarser(quarter)
        return jobs

    def close(self) -> None:
        """The level below written out, and every map let go."""
        below, self.below = self.below, None
        if below is not None:
            for part in below:
                if isinstance(part, np.memmap):
                    part.flush()
        del below


def level_strips(
    work: Path,
    dest: Path,
    level: int,
    spacing_m: float,
    pool: Executor,
    last: bool,
    folded: TileSet = frozenset(),
) -> int:
    """One coarser level from the sources in ``work``, whole, replacing them with the next
    level's unless ``last``; ``folded`` names its tiles above a tile with a folded band."""
    built = CoarseLevel(open_sources(work), dest, level, spacing_m, work, None if last else ".next")
    pending: deque[list[Future[int]]] = deque()
    while built.ready(built.size):
        built.strip(pool, pending, folded)
    while pending:
        _wait(pending.popleft())
    tiles = built.tiles
    built.close()
    del built  # Windows replaces a file only once its last map is closed
    if not last:
        for name, _, _ in level_layout(2):
            (work / f"{name}.next.npy").replace(work / f"{name}.npy")
    return tiles


def _atlas_bytes(quarter: U8Grid) -> U8Grid:
    """A level's stored cells as its atlas holds them: the horizons encoded, the ambient
    occlusion as it is."""
    horizons, ambient = quarter[..., :HORIZON_CELLS], quarter[..., HORIZON_CELLS:]
    return np.concatenate([LINEAR_TO_HZ[horizons], ambient], -1)


def _coarser(quarter: U8Grid) -> U8Grid:
    """The next level's stored cells from a level's: the horizons refolded, ``REFOLD_ROWS``
    rows at a time, the ambient occlusion averaged."""
    out = np.empty((quarter.shape[0] // 2, quarter.shape[1] // 2, quarter.shape[2]), np.uint8)
    for top in range(0, quarter.shape[0], REFOLD_ROWS):
        part = quarter[top : top + REFOLD_ROWS, :, :HORIZON_CELLS]
        # On the CPU: the run's own process opens no CUDA context (renders.md section 41).
        folded = refold(decode_linear(part), path_elevations(), gpu=False)
        out[top // 2 : (top + part.shape[0]) // 2, :, :HORIZON_CELLS] = encode_linear(folded)
    out[..., HORIZON_CELLS:] = np.round(downsample(quarter[..., HORIZON_CELLS:].astype(np.float32)))
    return out


def _wait(futures: Sequence[Future[int]]) -> None:
    for future in futures:
        future.result()
