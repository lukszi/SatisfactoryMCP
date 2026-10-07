"""Colour calibration of the game-painted style: display targets taken back to ground colour.

The tone curve, the inverse pipeline from a display sRGB target to ground OKLab, the per-layer
transfer, the targets derived by rule and the area scoping. docs/spatial-and-map.md section 31.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import TypeAlias

import numpy as np
import numpy.typing as npt
from scipy import ndimage

from mapgen.colour import (
    LUMA,
    linear_from_oklab,
    linear_to_srgb,
    oklab,
    srgb_to_linear,
    unit_luminance,
)
from mapgen.palette.painted.shapes import (
    CalibrationArea,
    CalibrationStyle,
    ColourPlanes,
    DerivedLayer,
    FloatGrid,
    PaintedPalette,
    PaintPlane,
    Sampler,
)
from satisfactory_mcp.core.arrays import BoolMask, U8Grid

__all__ = [
    "Transfer",
    "area_ids",
    "chroma_turn",
    "derived_hex",
    "display_to_crown",
    "display_to_ground",
    "display_to_linear",
    "exposure_gain",
    "flat_ground_light",
    "hex_rgb",
    "layer_transfer",
    "median_lab",
    "rehome_offshore",
    "sampled_rgb",
    "scoped_planes",
    "split_weight",
    "tone",
    "transfer_op",
    "weighted_median",
    "with_derived",
]

#: A colour transfer: the lightness step and the (a, b) matrix, chroma scale times hue turn.
Transfer: TypeAlias = tuple[float, FloatGrid]


def tone(luminance: npt.ArrayLike, knee: float, white: float) -> FloatGrid:
    """Identity below ``knee``; above it a Reinhard shoulder that takes ``white`` to 1."""
    y = np.asarray(luminance, np.float32)
    span = np.float32(1.0 - knee)
    x = np.maximum(y - knee, 0.0) / span
    top = np.float32((white - knee) / span)
    shoulder = knee + span * x * (1.0 + x / (top * top)) / (1.0 + x)
    return np.where(y > knee, shoulder, y).astype(np.float32)


def exposure_gain(palette: PaintedPalette) -> np.float32:
    """The linear gain before the tone: the style's exposure times the tone's gain."""
    return np.float32(palette["exposure"] * palette["tone"]["gain"])


def flat_ground_light(palette: PaintedPalette) -> FloatGrid:
    """The flat-ground sky-and-sun light as a colour of unit luminance per term."""
    a = np.float32(palette["ambient"])
    return a * unit_luminance(palette["sky"]) + (1 - a) * unit_luminance(palette["sun"])


def hex_rgb(hex_colour: str) -> list[int]:
    """``#rrggbb`` as its three 0..255 channels."""
    return [int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)]


def chroma_turn(turn: float | np.floating, scale: float | np.floating) -> FloatGrid:
    """The (a, b) matrix that turns the hue by ``turn`` radians and scales the chroma."""
    c, s = np.cos(turn) * scale, np.sin(turn) * scale
    return np.array([[c, -s], [s, c]], np.float32)


def display_to_linear(palette: PaintedPalette, hex_colour: str) -> FloatGrid:
    """A display sRGB colour back through the tone: the linear colour the tone maps onto it."""
    rgb = srgb_to_linear(hex_rgb(hex_colour))
    curve = palette["tone"]
    y = float(rgb @ LUMA)
    grid = np.linspace(0.0, curve["white"], 4097, dtype=np.float32)
    y0 = float(np.interp(min(y, 0.999), tone(grid, curve["knee"], curve["white"]), grid))
    return rgb * np.float32(y0 / max(y, 1e-6))


def display_to_ground(palette: PaintedPalette, hex_colour: str) -> FloatGrid:
    """A display sRGB target back through flat light, exposure, tone and chroma: OKLab."""
    rgb = display_to_linear(palette, hex_colour) / exposure_gain(palette)
    lab = oklab(rgb / flat_ground_light(palette))
    lab[0] -= np.float32(palette["altitude_lift"] * 0.5)
    lab[1:] /= np.float32(palette["chroma_gain"])
    return lab


def display_to_crown(palette: PaintedPalette, hex_colour: str) -> FloatGrid:
    """A display target as a tree crown's OKLab: the ground's, without the altitude lift."""
    lab = display_to_ground(palette, hex_colour)
    lab[0] += np.float32(palette["altitude_lift"] * 0.5)
    return lab


def transfer_op(source_lab: npt.ArrayLike, target_lab: npt.ArrayLike) -> Transfer:
    """The lightness step and (a, b) matrix, chroma scale times hue turn, source to target."""
    s, t = np.asarray(source_lab, np.float64), np.asarray(target_lab, np.float64)
    scale = np.clip(np.hypot(t[1], t[2]) / max(np.hypot(s[1], s[2]), 1e-4), 0.25, 4.0)
    turn = np.arctan2(t[2], t[1]) - np.arctan2(s[2], s[1])
    return float(t[0] - s[0]), chroma_turn(turn, scale)


def layer_transfer(
    albedo: FloatGrid,
    weights: Mapping[str, PaintPlane],
    ops: Mapping[str, Transfer],
    rows_per_block: int = 512,
) -> FloatGrid:
    """Each texel moved by its layers' ops, mixed by their normalised weights."""
    out = np.empty_like(albedo)
    for start in range(0, albedo.shape[0], rows_per_block):
        block = slice(start, start + rows_per_block)
        total = np.zeros(albedo[block].shape[:2], np.float32)
        for weight in weights.values():
            total += weight[block]
        total = np.maximum(total, np.float32(1e-6))
        lab = oklab(np.clip(albedo[block], 1e-7, None))
        d_l = np.zeros(lab.shape[:2], np.float32)
        m = np.zeros((*lab.shape[:2], 2, 2), np.float32)
        m[..., 0, 0] = m[..., 1, 1] = 1.0
        for name, (step, matrix) in ops.items():
            if name not in weights:
                continue
            w = weights[name][block] / total
            d_l += w * np.float32(step)
            m += w[..., None, None] * (matrix - np.eye(2, dtype=np.float32))
        lab[..., 0] += d_l
        lab[..., 1:] = np.einsum("...ij,...j->...i", m, lab[..., 1:])
        out[block] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return out


def median_lab(colours: FloatGrid) -> FloatGrid:
    """The per-channel OKLab median of linear colours."""
    return np.median(oklab(np.clip(colours, 1e-7, None)), axis=0)


def area_ids(area_names: Sequence[str], assets: Sequence[str], keys: Collection[str]) -> list[int]:
    """Biome raster indices whose area stem (``Area_crater``) or asset (``Area_crater_1``) is listed."""
    return [
        i
        for i, name in enumerate(area_names)
        if name in keys or (i < len(assets) and assets[i] in keys)
    ]


def rehome_offshore(index: U8Grid, names: Sequence[str], land: BoolMask, sea: str) -> U8Grid:
    """The area raster with each area's offshore pieces handed to the area they border.

    A piece of an area other than the one holding most of its land, and itself mostly sea,
    takes the named area it borders most, else ``sea``. ``land`` is a bool plane on ``index``.
    """
    out = index.copy()
    eight = np.ones((3, 3), bool)
    seas = [i for i, name in enumerate(names) if name == sea]
    for stem in sorted(set(names) - {sea}):
        ids = [i for i, name in enumerate(names) if name == stem]
        pieces, count = ndimage.label(np.isin(index, ids), eight)
        if count < 2:
            continue
        cells = np.bincount(pieces.ravel(), minlength=count + 1)[1:]
        held = np.bincount(pieces.ravel(), land.ravel().astype(np.float32), count + 1)[1:]
        main = int(np.argmax(held))
        for k, found in enumerate(ndimage.find_objects(pieces)):
            if found is None or k == main or 2 * held[k] >= cells[k]:
                continue
            box = tuple(slice(max(s.start - 1, 0), s.stop + 1) for s in found)
            piece = pieces[box] == k + 1
            ring = ndimage.binary_dilation(piece, eight) & ~piece
            border = np.bincount(index[box][ring], minlength=len(names))[: len(names)]
            border[ids] = 0
            named = border.copy()
            named[seas] = 0
            pick = named if named.any() else border
            if pick.any():
                out[box][piece] = np.argmax(pick)
    return out


def split_weight(weight: PaintPlane, share_u8: U8Grid) -> tuple[U8Grid, PaintPlane]:
    """A u8 layer weight cut in two by a 0..255 share: ``(inside, outside)``, summing to it."""
    inside = ((weight.astype(np.uint16) * share_u8 + 127) // 255).astype(np.uint8)
    return inside, weight - inside


def derived_hex(hex_colour: str, rule: DerivedLayer) -> str:
    """A display colour moved in its OKLab by ``rule``: the lightness times ``lightness``, the
    chroma times ``chroma``, the hue turned by ``hue_deg``."""
    lab = oklab(srgb_to_linear(hex_rgb(hex_colour)))
    turn = chroma_turn(np.radians(np.float32(rule.get("hue_deg", 0.0))), rule.get("chroma", 1.0))
    a, b = lab[1], lab[2]
    moved = np.array(
        [
            lab[0] * rule.get("lightness", 1.0),
            turn[0, 0] * a + turn[0, 1] * b,
            turn[1, 0] * a + turn[1, 1] * b,
        ]
    )
    rgb = np.round(linear_to_srgb(np.clip(linear_from_oklab(moved), 0.0, 1.0))).astype(int)
    return "#" + "".join(f"{v:02x}" for v in rgb)


def with_derived(cal: CalibrationStyle) -> CalibrationStyle:
    """The calibration with each ``derived`` rule's target added to every layer scope where its
    ``from`` layer has one and it has none."""
    rules = cal.get("derived", {})

    def scope(layers: dict[str, str]) -> dict[str, str]:
        found = {name: derived_hex(layers[rule["from"]], rule) for name, rule in rules.items()
                 if rule["from"] in layers and name not in layers}  # fmt: skip
        return {**layers, **found}

    areas: list[CalibrationArea] = [{**e, "layers": scope(e["layers"])} if "layers" in e else e
                               for e in cal.get("areas", [])]  # fmt: skip
    return {**cal, "layers": scope(cal.get("layers", {})), "areas": areas}


def weighted_median(values: FloatGrid, weights: FloatGrid) -> FloatGrid:
    """The per-column median of ``values`` (n, k), each row counted by its weight."""
    out = np.empty(values.shape[1], np.float32)
    for k in range(values.shape[1]):
        order = np.argsort(values[:, k])
        cum = np.cumsum(weights[order])
        out[k] = values[order[np.searchsorted(cum, 0.5 * cum[-1])], k]
    return out


def scoped_planes(
    default: FloatGrid, scoped: Sequence[tuple[FloatGrid, FloatGrid]]
) -> ColourPlanes:
    """``default`` where no area entry reaches, each entry's value by its weight: one plane
    per component of ``default``."""
    if not scoped:
        return default
    total = np.zeros(scoped[0][0].shape, np.float32)
    for weight, _ in scoped:
        total += weight
    norm = np.maximum(total, 1.0)
    planes: list[FloatGrid] = []
    for k in range(len(default)):
        plane = np.float32(default[k]) * (1.0 - np.minimum(total, 1.0))
        for weight, rgb in scoped:
            plane = plane + weight / norm * np.float32(rgb[k])
        planes.append(plane.astype(np.float32))
    return planes


def sampled_rgb(value: ColourPlanes, sample_rock: Sampler | None) -> FloatGrid:
    """A constant colour, or three coarse planes sampled onto the band."""
    if not isinstance(value, list):
        return value
    if sample_rock is None:
        raise ValueError("coarse colour planes need the rock grid's sampler")
    return np.stack([sample_rock(plane) for plane in value], -1)
