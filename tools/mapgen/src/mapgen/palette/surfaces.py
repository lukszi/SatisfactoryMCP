"""What stands on the painted ground: rock in its family's colour, the canopy over rock, and
the render-only meshes. docs/spatial-and-map.md sections 27, 30 and 31.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.gamedata.rockfamily import FAMILIES
from mapgen.palette.calibration import display_to_ground, sampled_rgb
from mapgen.palette.colour import linear_from_oklab
from mapgen.terrain.rasters import MESH_CORAL, MESH_ROCK
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "SPECK_WATER",
    "canopy_over_rock",
    "family_cells",
    "family_tables",
    "family_targets",
    "mesh_surface",
    "patch_noise",
    "rock_surface",
    "sunk_specks",
    "top_cover",
    "top_targets",
]

#: A coral pixel is narrower than the pixel when at least this share of its eight neighbours
#: is water: a coral head standing in the sea, which the max-Z raster widens to a pixel.
SPECK_WATER = 0.6

_MASK64 = (1 << 64) - 1
_PRIMES = (0x9E3779B97F4A7C15, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9)
_FMIX = (np.uint64(0xFF51AFD7ED558CCD), np.uint64(0xC4CEB9FE1A85EC53))


def family_tables(
    families: dict, palette: dict | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """By family code: the tint relative to the families' median, the top layer, and whether
    there is one. With ``palette``, its ``calibration.tops`` replace their families' tops
    (``top_targets``).

    The rock targets are calibrated on rock that already wears the common tint, so only a
    family's departure from it is applied; with one tint for all, rock stays on target.
    """
    n = len(FAMILIES)
    tint = np.ones((n, 3), np.float32)
    top = np.zeros((n, 3), np.float32)
    has_top = np.zeros(n, np.float32)
    tinted = []
    for name, entry in families.items():
        if name not in FAMILIES:
            continue
        code = FAMILIES.index(name)
        if entry.get("tint"):
            tint[code] = entry["tint"]
            tinted.append(code)
        if entry.get("top"):
            top[code] = entry["top"]
            has_top[code] = 1.0
    if tinted:
        tint[tinted] /= np.maximum(np.median(tint[tinted], axis=0), np.float32(1e-6))
    if palette is not None:
        top = top_targets(top, palette["calibration"].get("tops", {}), palette)
    return tint, top, has_top


def family_cells(plane, shape: tuple[int, int], step_m: float) -> np.ndarray:
    """The family plane's code at each point of a ``step_m`` grid from the frame's corner: the
    code of the pixel the point falls in."""
    span_m = BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]
    picks = [
        np.clip(np.floor(np.arange(n) * step_m * size / span_m), 0, size - 1).astype(int)
        for n, size in zip(shape, plane.shape, strict=True)
    ]
    return np.asarray(plane[picks[0]][:, picks[1]])


def family_targets(base_lab, codes, targets: dict, palette: dict, min_cells: int):
    """Per family with a target, its rock on the rock grid, and the step measured for it.

    ``base_lab`` is the rock grid in OKLab before any target, ``codes`` the family under each
    cell. As for an area target, chroma and hue become the target's and the lightness moves
    by the step from the median of the family's own cells to the target's.
    """
    planes, measured = {}, {}
    for name, hex_colour in targets.items():
        own = codes == FAMILIES.index(name)
        if own.sum() < min_cells:
            continue
        target = display_to_ground(palette, hex_colour)
        lab = base_lab.copy()
        step = float(target[0] - np.median(base_lab[own][:, 0]))
        lab[..., 0] += np.float32(step)
        lab[..., 1:] = target[1:]
        rgb = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        planes[FAMILIES.index(name)] = [rgb[..., k].astype(np.float32) for k in range(3)]
        measured[name] = {"cells": int(own.sum()), "dL": round(step, 4)}
    return planes, measured


def top_targets(top, targets: dict, palette: dict) -> np.ndarray:
    """The top layer table with each named family's display target, as ground colour, in
    place of its texture's mean."""
    top = np.array(top, np.float32)
    for name, hex_colour in targets.items():
        lab = display_to_ground(palette, hex_colour)
        top[FAMILIES.index(name)] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return top


def _lattice(i, j, seed: int) -> np.ndarray:
    """A value in [0, 1) per integer lattice point: a hash of the point and ``seed``."""
    mixed = (seed * _PRIMES[2]) & _MASK64
    h = i.astype(np.uint64) * np.uint64(_PRIMES[0]) + j.astype(np.uint64) * np.uint64(_PRIMES[1])
    h = h ^ np.uint64(mixed)
    for mult in _FMIX:
        h = (h ^ (h >> np.uint64(33))) * mult
    h = h ^ (h >> np.uint64(33))
    return (h >> np.uint64(40)).astype(np.float32) / np.float32(1 << 24)


def patch_noise(x_m, y_m, octaves, seed: int) -> np.ndarray:
    """Value noise in [0, 1] at points in metres from the frame's corner: per octave
    ``(wavelength m, amount)`` a hashed lattice blended by smoothstep, mixed by amount. The
    lattice is hashed once over the points' extent and its corners gathered from it."""
    x_m, y_m = np.broadcast_arrays(np.asarray(x_m, np.float64), np.asarray(y_m, np.float64))
    total = np.zeros(x_m.shape, np.float32)
    if not x_m.size:
        return total
    for k, (wavelength, amount) in enumerate(octaves):
        u, v = x_m / wavelength, y_m / wavelength
        i, j = np.floor(u), np.floor(v)
        su, sv = (t * t * (3.0 - 2.0 * t) for t in (u - i, v - j))
        i, j = i.astype(np.int64), j.astype(np.int64)
        i0, j0 = int(i.min()), int(j.min())
        width = int(i.max()) - i0 + 2
        cols, rows = np.arange(i0, i0 + width), np.arange(j0, int(j.max()) + 2)
        table = _lattice(cols[None, :], rows[:, None], seed + k).ravel()
        at = (j - j0) * width + (i - i0)
        corner = [table[at + offset] for offset in (0, 1, width, width + 1)]
        near = corner[0] + (corner[1] - corner[0]) * su
        far = corner[2] + (corner[3] - corner[2]) * su
        total += np.float32(amount) * (near + (far - near) * sv).astype(np.float32)
    return total / np.float32(sum(amount for _w, amount in octaves))


def _ramp(nz, lo_hi) -> np.ndarray:
    lo, hi = lo_hi
    return ndimage.uniform_filter(np.clip((nz - lo) / (hi - lo), 0.0, 1.0), 3)


def top_cover(scene: dict, ground, code) -> np.ndarray:
    """The top layer's weight per pixel: the up-facing faces of a family with a top, and with
    ``rock_top.patches`` only in patches, more of them the flatter the face. The patches are
    ``patch_noise`` at each pixel's centre, so a point draws the same at any size or band."""
    _band, lo, _hi, c0, _c1, spacing_m = scene["grid"]
    d_south, d_east = np.gradient(scene["z_m"], spacing_m)
    nz = 1.0 / np.sqrt(1.0 + d_east * d_east + d_south * d_south)
    rule = ground.palette["rock_top"]
    weight = _ramp(nz, rule["up"]) * ground.family_has_top[code]
    patches = rule.get("patches")
    seen = weight > 0.0
    if patches is None or not seen.any():
        return weight
    rows, cols = np.nonzero(seen)
    noise = patch_noise((c0 + cols + 0.5) * spacing_m, (lo + rows + 0.5) * spacing_m,
                        patches["octaves_m"], patches["seed"])  # fmt: skip
    level = noise + patches["flat_gain"] * (_ramp(nz, patches["flat"])[seen] - 1.0)
    weight[seen] *= np.clip((level - patches["level"]) / patches["soft"] + 0.5, 0.0, 1.0)
    return weight


def rock_surface(rock_rgb, scene: dict, ground, sample_rock=None, code=None) -> np.ndarray:
    """Rock in its family's colour: the family's own target where it has one, else the area's
    rock in the family's tint, with the family's top layer on its up-facing faces (``top_cover``).
    ``code`` is the family per pixel; the direct pass's family plane on this band when None."""
    if code is None and ground.rock_family is None:
        return rock_rgb
    band = scene["grid"][0]
    code = np.asarray(ground.rock_family[band] if code is None else code)
    for which, planes in getattr(ground, "family_rock", {}).items():
        hit = (code == which)[..., None]
        if hit.any():
            rock_rgb = np.where(hit, sampled_rgb(planes, sample_rock), rock_rgb)
    rgb = rock_rgb * ground.family_tint[code]
    weight = top_cover(scene, ground, code)[..., None]
    return rgb * (1.0 - weight) + ground.family_top[code] * weight


def canopy_over_rock(g, canopy, rock, scene: dict, ground, sample, rgb=None):
    """The canopy laid over rock wherever the drawn surface is no higher than a crown top.

    ``rgb`` is the canopy colour already sampled onto the band; the ground's constant if None.
    """
    if ground.crown is None:
        return g
    crown_m = sample(ground.crown) / np.float32(hf.DM_PER_M)
    seen = (scene["z_m"] <= crown_m)[..., None]
    cover = canopy * rock * seen
    return g * (1.0 - cover) + (ground.canopy_rgb if rgb is None else rgb) * cover


def sunk_specks(scene: dict) -> dict:
    """The band's water with each coral speck standing in it drawn as that water.

    A speck is a coral pixel whose neighbours are mostly water: it takes their mean depth and
    full water cover, so the coral is seen as the bed under the water around it, as the
    seabed carpet is. Larger coral, and coral on land, keeps its own surface.
    """
    water = scene["water"]
    mesh_w = scene.get("mesh_weight")
    if mesh_w is None:
        return water
    coral = (scene["mesh_class"] == MESH_CORAL) & (mesh_w > 0)
    if not coral.any():
        return water
    cover = water["cover"]
    around = (ndimage.uniform_filter(cover, 3, mode="nearest") * 9 - cover) / 8
    speck = coral & (around >= SPECK_WATER)
    if not speck.any():
        return water
    wet_depth = cover * water["depth_m"]
    deep = (ndimage.uniform_filter(wet_depth, 3, mode="nearest") * 9 - wet_depth) / 8
    return {
        **water,
        "cover": np.where(speck, np.float32(1.0), cover),
        "depth_m": np.where(speck, deep / np.maximum(around, 1e-6), water["depth_m"]),
    }


def mesh_surface(g, area_rock, scene: dict, ground, sample_rock):
    """The render-only meshes over ``g``, each class in its own colour.

    A rock wears its own family (``scene["mesh_family"]``) and that family's top layer, as a
    cliff does, never the family of a cliff it happens to overlap; without the plane it takes
    the area's rock. Coral under water is the seabed coral; ``scene["water"]`` is the water
    after ``sunk_specks``.
    """
    mesh_w = scene.get("mesh_weight")
    if mesh_w is None or not mesh_w.any():
        return g
    cls, family = scene["mesh_class"], scene.get("mesh_family")
    colour = area_rock
    if family is not None and (cls == MESH_ROCK).any():
        colour = rock_surface(area_rock, scene, ground, sample_rock, family)
    for which, rgb in ground.mesh_rgb.items():
        colour = np.where((cls == which)[..., None], sampled_rgb(rgb, sample_rock), colour)
    wet = np.where(cls == MESH_CORAL, np.clip(scene["water"]["cover"], 0.0, 1.0), 0.0)
    colour = colour + (ground.seabed_coral - colour) * wet[..., None]
    return g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]
