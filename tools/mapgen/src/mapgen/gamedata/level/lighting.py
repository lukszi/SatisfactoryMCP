"""The persistent level's daylight and the atmosphere volumes that override it, read at noon.

The paint command stores both in its ``meta.json``; ``mapgen calibrate`` derives display colours
from them. docs/map/calibration.md section 31 names every property read.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import TypedDict

from mapgen.gamedata.install import GameReader
from mapgen.gamedata.level.curves import (
    Tag,
    evaluate,
    runtime_curves,
    tag_float,
    tag_floats,
    tag_stream,
)
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    class_name_of,
    quat_rotate,
    root_component,
    world_transform,
)

__all__ = [
    "VOLUME_CLASS",
    "AtmosphereVolume",
    "AutoExposure",
    "LevelLighting",
    "convex_hull_xy",
    "level_volumes",
    "persistent_lighting",
]

PERSISTENT_LEVEL = "/GameLevel01/Persistent_Level.umap"
VOLUME_CLASS = "FGAtmosphereVolume"
SKY_CLASS = "BP_Sky_Sphere_C"
SUN_ACTOR = "LightSource_0"
NOON_H = 12.0

#: The override curves the camera model reads, each with the switch that enables it.
VOLUME_SWITCHES = {
    "mSunLightColorCurve": "mOverrideSunLightColor",
    "mSunIntensity": "mOverrideSunIntensity",
    "mColorGainShadows": "mEnableColorGainShadows",
    "mColorGainMidtones": "mEnableColorGainMidtones",
    "mColorGainHighlights": "mEnableColorGainHighlights",
    "mColorGammaMidtones": "mEnableColorGammaMidtones",
    "mColorContrastShadows": "mEnableColorContrastShadows",
    "mColorSaturationMidtones": "mEnableColorSaturationMidtones",
}

#: ``Settings`` member -> the key it is stored under.
AUTO_EXPOSURE = {
    "AutoExposureBias": "bias_ev",
    "AutoExposureLowPercent": "low_pct",
    "AutoExposureHighPercent": "high_pct",
    "AutoExposureMinBrightness": "min_brightness",
    "AutoExposureMaxBrightness": "max_brightness",
}


class AutoExposure(TypedDict):
    """The global post-process volume's histogram auto-exposure."""

    bias_ev: float
    low_pct: float
    high_pct: float
    min_brightness: float
    max_brightness: float


class LevelLighting(TypedDict):
    """The persistent level's light at noon: sun colour and lux, its pitch, sky and exposure."""

    level: str
    noon_h: float
    sun_colour: list[float]
    sun_lux: float
    sun_pitch_deg: float
    day_seconds: float | None
    sky_luminance_factor: list[float]
    auto_exposure: AutoExposure


class AtmosphereVolume(TypedDict):
    """One ``FGAtmosphereVolume``: its enabled overrides at noon and its brush seen from above."""

    name: str
    level: str
    priority: float
    noon: dict[str, list[float]]
    box_m: list[float]
    hull_xy_m: list[list[float]]


@dataclass
class _Found:
    sun_colour: list[float] | None = None
    sun_lux: float | None = None
    sun_pitch_deg: float | None = None
    day_seconds: float | None = None
    sky: list[float] | None = None
    exposure: dict[str, float] = field(default_factory=dict[str, float])


def _noon(tag: Tag, names: list[str], channels: int) -> list[float] | None:
    values = [evaluate(curve, NOON_H) for curve in runtime_curves(tag.payload, names)[:channels]]
    return [v for v in values if v is not None] if None not in values else None


def _export_tags(view: PackageView, slot: int) -> list[Tag]:
    return tag_stream(view.pkg.body(view.exports[slot]), view.pkg.names, 1)[0]


def _sky(view: PackageView, slot: int, found: _Found) -> None:
    for tag in _export_tags(view, slot):
        if tag.name == "mSunLightColorCurve":
            found.sun_colour = _noon(tag, view.pkg.names, 3)
        elif tag.name == "mSunIntensity":
            lux = _noon(tag, view.pkg.names, 1)
            found.sun_lux = lux[0] if lux else None


def _sun(view: PackageView, slot: int, found: _Found) -> None:
    for child in view.children.get(slot, []):
        for tag in _export_tags(view, child):
            rotation = tag_floats(tag) if tag.name == "RelativeRotation" else None
            if rotation is not None:
                found.sun_pitch_deg = rotation[0]


def _exposure(view: PackageView, slot: int, found: _Found) -> None:
    for tag in _export_tags(view, slot):
        if tag.name != "Settings":
            continue
        for inner in tag_stream(tag.payload, view.pkg.names)[0]:
            value = tag_float(inner)
            if inner.name in AUTO_EXPOSURE and value is not None:
                found.exposure[AUTO_EXPOSURE[inner.name]] = value


def _other(view: PackageView, slot: int, found: _Found) -> None:
    for tag in _export_tags(view, slot):
        if tag.name == "SkyLuminanceFactor" and (rgba := tag_floats(tag)):
            found.sky = list(rgba[:3])
        elif tag.name == "mDaySeconds":
            found.day_seconds = tag_float(tag)


def _read_level(view: PackageView) -> _Found:
    found = _Found()
    for slot, class_path in view.class_of.items():
        cls, name = class_name_of(class_path), view.exports[slot]["name"]
        if cls == SKY_CLASS:
            _sky(view, slot, found)
        elif cls == "DirectionalLight" and name == SUN_ACTOR:
            _sun(view, slot, found)
        elif cls == "PostProcessVolume" and name.startswith("GlobalPostProcess"):
            _exposure(view, slot, found)
        elif cls in ("SkyAtmosphereComponent", "BP_TimeOfDaySubsystem_C"):
            _other(view, slot, found)
    return found


def persistent_lighting(game: GameReader) -> tuple[LevelLighting | None, list[str]]:
    """The persistent level's lighting, or None and the names of what it could not read."""
    path = next((p for p in game.store.by_path if p.endswith(PERSISTENT_LEVEL)), None)
    if path is None:
        return None, [PERSISTENT_LEVEL]
    f = _read_level(PackageView(game.store.read_path(path), game.scripts))
    e = f.exposure
    missing = [n for n, v in (("sun colour", f.sun_colour), ("sun lux", f.sun_lux),
                              ("sun pitch", f.sun_pitch_deg), ("sky luminance factor", f.sky))
               if v is None] + [n for n in AUTO_EXPOSURE.values() if n not in e]  # fmt: skip
    if missing or f.sun_colour is None or f.sun_lux is None or f.sun_pitch_deg is None:
        return None, missing
    exposure: AutoExposure = {"bias_ev": e["bias_ev"], "low_pct": e["low_pct"],
                              "high_pct": e["high_pct"], "min_brightness": e["min_brightness"],
                              "max_brightness": e["max_brightness"]}  # fmt: skip
    return {"level": PERSISTENT_LEVEL, "noon_h": NOON_H, "sun_colour": f.sun_colour,
            "sun_lux": f.sun_lux, "sun_pitch_deg": f.sun_pitch_deg, "day_seconds": f.day_seconds,
            "sky_luminance_factor": f.sky or [], "auto_exposure": exposure}, []  # fmt: skip


def convex_hull_xy(points: list[tuple[float, float]]) -> list[list[float]]:
    """The convex hull of ``points``, counter-clockwise from the lowest x (Andrew's chain)."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return [list(p) for p in pts]

    def chain(seq: list[tuple[float, float]]) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out[:-1]

    return [list(p) for p in chain(pts) + chain(pts[::-1])]


def _convex_vertices(view: PackageView, body: int) -> list[tuple[float, float, float]]:
    """``AggGeom.ConvexElems[*].VertexData`` of a body setup, local cm."""
    found: list[tuple[float, float, float]] = []
    names = view.pkg.names
    for agg in (t for t in _export_tags(view, body) if t.name == "AggGeom"):
        for elems in (t for t in tag_stream(agg.payload, names)[0] if t.name == "ConvexElems"):
            pos = 4
            for _ in range(struct.unpack_from("<i", elems.payload)[0]):
                element, pos = tag_stream(elems.payload, names, pos)
                for data in (t.payload for t in element if t.name == "VertexData"):
                    count = struct.unpack_from("<i", data)[0]
                    if len(data) >= 4 + 24 * count:
                        found += [v for v in struct.iter_unpack("<3d", data[4 : 4 + 24 * count])]
    return found


def _brush_points_cm(view: PackageView, slot: int, classes: ClassFacts) -> list[tuple[float, ...]]:
    """The volume's brush vertices in world cm."""
    root = root_component(view, slot)
    if root is None:
        return []
    brush = view.props(root).get("BrushBodySetup")
    body = view.export_ref(brush) if brush else None
    transform = world_transform(view, root, classes)[0]
    if body is None or transform is None:
        return []
    location, quat, scale = transform
    points: list[tuple[float, ...]] = []
    for x, y, z in _convex_vertices(view, body):
        moved = quat_rotate(quat, (x * scale[0], y * scale[1], z * scale[2]))
        points.append(tuple(c + o for c, o in zip(moved, location, strict=True)))
    return points


def _atmosphere_volume(
    view: PackageView, slot: int, level: str, classes: ClassFacts
) -> AtmosphereVolume | None:
    tags = _export_tags(view, slot)
    switches = {t.name for t in tags if t.kind == "BoolProperty" and t.true}
    noon: dict[str, list[float]] = {}
    priority = 0.0
    for tag in tags:
        if tag.name == "mPriority":
            priority = tag_float(tag) or 0.0
        elif tag.name in VOLUME_SWITCHES and VOLUME_SWITCHES[tag.name] in switches:
            value = _noon(tag, view.pkg.names, 3)
            if value:
                noon[tag.name] = value
    points = _brush_points_cm(view, slot, classes)
    if not noon or len(points) < 3:
        return None
    xs, ys, zs = (sorted(p[k] / 100.0 for p in points) for k in range(3))
    hull = convex_hull_xy([(round(p[0] / 100.0, 2), round(p[1] / 100.0, 2)) for p in points])
    box = [round(v, 2) for v in (xs[0], xs[-1], ys[0], ys[-1], zs[0], zs[-1])]
    return {"name": view.exports[slot]["name"], "level": level, "priority": priority,
            "noon": noon, "box_m": box, "hull_xy_m": hull}  # fmt: skip


def level_volumes(view: PackageView, level: str, classes: ClassFacts) -> list[AtmosphereVolume]:
    """Every atmosphere volume of one level package that overrides the sun or the grade."""
    found = [
        _atmosphere_volume(view, slot, level, classes)
        for slot, class_path in view.class_of.items()
        if class_name_of(class_path) == VOLUME_CLASS
    ]
    return [volume for volume in found if volume is not None]
