"""The game-painted satellite style: the landscape's own paint, lit and coloured in linear light.

Ground colour is the game's baked ground colour where a ``GroundBake`` is given, else the
paint-layer weights of ``data/local/paint/`` times each layer's albedo, tinted by the
PigmentMap; a per-layer colour transfer to calibrated targets; under the tree canopy; rocks,
arches and the render-only meshes take their own colours; then a sky-and-sun light, an
exposure gain with a soft shoulder and Beer-Lambert water. Every number is in
``palette/palettes/satellite-painted.json``. docs/spatial-and-map.md sections 27 and 28.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from mapgen.gamedata.paint import CANOPY_NAME, META_NAME, PIGMENT_NAME
from mapgen.palette.shore import add_foam, wet_band
from mapgen.terrain.rasters import MESH_CORAL, MESH_SHELL
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "ROCK_GRID_M",
    "GroundBake",
    "PaintedGround",
    "biome_grid",
    "display_to_ground",
    "dry_land_range",
    "ground_albedo",
    "layer_table",
    "layer_transfer",
    "linear_from_oklab",
    "linear_to_srgb",
    "load_paint_meta",
    "mix_layers",
    "oklab",
    "painted_colours",
    "ramp_position",
    "seam_blend",
    "srgb_to_linear",
    "tone",
    "transfer_op",
]

#: OKLab, Björn Ottosson's matrices.
_M1 = np.array(
    [
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005],
    ],
    np.float32,
)
_M2 = np.array(
    [
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660],
    ],
    np.float32,
)
_M1_INV = np.linalg.inv(_M1).astype(np.float32)
_M2_INV = np.linalg.inv(_M2).astype(np.float32)

#: The rock colour's grid, coarser than the paint: it is a 25 m blur of it.
ROCK_GRID_M = 4

_LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)


def srgb_to_linear(values) -> np.ndarray:
    c = np.asarray(values, np.float32) / np.float32(255.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(values) -> np.ndarray:
    c = np.clip(values, 0.0, 1.0)
    return (
        np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055) * 255.0
    ).astype(np.float32)


def oklab(linear) -> np.ndarray:
    return np.cbrt(np.asarray(linear, np.float32) @ _M1.T) @ _M2.T


def linear_from_oklab(lab) -> np.ndarray:
    return (np.asarray(lab, np.float32) @ _M2_INV.T) ** 3 @ _M1_INV.T


def ramp_position(height_m, lo_m: float, hi_m: float, cdf_m: np.ndarray, equalised: float):
    """Ground height to [0, 1] over dry land: part linear in metres, part equal-area."""
    linear = np.clip((height_m - lo_m) / max(hi_m - lo_m, 1e-6), 0.0, 1.0)
    quantile = np.interp(height_m, cdf_m, np.linspace(0.0, 1.0, len(cdf_m)))
    return ((1.0 - equalised) * linear + equalised * quantile).astype(np.float32)


def dry_land_range(field, lo_pct: float, hi_pct: float) -> tuple[float, float, np.ndarray]:
    """The ramp's height range and CDF, over dry land only (waterq dry, height known)."""
    height = field._height_dm[::4, ::4]
    grades = field._water_quality_raster()
    dry = height != hf.NODATA
    if grades is not None:
        dry &= grades[::4, ::4] == hf.WATER_DRY
    metres = height[dry].astype(np.float32) / hf.DM_PER_M
    lo, hi = np.percentile(metres, [lo_pct, hi_pct])
    cdf = np.percentile(metres[(metres >= lo) & (metres <= hi)], np.linspace(0, 100, 101))
    return float(lo), float(hi), cdf.astype(np.float32)


def load_paint_meta(paint_dir: Path) -> dict | None:
    try:
        return json.loads((paint_dir / META_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _plane(paint_dir: Path, meta: dict, name: str) -> np.ndarray:
    shape = meta["files"][name]["shape"]
    flat_width = shape[1] * (shape[2] if len(shape) > 2 else 1)
    grid = hf.decode_u8((paint_dir / name).read_bytes(), shape[0], flat_width)
    return grid.reshape(shape)


def layer_table(meta: dict, palette: dict) -> dict[str, np.ndarray]:
    """Linear albedo per paint layer, with the palette's WetSand correction applied."""
    darkening = np.float32(palette["albedo_darkening"])
    table = {
        name: np.asarray(value, np.float32) * darkening
        for name, value in meta["albedo_linear"]["layers"].items()
    }
    wet, sand = table.get("WetSand_LayerInfo"), table.get("Sand_LayerInfo")
    if wet is not None and sand is not None:
        lab = oklab(wet)
        lab[0] = oklab(sand)[0] * np.float32(palette["wet_sand_lightness_of_sand"])
        table["WetSand_LayerInfo"] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
    return table


def mix_layers(weights: dict[str, np.ndarray], table: dict[str, np.ndarray], shape) -> tuple:
    """Weight-normalised mix of the layers' albedos; and where any layer was painted."""
    acc = np.zeros((*shape, 3), np.float32)
    total = np.zeros(shape, np.float32)
    for name, weight in weights.items():
        if name not in table:
            continue
        w = weight.astype(np.float32) / np.float32(255.0)
        acc += w[..., None] * table[name]
        total += w
    have = total > 0
    return acc / np.maximum(total, 1e-6)[..., None], have


def seam_blend(rgb: np.ndarray, origins, size_px: int, palette: dict) -> tuple[np.ndarray, int]:
    """Soften paint steps that sit exactly on landscape-component edges.

    A component painted solid with one layer meets its neighbour in a straight 127 m line. The
    jump across every component edge is measured; where it is a step rather than a gradient,
    the colour is blended towards its own blur over ``seam_blend_m``.
    """
    rows, cols = rgb.shape[:2]
    edge = np.zeros((rows, cols), bool)
    for row, col in origins:
        r0, r1 = max(row, 0), min(row + size_px, rows)
        c0, c1 = max(col, 0), min(col + size_px, cols)
        if r0 >= r1 or c0 >= c1:
            continue
        for r in (row, row + size_px - 1):
            if 0 <= r < rows:
                edge[r, c0:c1] = True
        for c in (col, col + size_px - 1):
            if 0 <= c < cols:
                edge[r0:r1, c] = True
    tone = np.sqrt(np.clip(rgb, 0.0, 1.0))
    jump = np.zeros((rows, cols), np.float32)
    jump[:, 1:-1] = np.abs(tone[:, 2:] - tone[:, :-2]).max(-1)
    jump[1:-1, :] = np.maximum(jump[1:-1, :], np.abs(tone[2:] - tone[:-2]).max(-1))
    lo, hi = palette["seam_jump"]
    step = np.where(edge, np.clip((jump - lo) / (hi - lo), 0.0, 1.0), 0.0).astype(np.float32)
    sigma = float(palette["seam_blend_m"])
    weight = np.clip(ndimage.gaussian_filter(step, sigma) * np.sqrt(2 * np.pi) * sigma, 0.0, 1.0)
    out = rgb.copy()
    for k in range(3):
        soft = ndimage.gaussian_filter(rgb[..., k], sigma)
        out[..., k] = rgb[..., k] * (1.0 - weight) + soft * weight
    return out, int((step > 0).sum())


def biome_grid(biome: dict, rows: int, cols: int) -> np.ndarray:
    """The biome raster's index under every texel of the 1 m grid. Nearest."""
    width = biome["width"]
    r = np.clip(((np.arange(rows) + 0.5) * width / rows).astype(np.int64), 0, width - 1)
    c = np.clip(((np.arange(cols) + 0.5) * width / cols).astype(np.int64), 0, width - 1)
    return biome["area"][np.ix_(r, c)]


@dataclass(frozen=True)
class GroundBake:
    """The game's baked ground colour on the paint grid: linear RGB and where it exists."""

    linear: np.ndarray
    have: np.ndarray

    @classmethod
    def from_srgb(cls, rgb, have) -> GroundBake:
        """From 8-bit sRGB; black texels inside ``have`` are holes in the bake, not ground."""
        rgb = np.asarray(rgb)
        linear = np.empty(rgb.shape, np.float32)
        for k in range(3):
            linear[..., k] = srgb_to_linear(rgb[..., k])
        return cls(linear, np.asarray(have, bool) & (rgb.astype(np.uint16).sum(-1) >= 3))


def ground_albedo(paint, have, bake: GroundBake | None, feather_m: float) -> tuple:
    """The ground albedo source: the bake where it exists, else the paint mix.

    Returns ``(albedo, have, bake_weight)``; ``bake_weight`` is None without a bake.
    """
    if bake is None:
        return paint, have, None
    inside = bake.have.astype(np.float32)
    weight = np.clip(ndimage.gaussian_filter(inside, feather_m) * 2.0 - 1.0, 0.0, 1.0) * inside
    w = weight[..., None]
    return paint * (1.0 - w) + bake.linear.astype(np.float32) * w, have | bake.have, weight


def tone(luminance, knee: float, white: float):
    """Identity below ``knee``; above it a Reinhard shoulder that takes ``white`` to 1."""
    y = np.asarray(luminance, np.float32)
    span = np.float32(1.0 - knee)
    x = np.maximum(y - knee, 0.0) / span
    top = np.float32((white - knee) / span)
    shoulder = knee + span * x * (1.0 + x / (top * top)) / (1.0 + x)
    return np.where(y > knee, shoulder, y).astype(np.float32)


def _flat_light(p: dict) -> np.ndarray:
    a = np.float32(p["ambient"])
    return a * _unit_luminance(p["sky"]) + (1 - a) * _unit_luminance(p["sun"])


def display_to_ground(p: dict, hex_colour: str) -> np.ndarray:
    """A display sRGB target back through flat light, exposure, tone and chroma: OKLab."""
    rgb = srgb_to_linear([int(hex_colour[i : i + 2], 16) for i in (1, 3, 5)])
    t = p["tone"]
    y = float(rgb @ _LUMA)
    grid = np.linspace(0.0, t["white"], 4097, dtype=np.float32)
    y0 = float(np.interp(min(y, 0.999), tone(grid, t["knee"], t["white"]), grid))
    rgb = rgb * np.float32(y0 / max(y, 1e-6)) / np.float32(p["exposure"] * t["gain"])
    lab = oklab(rgb / _flat_light(p))
    lab[0] -= np.float32(p["altitude_lift"] * 0.5)
    lab[1:] /= np.float32(p["chroma_gain"])
    return lab


def transfer_op(source_lab, target_lab) -> tuple[float, np.ndarray]:
    """The lightness step and (a, b) matrix, chroma scale times hue turn, source to target."""
    s, t = np.asarray(source_lab, np.float64), np.asarray(target_lab, np.float64)
    scale = np.clip(np.hypot(*t[1:]) / max(np.hypot(*s[1:]), 1e-4), 0.25, 4.0)
    turn = np.arctan2(t[2], t[1]) - np.arctan2(s[2], s[1])
    c, si = np.cos(turn) * scale, np.sin(turn) * scale
    return float(t[0] - s[0]), np.array([[c, -si], [si, c]], np.float32)


def layer_transfer(albedo, weights: dict, ops: dict, rows_per_block: int = 512) -> np.ndarray:
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


def _median_lab(colours: np.ndarray) -> np.ndarray:
    return np.median(oklab(np.clip(colours, 1e-7, None)), axis=0)


class PaintedGround:
    """Everything the painted style samples per band, built once from the paint store."""

    def __init__(
        self,
        paint_dir: Path,
        palette: dict,
        field,
        biome: dict,
        area_names: list[str],
        bake: GroundBake | None = None,
    ):
        meta = load_paint_meta(paint_dir)
        if meta is None:
            raise FileNotFoundError(f"no paint store at {paint_dir}")
        self.meta, self.palette = meta, palette
        grid = meta["grid"]
        rows, cols = grid["height"], grid["width"]
        table = layer_table(meta, palette)
        weights = {
            entry["layer"]: _plane(paint_dir, meta, name)
            for name, entry in meta["files"].items()
            if "layer" in entry
        }
        albedo, have = mix_layers(weights, table, (rows, cols))
        darkening = np.float32(palette["albedo_darkening"])
        for name, value in meta["albedo_linear"]["overlays"].items():
            if name in weights:
                w = (weights[name].astype(np.float32) / 255.0)[..., None]
                albedo = albedo * (1.0 - w) + np.asarray(value, np.float32) * darkening * w
        albedo = self._pigment(paint_dir, albedo, rows, cols)
        albedo, self.seam_texels = seam_blend(
            albedo, meta["components"], meta["component_px"], palette
        )
        albedo, have, self.bake_weight = ground_albedo(albedo, have, bake, palette["have_blur_m"])
        self.albedo_source = "paint" if bake is None else "bake"
        albedo = self._calibrate(albedo, weights)
        del weights
        index = biome_grid(biome, rows, cols)
        albedo = self._fallback(albedo, have, index, len(area_names))
        albedo = self._biome_tint(albedo, index, area_names)
        self.albedo = [albedo[..., k].astype(np.float16) for k in range(3)]
        self.rock = self._rock(albedo, index, area_names)
        del albedo
        self.canopy = _plane(paint_dir, meta, CANOPY_NAME)
        self.canopy_rgb = np.asarray(meta["albedo_linear"]["canopy"], np.float32) * np.float32(
            palette["canopy_dark"]
        )
        colours = {k: srgb_to_linear(v) for k, v in palette["mesh_colours"].items()}
        targets = palette["calibration"]
        if "canopy" in targets:
            self.canopy_rgb = self._target(targets["canopy"])
        for name, hex_colour in targets.get("meshes", {}).items():
            colours[name] = self._target(hex_colour)
        self.mesh_rgb = {MESH_CORAL: colours["coral"], MESH_SHELL: colours["shell"]}
        water = palette["water"]
        self.seabed_coral = colours["coral_seabed"] / np.float32(water["bed_wet"])
        self.water = {
            "k": np.asarray(water["k_per_m"], np.float32),
            "body": srgb_to_linear(water["body"]),
            "sky": srgb_to_linear(water["sky"]) * np.float32(water["surface_r"]),
            "deep": srgb_to_linear(water["deep"]),
            "deep_tau_m": np.float32(water["deep_tau_m"]),
            "bed": np.float32(water["bed_wet"]),
        }
        lo, hi, cdf = dry_land_range(field, palette["ramp_lo_pct"], palette["ramp_hi_pct"])
        self.ramp = (lo, hi, cdf)

    def _pigment(self, paint_dir, albedo, rows, cols):
        strength = np.float32(self.palette["pigment"])
        if not strength:
            return albedo
        texture = srgb_to_linear(_plane(paint_dir, self.meta, PIGMENT_NAME))
        side = texture.shape[0]
        coords = (np.arange(rows, dtype=np.float32) + 0.5) * side / rows - 0.5
        rr, cc = np.meshgrid(
            coords, (np.arange(cols, dtype=np.float32) + 0.5) * side / cols - 0.5, indexing="ij"
        )
        for k in range(3):
            tint = ndimage.map_coordinates(texture[..., k], [rr, cc], order=1, mode="nearest")
            albedo[..., k] *= (1.0 - strength) + strength * tint
        return albedo

    def _fallback(self, albedo, have, index, areas: int):
        """Off the landscape: the median paint of the biome, blurred, faded in over the edge."""
        p = self.palette
        sample = (slice(None, None, 4), slice(None, None, 4))
        idx, ok = index[sample], have[sample]
        flat = albedo[sample]
        global_median = np.median(flat[ok], axis=0) if ok.any() else np.full(3, 0.2, np.float32)
        medians = np.tile(global_median, (areas, 1)).astype(np.float32)
        for i in np.unique(idx[ok]):
            picked = flat[ok & (idx == i)]
            if len(picked) >= 50:
                medians[i] = np.median(picked, axis=0)
        fallback = medians[index]
        for k in range(3):
            fallback[..., k] = ndimage.gaussian_filter(fallback[..., k], p["fallback_blur_m"])
        weight = ndimage.gaussian_filter(have.astype(np.float32), p["have_blur_m"])[..., None]
        return albedo * weight + fallback * (1.0 - weight)

    def _biome_tint(self, albedo, index, area_names: list[str]):
        """A subtle per-biome OKLab hue offset, so biomes painted with one layer separate."""
        p = self.palette
        strength = np.float32(p["biome_tint_strength"])
        offsets = np.zeros((len(area_names), 2), np.float32)
        for i, name in enumerate(area_names):
            offsets[i] = p["biome_tint_ab"].get(name, (0.0, 0.0))
        shift = offsets[index] * strength
        for k in range(2):
            shift[..., k] = ndimage.gaussian_filter(shift[..., k], p["biome_tint_blur_m"])
        out = np.empty_like(albedo)
        for start in range(0, albedo.shape[0], 512):
            block = slice(start, start + 512)
            lab = oklab(np.clip(albedo[block], 1e-7, None))
            lab[..., 1:] += shift[block]
            out[block] = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        if self.bake_weight is not None:
            w = self.bake_weight[..., None]
            out = out * (1.0 - w) + albedo * w
        return out

    def _target(self, hex_colour: str) -> np.ndarray:
        return np.clip(linear_from_oklab(display_to_ground(self.palette, hex_colour)), 0.0, 1.0)

    def _calibrate(self, albedo, weights):
        """Move each layer's median colour onto its target, measured on whatever albedo came."""
        cal = self.palette["calibration"]
        sample = (slice(None, None, 4), slice(None, None, 4))
        total = sum(w[sample].astype(np.float32) for w in weights.values())
        flat = albedo[sample]
        ops, self.calibration = {}, {}
        for name, hex_colour in cal["layers"].items():
            if name not in weights:
                continue
            pure = weights[name][sample] >= cal["pure_share"] * np.maximum(total, 1.0)
            if pure.sum() < cal["min_texels"]:
                continue
            source = _median_lab(flat[pure])
            ops[name] = transfer_op(source, display_to_ground(self.palette, hex_colour))
            self.calibration[name] = {"texels": int(pure.sum()), "dL": round(ops[name][0], 4)}
        return layer_transfer(albedo, weights, ops) if ops else albedo

    def _rock(self, albedo, index, area_names: list[str]):
        """Rock colour on a coarse grid: the game's rock albedo, tinted by the ground around."""
        p = self.palette
        step = ROCK_GRID_M
        rock_lab = oklab(np.asarray(self.meta["albedo_linear"]["rock"], np.float32))
        near = np.stack(
            [
                ndimage.gaussian_filter(albedo[..., k], p["rock_tint_blur_m"])[::step, ::step]
                for k in range(3)
            ],
            -1,
        )
        lab = oklab(np.clip(near, 1e-7, None))
        lab[..., 0] = (
            rock_lab[0] * (1 - p["rock_tint_lightness"]) + lab[..., 0] * p["rock_tint_lightness"]
        )
        lab[..., 0] += np.float32(p["rock_lightness_add"])
        lab[..., 1:] = (
            rock_lab[1:] * (1 - p["rock_tint_chroma"]) + lab[..., 1:] * p["rock_tint_chroma"]
        )
        cal = p["calibration"]
        mask = np.zeros(lab.shape[:2], np.float32)
        desert = cal.get("desert_rock")
        if desert:
            wanted = [i for i, n in enumerate(area_names) if n in desert["areas"]]
            mask = np.isin(index[::step, ::step], wanted).astype(np.float32)
            mask = ndimage.gaussian_filter(mask, p["rock_tint_blur_m"] / step)
        if (mask > 0.5).any():
            target = display_to_ground(p, desert["target"])
            moved = np.empty_like(lab)
            moved[..., 0] = lab[..., 0] + (target[0] - np.median(lab[mask > 0.5][:, 0]))
            moved[..., 1:] = target[1:]
            lab = lab + (moved - lab) * mask[..., None]
        rock = np.clip(linear_from_oklab(lab), 0.0, 1.0)
        if cal.get("rock_keeps_exposure"):
            rock *= (mask + (1.0 - mask) / np.float32(p["tone"]["gain"]))[..., None]
        return [rock[..., k].astype(np.float32) for k in range(3)]


def _unit_luminance(colour) -> np.ndarray:
    c = np.asarray(colour, np.float32)
    return c / (c @ _LUMA)


def painted_colours(scene: dict, ground: PaintedGround, sample, sample_rock) -> np.ndarray:
    """One band of the painted layer, sRGB 0..255.

    ``sample(plane)`` resamples a 1 m plane onto the band, ``sample_rock(plane)`` a plane of
    the coarse rock grid.
    """
    p = ground.palette
    albedo = np.stack([sample(plane) for plane in ground.albedo], -1)
    canopy = np.clip(sample(ground.canopy) / 255.0 * p["canopy_gain"], 0.0, 1.0)[..., None]
    g = albedo * (1.0 - canopy) + ground.canopy_rgb * canopy
    rock_rgb = np.stack([sample_rock(plane) for plane in ground.rock], -1)
    rock = scene["rock_weight"][..., None]
    g = g * (1.0 - rock) + rock_rgb * rock
    mesh_w = scene.get("mesh_weight")
    if mesh_w is not None and mesh_w.any():
        cls = scene["mesh_class"]
        colour = rock_rgb.copy()
        for which, rgb in ground.mesh_rgb.items():
            colour = np.where((cls == which)[..., None], rgb, colour)
        under = (cls == MESH_CORAL) & (scene["water"]["depth_m"] > 0)
        colour = np.where(under[..., None], ground.seabed_coral, colour)
        g = g * (1.0 - mesh_w[..., None]) + colour * mesh_w[..., None]

    lab = oklab(np.clip(g, 1e-7, None))
    lab[..., 1:] *= np.float32(p["chroma_gain"])
    lo, hi, cdf = ground.ramp
    lab[..., 0] += np.float32(p["altitude_lift"]) * ramp_position(
        scene["z_m"], lo, hi, cdf, p["ramp_equalised"]
    )
    g = np.clip(linear_from_oklab(lab), 0.0, 1.0)

    borrow = scene["borrow"]
    borrow = np.where(borrow < 1.0, 1.0 + (borrow - 1.0) * np.float32(p["borrow_ink_damp"]), borrow)
    ambient = np.float32(p["ambient"])
    light = (
        ambient * _unit_luminance(p["sky"])
        + (1 - ambient) * _unit_luminance(p["sun"]) * (scene["ndl"] / scene["ndl_flat"])[..., None]
    )
    exposure = np.float32(p["exposure"] * p["tone"]["gain"])
    lit = g * light * (exposure * borrow)[..., None]

    water = scene["water"]
    lit = wet_band(lit, water, p["shore"].get("wet_band"))
    w = ground.water
    depth = water["depth_m"][..., None]
    transmit = np.exp(-w["k"] * depth)
    bed = g * exposure * w["bed"]
    under = bed * transmit + w["body"] * (1.0 - transmit) + w["sky"]
    open_sea = 1.0 - np.exp(-depth / w["deep_tau_m"])
    under = under * (1.0 - open_sea) + w["deep"] * open_sea
    cover = water["cover"][..., None]
    out = lit * (1.0 - cover) + under * cover
    stroke = np.float32(p["shore"]["stroke"])
    if stroke:
        out = out * (1.0 - stroke * water["edge"][..., None])
    out = add_foam(out, water, p["shore"].get("foam"), np.float32(1.0))

    y = np.maximum(out @ _LUMA, 1e-7)
    t = p["tone"]
    out = out * (tone(y, t["knee"], t["white"]) / y)[..., None]
    return linear_to_srgb(out)
