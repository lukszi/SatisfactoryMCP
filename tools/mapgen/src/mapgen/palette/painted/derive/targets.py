"""Display targets derived from the paint store, and the palette that wears them.

``derive`` lights each rule's albedo by the atmosphere volume its samples stand in and measures
it through the camera model. ``mapgen calibrate`` keeps the result beside the store; a render
lays it over the keys its palette's ``derived_keys`` names. docs/map/calibration.md section 31.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from mapgen.gamedata.level.lighting import AtmosphereVolume, LevelLighting
from mapgen.palette.painted.derive.camera import Light, exposure, hex_of_lab, measured_lab
from mapgen.palette.painted.derive.rules import Derivation, entries, scope_key, untargeted_layers
from mapgen.palette.painted.derive.scene import Scene
from mapgen.palette.painted.derive.tonemap import Gains
from mapgen.palette.painted.shapes import CalibrationStyle, PaintedPalette
from mapgen.palette.schema import checked
from mapgen.palette.styles import palette_digest
from satisfactory_mcp.core.arrays import BoolMask, F64Grid
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue, require_object, to_json_object

__all__ = [
    "GLOBAL",
    "MODEL_VERSION",
    "TARGETS_NAME",
    "Derived",
    "NoDaylight",
    "Target",
    "derive",
    "key_slot",
    "make_key_slot",
    "read_targets",
    "stamp_of",
    "targets_json",
    "vote",
    "with_targets",
]

TARGETS_NAME = "targets.derived.json"
#: Bumped whenever a rule or the camera model changes what a store derives.
MODEL_VERSION = 1
GLOBAL = "global"
#: Samples a light vote counts at most, drawn with a fixed seed.
MAX_VOTES = 20_000
_GAINS = ("mColorGainShadows", "mColorGainMidtones", "mColorGainHighlights")
#: The blocks a calibration key names.
_BLOCKS = ("layers", "derived", "canopy", "rock", "families", "tops", "meshes", "crowns", "species")
_UNMODELLED = ("mColorGammaMidtones", "mColorContrastShadows", "mColorSaturationMidtones")


class NoDaylight(Exception):
    """A paint store written before it kept the level's light."""


@dataclass(frozen=True)
class Target:
    """One key's derived display colour, the light it was measured under, and its rule."""

    key: str
    hex: str | None
    light: str
    shares: dict[str, float]
    rule: Derivation
    targeted: bool


@dataclass(frozen=True)
class Derived:
    """Every key's target, the exposure they share, and the lights the votes chose from."""

    exposure: float
    band_luminance: float
    texels: int
    lights: dict[str, Light]
    targets: list[Target]
    notes: list[str]

    def hexes(self) -> dict[str, str]:
        return {t.key: t.hex for t in self.targets if t.hex}


def _rgb(values: Sequence[float]) -> tuple[float, float, float]:
    return float(values[0]), float(values[1]), float(values[2])


def _lights(
    lighting: LevelLighting, volumes: Sequence[AtmosphereVolume]
) -> tuple[dict[str, Light], list[str]]:
    """The level's light and each volume's: its sun colour and grade, its brighter sun left
    to auto-exposure."""
    base = Light(
        GLOBAL,
        _rgb(lighting["sun_colour"]),
        lighting["sun_lux"],
        -lighting["sun_pitch_deg"],
        _rgb(lighting["sky_luminance_factor"]),
    )
    lights: dict[str, Light] = {GLOBAL: base}
    notes: list[str] = []
    for volume in volumes:
        noon = volume["noon"]
        gains: Gains | None = None
        if any(k in noon for k in _GAINS):
            shadows, mids, highs = (_rgb(noon.get(k, [1.0, 1.0, 1.0])) for k in _GAINS)
            gains = (shadows, mids, highs)
        notes += [
            f"{volume['name']}: {k} {noon[k]} is not modelled"
            for k in _UNMODELLED
            if k in noon and not np.allclose(noon[k], 1.0)
        ]
        sun = (
            _rgb(noon["mSunLightColorCurve"]) if "mSunLightColorCurve" in noon else base.sun_colour
        )
        lights[volume["name"]] = Light(
            volume["name"], sun, base.sun_lux, base.elevation_deg, base.sky_luminance_factor, gains
        )
    return lights, notes


def _in_hull(x: F64Grid, y: F64Grid, hull: Sequence[Sequence[float]]) -> BoolMask:
    """Points inside a counter-clockwise convex polygon."""
    inside = np.ones(len(x), bool)
    for i, (x0, y0) in enumerate(hull):
        x1, y1 = hull[(i + 1) % len(hull)]
        inside &= (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0) >= 0
    return inside


def vote(
    where: tuple[F64Grid, F64Grid] | None, volumes: Sequence[AtmosphereVolume]
) -> dict[str, float]:
    """Each sample takes the highest-priority volume whose brush holds it, else the level's
    light; every light's share, largest first."""
    if where is None or len(where[0]) == 0:
        return {GLOBAL: 1.0}
    x, y = (np.asarray(a, np.float64) for a in where)
    if len(x) > MAX_VOTES:
        pick = np.random.default_rng(0).choice(len(x), MAX_VOTES, replace=False)
        x, y = x[pick], y[pick]
    best = np.full(len(x), -1)
    priority = np.full(len(x), -np.inf)
    for i, volume in enumerate(volumes):
        inside = _in_hull(x, y, volume["hull_xy_m"]) & (volume["priority"] > priority)
        best[inside], priority[inside] = i, volume["priority"]
    counts = np.bincount(best + 1, minlength=len(volumes) + 1) / len(x)
    names = [GLOBAL, *(v["name"] for v in volumes)]
    shares = {names[k]: round(float(c), 3) for k, c in enumerate(counts) if c > 0}
    return dict(sorted(shares.items(), key=lambda kv: -kv[1]))


def derive(scene: Scene, cal: CalibrationStyle) -> Derived:
    """Every calibration key's derived display colour, and the untargeted layers'."""
    lighting = scene.meta.get("lighting")
    if not lighting:
        raise NoDaylight("the paint store keeps no daylight: re-run `python -m mapgen paint`")
    volumes = scene.meta.get("atmosphere_volumes", [])
    lights, notes = _lights(lighting, volumes)
    auto = lighting["auto_exposure"]
    gain, band = exposure(
        scene.bake_linear[scene.have],
        lights[GLOBAL],
        auto["bias_ev"],
        (auto["low_pct"], auto["high_pct"]),
    )
    found: list[Target] = []
    rows = [(row, True) for row in entries(scene, cal)]
    rows += [(row, False) for row in untargeted_layers(scene, cal)]
    for row, targeted in rows:
        shares = vote(row.where, volumes)
        light = row.light if row.light in lights else next(iter(shares))
        colour = None
        if row.albedo is not None:
            colour = hex_of_lab(measured_lab(row.albedo, lights[light], gain))
        found.append(Target(row.key, colour, light, shares, row, targeted))
    return Derived(gain, band, int(scene.have.sum()), lights, found, notes)


# -- the file beside the store, and the palette that wears it ----------------------------------


def stamp_of(paint_digest: str | None, areas_digest: str, cal: CalibrationStyle) -> JsonObject:
    """What a derivation depends on: the store, the area map, the calibration block (not its
    prose, nor which keys wear the result), the model."""
    block: dict[str, object] = {k: v for k, v in cal.items() if k not in ("about", "derived_keys")}
    return {
        "paint_digest": paint_digest,
        "areas_digest": areas_digest,
        "calibration_digest": palette_digest(block),
        "model_version": MODEL_VERSION,
    }


def _target_json(t: Target) -> JsonObject:
    rule = t.rule
    return {
        "hex": t.hex,
        "light": t.light,
        "light_shares": dict(t.shares),
        "rule": rule.rule or None,
        "albedo_linear": None if rule.albedo is None else [round(float(v), 5) for v in rule.albedo],
        "assets": list(rule.assets),
        "params": {k: list(v) for k, v in rule.params.items()},
        "samples": rule.samples or None,
        "why_not": rule.error,
    }


def targets_json(derived: Derived, stamp: JsonObject, build: int | None) -> JsonObject:
    """``targets.derived.json``: every key's derived colour with its rule, the stamp, the model."""
    lights: JsonObject = {
        name: {
            "sun_colour": list(light.sun_colour),
            "sun_lux": light.sun_lux,
            "elevation_deg": round(light.elevation_deg, 3),
            "gains": None if light.gains is None else [list(g) for g in light.gains],
        }
        for name, light in derived.lights.items()
    }
    targets: JsonObject = {t.key: _target_json(t) for t in derived.targets if t.targeted}
    untargeted: JsonObject = {t.key: _target_json(t) for t in derived.targets if not t.targeted}
    return {
        "generator": "mapgen calibrate",
        "about": "display targets derived from the game install; docs/map/calibration.md §31",
        "build": build,
        "stamp": stamp,
        "exposure": {
            "E": round(derived.exposure, 4),
            "band_mean_luminance": round(derived.band_luminance, 5),
            "bake_texels": derived.texels,
        },
        "lights": lights,
        "notes": list(derived.notes),
        "targets": targets,
        "untargeted_layers": untargeted,
    }


def read_targets(paint_dir: Path, stamp: JsonObject) -> dict[str, str] | None:
    """The file's hex per key when its stamp is ``stamp``; None when absent or stale."""
    try:
        raw: JsonValue = json.loads((paint_dir / TARGETS_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("stamp") != stamp:
        return None
    found: dict[str, str] = {}
    for block in ("targets", "untargeted_layers"):
        rows = raw.get(block)
        for key, row in (rows if isinstance(rows, dict) else {}).items():
            colour = row.get("hex") if isinstance(row, dict) else None
            if isinstance(colour, str):
                found[key] = colour
    return found


def _entry_scope(entry: JsonValue) -> str | None:
    areas = entry.get("areas") if isinstance(entry, dict) else None
    return scope_key([str(a) for a in areas]) if isinstance(areas, list) else None


def _key_place(cal: JsonObject, key: str) -> tuple[JsonObject, str, str] | None:
    """The table holder a calibration key names (the block, or its area's entry), the table's
    block (``derived`` reads ``layers``) and the name in it; None for a key the block cannot
    hold."""
    holder: JsonValue = cal
    block, _, name = key.partition(".")
    if key.startswith("areas["):
        scope, _, rest = key.partition("].")
        areas = cal.get("areas")
        area_entries = areas if isinstance(areas, list) else []
        holder = next((area for area in area_entries if _entry_scope(area) == scope + "]"), None)
        block, _, name = rest.partition(".")
    if not isinstance(holder, dict) or block not in _BLOCKS:
        return None
    return holder, ("layers" if name and block == "derived" else block), name


def key_slot(cal: JsonObject, key: str) -> tuple[JsonObject, str] | None:
    """Where a calibration key's colour sits: its table and its name there, a ``derived`` key's
    on its layer; None for a key the block cannot hold, or whose table it lacks."""
    place = _key_place(cal, key)
    if place is None:
        return None
    holder, block, name = place
    if not name:
        return holder, block
    table = holder.get(block)
    return (table, name) if isinstance(table, dict) else None


def make_key_slot(cal: JsonObject, key: str) -> tuple[JsonObject, str] | None:
    """``key_slot``, with the key's table added to ``cal`` first where it is missing."""
    place = _key_place(cal, key)
    if place is not None and place[2]:
        place[0].setdefault(place[1], {})
    return key_slot(cal, key)


def with_targets(
    palette: PaintedPalette, hexes: Mapping[str, str]
) -> tuple[PaintedPalette, list[str]]:
    """The palette with each ``derived_keys`` key that has a derived colour wearing it, and
    those keys. A derived key the calibration block cannot hold is refused."""
    keys = palette["calibration"].get("derived_keys", [])
    calibration = palette["calibration"]
    unknown = [k for k in keys if make_key_slot(to_json_object(calibration), k) is None]
    if unknown:
        raise ValueError(f"calibration.derived_keys names no calibration key: {unknown}")
    merged = to_json_object(palette)
    cal = require_object(merged["calibration"])
    applied = [key for key in keys if key in hexes]
    for key in applied:
        slot = make_key_slot(cal, key)
        if slot is not None:
            slot[0][slot[1]] = hexes[key]
    return checked(PaintedPalette, merged, palette["id"]), applied
