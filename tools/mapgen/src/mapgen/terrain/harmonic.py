"""Fills over a mask: the nearest value, the harmonic and biharmonic fills, the screened
membrane, and the hole fill that picks between them.

The lattice rebuild (``terrain.fill``), the open sea's bed and the perched water's levels all
solve with these. The numbers behind each constant are in docs/map/renders.md section 26.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from numpy.typing import NDArray
from scipy import ndimage

from mapgen.terrain.emptied import edge_labels
from mapgen.terrain.solve import jacobi_cg
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, F64Grid

__all__ = [
    "HOLE_FALLBACK_M",
    "HOLE_MAX_TEXELS",
    "HOLE_RING_TEXELS",
    "SOLVE_HALO",
    "SOLVE_TILE",
    "HoleFill",
    "fill_holes",
    "harmonic_fill",
    "nearest_fill",
    "relax",
    "tiled_harmonic",
]

#: Holes larger than this are left empty, as is anything touching the field's edge.
HOLE_MAX_TEXELS = 200_000

#: Context around a hole that the solve is anchored to, in texels.
HOLE_RING_TEXELS = 16

#: How far a biharmonic fill may leave its border's range before the harmonic one is used.
HOLE_FALLBACK_M = 2.0

#: Tiles of the band solve, and the halo each is solved with.
SOLVE_TILE = 1024
SOLVE_HALO = 96


@dataclass
class HoleFill:
    """What ``fill_holes`` did: holes and texels filled, harmonic fallbacks, worst overshoot."""

    holes: int = 0
    texels: int = 0
    harmonic_fallback: int = 0
    overshoot_max_m: float = 0.0


def nearest_fill(values: NDArray[np.floating], known: BoolMask) -> NDArray[np.floating]:
    """Every unknown texel takes its nearest known neighbour's value."""
    index = ndimage.distance_transform_edt(~known, return_distances=False, return_indices=True)
    return values[tuple(index)]


def _laplacian(active: BoolMask) -> sp.csr_matrix:
    """Graph Laplacian of the 4-neighbour grid restricted to ``active``."""
    h, w = int(active.shape[0]), int(active.shape[1])
    idx = np.arange(h * w).reshape(h, w)
    right = active[:, :-1] & active[:, 1:]
    down = active[:-1, :] & active[1:, :]
    a = np.concatenate([idx[:, :-1][right], idx[:-1, :][down]])
    b = np.concatenate([idx[:, 1:][right], idx[1:, :][down]])
    adj = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(h * w, h * w))
    adj = (adj + adj.T).tocsr()
    return (sp.diags(np.asarray(adj.sum(axis=1)).ravel()) - adj).tocsr()


def harmonic_fill(
    values: NDArray[np.floating], known: BoolMask, unknown: BoolMask, order: int
) -> F64Grid:
    """``values`` with ``unknown`` replaced by the harmonic (1) or biharmonic (2) fill.

    Known texels are Dirichlet; anything neither known nor unknown is outside the domain.
    """
    u = unknown.ravel()
    if not u.any():
        return values.astype(np.float64)
    lap = _laplacian(known | unknown)
    op = lap if order == 1 else (lap.T @ lap).tocsr()
    k = known.ravel()
    a_uu = op[u][:, u]
    rhs = -(op[u][:, k] @ values.ravel()[k].astype(np.float64))
    # A cluster with no known neighbour makes the block singular; pin it weakly.
    x = spla.spsolve((a_uu + 1e-9 * sp.eye(a_uu.shape[0])).tocsc(), rhs)
    out = values.astype(np.float64).ravel().copy()
    out[u] = x
    return out.reshape(values.shape)


def relax(
    values: NDArray[np.floating],
    known: BoolMask,
    unknown: BoolMask,
    scale: float,
    far: float,
    pull: NDArray[np.floating] | None = None,
    target: NDArray[np.floating] | None = None,
) -> F64Grid:
    """``values`` with ``unknown`` replaced by a membrane that settles towards ``far``.

    The screened Poisson equation: its border's offset from ``far`` dies away over about
    ``scale`` texels. Known texels are Dirichlet, anything else outside the domain. The
    screening keeps the system well conditioned, so conjugate gradients solve it at any size.
    ``pull`` (per texel, in 1 / texels squared) also draws it towards ``target``: a pull that
    changes from texel to texel bends the membrane's slope but never breaks it.
    """
    out = values.astype(np.float64).ravel().copy()
    u, k = unknown.ravel(), known.ravel()
    if not u.any():
        return out.reshape(values.shape)
    screen = 1.0 / (scale * scale)
    weight = np.full(int(u.sum()), screen)
    rhs = np.full(len(weight), screen * far)
    if pull is not None:
        if target is None:
            raise ValueError("a pull needs the target it pulls towards")
        weight += pull.ravel()[u]
        rhs += pull.ravel()[u] * target.ravel()[u]
    lap = _laplacian(known | unknown)
    rhs -= lap[u][:, k] @ out[k]
    a_uu = (lap[u][:, u] + sp.diags(weight)).tocsr()
    out[u] = jacobi_cg(a_uu, rhs, np.full(len(rhs), far), rtol=1e-6)
    return out.reshape(values.shape)


def tiled_harmonic(values: F32Grid, known: BoolMask, unknown: BoolMask) -> F32Grid:
    """``harmonic_fill(order=1)`` over the field in tiles with a halo, so each system stays
    small."""
    out = values.astype(np.float32).copy()
    rows, cols = values.shape
    for r in range(0, rows, SOLVE_TILE):
        for c in range(0, cols, SOLVE_TILE):
            block = (slice(r, min(r + SOLVE_TILE, rows)), slice(c, min(c + SOLVE_TILE, cols)))
            if not unknown[block].any():
                continue
            rs = slice(max(r - SOLVE_HALO, 0), min(r + SOLVE_TILE + SOLVE_HALO, rows))
            cs = slice(max(c - SOLVE_HALO, 0), min(c + SOLVE_TILE + SOLVE_HALO, cols))
            got = harmonic_fill(values[rs, cs], known[rs, cs], unknown[rs, cs], 1)
            inner = got[
                r - rs.start : block[0].stop - rs.start, c - cs.start : block[1].stop - cs.start
            ]
            mask = unknown[block]
            out[block][mask] = inner[mask]
    return out


def fill_holes(
    ground_m: NDArray[np.floating],
    known: BoolMask,
    hole_max: int = HOLE_MAX_TEXELS,
    keep_out: BoolMask | None = None,
) -> tuple[F32Grid, BoolMask, HoleFill]:
    """Biharmonic fill of every unknown component that is small and off the field's edge.

    Returns ``(filled copy, hole mask, what was filled)``. Anything else unknown stays NaN,
    and so does ``keep_out``, which bounds the solve like ground outside it.
    """
    out = ground_m.astype(np.float32).copy()
    unknown = ~known if keep_out is None else ~known & ~keep_out
    labels, count = ndimage.label(unknown)
    holes = np.zeros(unknown.shape, bool)
    stats = HoleFill()
    if not count:
        return out, holes, stats
    sizes = ndimage.sum(unknown, labels, np.arange(1, count + 1))
    border = set(edge_labels(labels).tolist())
    ring_n = HOLE_RING_TEXELS
    for label, box in enumerate(ndimage.find_objects(labels), start=1):
        if box is None or label in border or sizes[label - 1] > hole_max:
            continue
        rs = slice(max(box[0].start - ring_n, 0), min(box[0].stop + ring_n, out.shape[0]))
        cs = slice(max(box[1].start - ring_n, 0), min(box[1].stop + ring_n, out.shape[1]))
        hole = labels[rs, cs] == label
        anchor = known[rs, cs] & ndimage.binary_dilation(hole, iterations=ring_n)
        ring = ndimage.binary_dilation(hole, iterations=2) & anchor
        if not ring.any():  # walled in by a pit: nothing beside it to span
            continue
        values = np.nan_to_num(out[rs, cs]).astype(np.float64)
        got = harmonic_fill(values, anchor, hole, 2)
        lo, hi = values[ring].min(), values[ring].max()
        if got[hole].max() > hi + HOLE_FALLBACK_M or got[hole].min() < lo - HOLE_FALLBACK_M:
            got = harmonic_fill(values, anchor, hole, 1)
            stats.harmonic_fallback += 1
        over = max(float(got[hole].max() - hi), float(lo - got[hole].min()), 0.0)
        stats.overshoot_max_m = max(stats.overshoot_max_m, round(over, 3))
        out[rs, cs][hole] = got[hole]
        holes[rs, cs] |= hole
        stats.holes += 1
        stats.texels += int(hole.sum())
    return out, holes, stats
