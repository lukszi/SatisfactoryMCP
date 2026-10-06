"""The game's waterfalls: one record per ``BP_WaterFallTool_02``, read off its own modules.

The tool hangs a vertical curtain of 2 m by 10 m side modules from a lip, lays top modules
upstream of the lip, and puts splash modules where the curtain lands. A record keeps what a
top-down map can draw: the lip, its width, the drop and the splashes.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, TypeGuard

import numpy as np

from mapgen.gamedata.level.sweep import Sweep, flagged_tags, instance_matrices
from satisfactory_mcp.core.arrays import F64Grid
from satisfactory_mcp.core.gameassets.packages import (
    ClassFacts,
    PackageView,
    class_name_of,
    compose,
    local_transform,
    quat_rotate,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = [
    "FALLS_CACHE_DIR_NAME",
    "FALLS_CACHE_NAME",
    "FALL_CLASS",
    "SIDE_COMPONENT",
    "SIDE_MODULE_HEIGHT_CM",
    "SIDE_MODULE_WIDTH_CM",
    "SPLASH_COMPONENT",
    "TOP_COMPONENT",
    "TOP_MODULE_LENGTH_CM",
    "FallRecord",
    "FallsStamp",
    "cached_falls",
    "fall_from_modules",
    "load_or_sweep_falls",
    "read_fall",
    "write_falls",
]

FALL_CLASS = "BP_WaterFallTool_02_C"
SIDE_COMPONENT = "Waterfall_Side_ISM"
TOP_COMPONENT = "Waterfall_Top_ISM"
SPLASH_COMPONENT = "Waterfall_Splash_Mid"
TOP_CENTRE = "Waterfall Top Center"

#: ``SM_Waterfall_Side_Module`` spans x -100..100 and z -1000..0 cm, ``SM_Waterfall_Top_Module``
#: y 0..805 cm upstream of the lip, ``SM_SplashModule_Mid`` a 200 cm disc.
SIDE_MODULE_WIDTH_CM = 200.0
SIDE_MODULE_HEIGHT_CM = 1000.0
TOP_MODULE_LENGTH_CM = 805.0
SPLASH_MODULE_RADIUS_CM = 100.0

FALLS_CACHE_DIR_NAME = "falls.cache"
FALLS_CACHE_NAME = "falls.json"
_ISM = frozenset({"InstancedStaticMeshComponent", "HierarchicalInstancedStaticMeshComponent"})


class FallRecord(TypedDict):
    """One waterfall in metres: the lip, its axes, width, drop, top run and splashes."""

    x: float
    y: float
    z: float
    along: list[float]
    out: list[float]
    width_m: float
    height_m: float
    top_len_m: float
    splash: list[list[float]]
    actor: NotRequired[str]


class FallsStamp(TypedDict):
    """What a falls cache must agree with to be read: the build and the reader."""

    game_version_pinned: str | None
    reader_version: int


def _axes(quat: tuple[float, ...]) -> F64Grid:
    return np.stack([np.array(quat_rotate(quat, tuple(a))) for a in np.eye(3)])


def _instances(
    view: PackageView, slot: int, root_tf: tuple[tuple[float, ...], ...], classes: ClassFacts
) -> F64Grid | None:
    """An instanced component's matrices in world space, rows as UE's (scaled axes, origin)."""
    body = view.pkg.body(view.exports[slot])
    _props, end = flagged_tags(body, view.pkg.names)
    mats = instance_matrices(body[end:], None)
    if mats is None:
        return None
    # Attached through the class template, so the instance names no parent of its own.
    tf: tuple[tuple[float, ...], ...] | None
    if "AttachParent" in view.props(slot):
        tf = world_transform(view, slot, classes)[0]
    else:
        tf = compose(root_tf, local_transform(view, slot, classes))
    if tf is None:
        return None
    loc, quat, scale = tf
    axes, size = _axes(quat), np.array(scale)
    world = mats.copy()
    world[:, :3, :3] = (mats[:, :3, :3] * size[None, None, :]) @ axes
    world[:, 3, :3] = (mats[:, 3, :3] * size) @ axes + np.array(loc)
    return world


def fall_from_modules(
    lip_cm: Sequence[float],
    axes: F64Grid,
    side: F64Grid | None,
    top: F64Grid | None,
    splash: F64Grid | None,
) -> FallRecord | None:
    """One record from the world matrices of a fall's modules, in metres; ``None`` if empty.

    ``axes`` are the actor's local X (along the lip) and Y (upstream) in world space.
    """
    if side is None or not len(side):
        return None
    along = np.asarray(axes[0][:2], np.float64)
    out = -np.asarray(axes[1][:2], np.float64)
    along /= max(np.linalg.norm(along), 1e-9)
    out /= max(np.linalg.norm(out), 1e-9)
    pos = side[:, 3, :3]
    s = (pos[:, :2] - np.asarray(lip_cm[:2])) @ along
    module_w = SIDE_MODULE_WIDTH_CM * np.linalg.norm(side[:, 0, :3], axis=1)
    module_h = SIDE_MODULE_HEIGHT_CM * np.linalg.norm(side[:, 2, :3], axis=1)
    mid = 0.5 * (s.max() + s.min())
    lip = np.array([lip_cm[0] + along[0] * mid, lip_cm[1] + along[1] * mid, pos[:, 2].max()])
    top_len = 0.0
    if top is not None and len(top):
        top_len = float(np.median(TOP_MODULE_LENGTH_CM * np.linalg.norm(top[:, 1, :3], axis=1)))
    splashes: list[list[float]] = []
    for m in splash if splash is not None else ():
        r = SPLASH_MODULE_RADIUS_CM * max(np.linalg.norm(m[0, :3]), np.linalg.norm(m[1, :3]))
        splashes.append([round(float(v) / 100, 2) for v in (*m[3, :3], r)])
    return {
        "x": round(lip[0] / 100, 2),
        "y": round(lip[1] / 100, 2),
        "z": round(lip[2] / 100, 2),
        "along": [round(float(v), 5) for v in along],
        "out": [round(float(v), 5) for v in out],
        "width_m": round(float(s.max() - s.min() + np.median(module_w)) / 100, 2),
        "height_m": round(float(lip[2] - (pos[:, 2] - module_h).min()) / 100, 2),
        "top_len_m": round(top_len / 100, 2),
        "splash": splashes,
    }


def read_fall(
    view: PackageView, slot: int, class_path: str | None, classes: ClassFacts
) -> FallRecord | None:
    """The record of one level actor, or ``None`` if it is not a waterfall tool."""
    if class_name_of(class_path) != FALL_CLASS:
        return None
    root = root_component(view, slot)
    root_tf: tuple[tuple[float, ...], ...] | None
    root_tf = world_transform(view, root, classes)[0] if root is not None else None
    if root_tf is None:
        return None
    parts: dict[str, F64Grid] = {}
    lip = root_tf[0]
    for child in view.children.get(slot, []):
        name: str = view.exports[child]["name"]
        if name == TOP_CENTRE:
            found: tuple[tuple[float, ...], ...] | None = world_transform(view, child, classes)[0]
            lip = found[0] if found else lip
        elif class_name_of(view.class_of.get(child)) in _ISM and name in (
            SIDE_COMPONENT,
            TOP_COMPONENT,
            SPLASH_COMPONENT,
        ):
            got = _instances(view, child, root_tf, classes)
            if got is not None:
                parts[name] = got
    record = fall_from_modules(
        lip,
        _axes(root_tf[1]),
        parts.get(SIDE_COMPONENT),
        parts.get(TOP_COMPONENT),
        parts.get(SPLASH_COMPONENT),
    )
    if record is not None:
        record["actor"] = view.exports[slot]["name"]
    return record


def _metres(value: JsonValue) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def write_falls(path: Path, falls: Sequence[JsonObject], stamp: FallsStamp) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(falls, key=lambda f: (_metres(f["x"]), _metres(f["y"]), _metres(f["z"])))
    path.write_text(json.dumps({**stamp, "falls": ordered}, indent=0), encoding="utf-8")


def _is_fall(actor: object) -> TypeGuard[JsonObject]:
    """Whether a swept level actor is a fall record (``read_fall``'s answer for its class)."""
    return isinstance(actor, dict) and "width_m" in actor


def cached_falls(path: Path, stamp: FallsStamp) -> list[JsonObject] | None:
    """The records at ``path`` if they were read from this build by this reader."""
    try:
        recorded: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    falls = recorded.get("falls")
    return [f for f in falls if isinstance(f, dict)] if isinstance(falls, list) else None


def load_or_sweep_falls(
    cache_root: Path, build: str | None, sweep_once: Callable[[], Sweep]
) -> tuple[list[JsonObject], dict[str, JsonObject]]:
    """The falls from this build's cache, else from ``sweep_once()``, and what the sidecar says."""
    path = cache_root / FALLS_CACHE_DIR_NAME / FALLS_CACHE_NAME
    stamp: FallsStamp = {
        "game_version_pinned": build,
        "reader_version": READER_VERSIONS["waterfalls"],
    }
    falls = cached_falls(path, stamp)
    reused = falls is not None
    if falls is None:
        falls = [f for f in sweep_once().get("actors", ()) if _is_fall(f)]
        write_falls(path, falls, stamp)
    print(f"  {len(falls)} waterfalls" + (f", reused from {path}" if reused else ""))
    meta: JsonObject = {
        "actors": len(falls),
        "reader_version": stamp["reader_version"],
        "reused": reused,
        "width_m_total": round(sum(_metres(f["width_m"]) for f in falls), 1),
        "digest": sha256_hex(json.dumps(falls, sort_keys=True).encode("utf-8")),
    }
    return falls, {"waterfalls": meta}
