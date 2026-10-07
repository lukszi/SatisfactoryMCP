"""The paint layers moved onto their calibrated targets, texel by texel, as a blend that keeps
each layer's step to that layer's colour: no step widens chroma, two calibrated layers on one
texel share it by where its colour lies between theirs, and it lands between their targets.
docs/map/calibration.md section 31, "The blend at a layer's edge".
"""

from __future__ import annotations

from collections.abc import Mapping
from itertools import combinations
from typing import NamedTuple

import numpy as np
import numpy.typing as npt

from mapgen.colour import linear_from_oklab, oklab
from mapgen.palette.painted.calibration import transfer_op, turned
from mapgen.palette.painted.shapes import FloatGrid, PaintPlane

__all__ = ["GREY_SOURCE_CHROMA", "LayerOp", "by_colour", "layer_op", "layer_transfer"]

#: A source median under this chroma has no hue to turn: its step is a shift alone.
GREY_SOURCE_CHROMA = 0.02


class LayerOp(NamedTuple):
    """One layer's step as an affine map: ``L + step``, and ``(a, b)`` through ``matrix`` then
    ``+ shift``; ``source`` is the OKLab median it was measured on."""

    step: float
    matrix: FloatGrid
    shift: FloatGrid
    source: FloatGrid


def layer_op(source_lab: npt.ArrayLike, target_lab: npt.ArrayLike) -> LayerOp:
    """``transfer_op``, never widening chroma: a step that would scale it past one turns the
    hue only and adds what it lacks as a shift, so the source still lands on the target;
    around a grey source the step is that shift alone. A narrowing step has no shift."""
    step, matrix = transfer_op(source_lab, target_lab)
    source = np.asarray(source_lab, np.float32)
    s = source[1:]
    scale = float(np.hypot(*matrix[0]))
    if scale <= 1.0:
        return LayerOp(step, matrix, np.zeros(2, np.float32), source)
    if float(np.hypot(*s)) < GREY_SOURCE_CHROMA:
        matrix = np.eye(2, dtype=np.float32)
    else:
        matrix = (matrix / np.float32(scale)).astype(np.float32)
    moved = matrix[:, 0] * s[0] + matrix[:, 1] * s[1]
    shift = (np.asarray(target_lab, np.float32)[1:] - moved).astype(np.float32)
    return LayerOp(step, matrix, shift, source)


def _layer(key: str) -> str:
    """An op's paint layer: its key before any ``@area`` scope."""
    return key.partition("@")[0]


def _pair_terms(ops: Mapping[str, LayerOp]) -> list[tuple[str, str, FloatGrid]]:
    """For each two ops of different layers, ``(A_i - A_j)(s_i - s_j)``: what their blend
    misses when one texel carries both."""
    terms: list[tuple[str, str, FloatGrid]] = []
    for (i, a), (j, b) in combinations(ops.items(), 2):
        if _layer(i) == _layer(j):
            continue
        m, d = a.matrix - b.matrix, a.source[1:] - b.source[1:]
        terms.append((i, j, (m[:, 0] * d[0] + m[:, 1] * d[1]).astype(np.float32)))
    return terms


def layer_transfer(
    albedo: FloatGrid,
    weights: Mapping[str, PaintPlane],
    ops: Mapping[str, LayerOp],
    rows_per_block: int = 512,
) -> FloatGrid:
    """Each texel moved by its layers' ops, mixed by their shares.

    A layer's share is its normalised weight. Where calibrated layers of two paint layers
    meet, their summed share is split again by where the texel's colour lies between their
    medians (``by_colour``), and for each two of them
    ``w_i w_j / W (A_i - A_j)(s_i - s_j)`` is added, ``W`` their summed share: the texel lands
    on ``sum w t`` plus the blended matrix on its departure from ``sum w s``, between the
    targets, and is not the one layer's colour pushed by the other layer's step.
    """
    ops = {name: op for name, op in ops.items() if name in weights}
    pairs = _pair_terms(ops)
    eye = np.eye(2, dtype=np.float32)
    out = np.empty_like(albedo)
    for start in range(0, albedo.shape[0], rows_per_block):
        block = slice(start, start + rows_per_block)
        total = np.zeros(albedo[block].shape[:2], np.float32)
        for weight in weights.values():
            total += weight[block]
        total = np.maximum(total, np.float32(1e-6))
        lab = oklab(np.clip(albedo[block], 1e-7, None))
        share = by_colour(lab, {name: weights[name][block] / total for name in ops}, ops)
        d_l = np.zeros(lab.shape[:2], np.float32)
        m = np.zeros((*lab.shape[:2], 2, 2), np.float32)
        m[..., 0, 0] = m[..., 1, 1] = 1.0
        shift = np.zeros((*lab.shape[:2], 2), np.float32)
        shifted = False
        for name, op in ops.items():
            w = share[name]
            d_l += w * np.float32(op.step)
            m += w[..., None, None] * (op.matrix - eye)
            if op.shift.any():
                shift += w[..., None] * op.shift
                shifted = True
        if pairs:
            shifted = _add_pair_terms(shift, share, pairs) or shifted
        lab[..., 0] += d_l
        lab[..., 1:] = turned(lab[..., 1], lab[..., 2], m)
        if shifted:
            lab[..., 1:] += shift
        out[block] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return out


def by_colour(
    lab: FloatGrid, share: Mapping[str, FloatGrid], ops: Mapping[str, LayerOp]
) -> dict[str, FloatGrid]:
    """The shares of a texel's two largest calibrated paint layers, split again by where its
    colour lies between their source medians, ``u`` (0 at the second's, 1 at the first's,
    clipped): the bake draws a forest patch's edge sharper than the weights blur it, so a
    texel with the sand's colour and half a forest floor's weight is sand. The colour decides
    by ``4 w_i w_j / (w_i + w_j)^2``: all of it where the weights are even, none where one
    of them fades out, so the shares stay continuous. Their sum stays the same, and a texel
    of one calibrated layer, or of scopes of one layer, keeps its shares exactly."""
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


def _add_pair_terms(
    shift: FloatGrid, share: Mapping[str, FloatGrid], pairs: list[tuple[str, str, FloatGrid]]
) -> bool:
    """Adds the pair terms into ``shift`` where both layers of a pair are present; whether
    any was."""
    held = {name: bool(w.any()) for name, w in share.items()}
    present = [(i, j, term) for i, j, term in pairs if held[i] and held[j]]
    if not present:
        return False
    summed = np.zeros(shift.shape[:2], np.float32)
    for w in share.values():
        summed += w
    summed = np.maximum(summed, np.float32(1e-6))
    for i, j, term in present:
        shift += (share[i] * share[j] / summed)[..., None] * term
    return True
