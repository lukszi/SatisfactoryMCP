"""Generation presets: a request names a preset and enumerated options, never a command line.

Every argument is built here from a whitelist, and every path in it is chosen by the server
under ``data/local``; the install is named only by ``--game``. The estimates come from the
stage timings of one measured full render, scaled by area, until a finished job of the same
preset has left real ones in the manifest's history. docs/maps_contract.md §4.
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

from ... import config
from ...core.gameassets.versions import (
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPE_KERNEL_ONLY,
    STYLES,
)
from . import axes as ax
from . import registry

__all__ = [
    "PRESETS",
    "DiskShort",
    "PresetError",
    "QueueFull",
    "can_generate",
    "estimate",
    "normalise",
    "plan",
]

RENDER_LAYERS = ("terrain", "satellite")
RENDER_SIZES = (1024, 2048, 4096, 8192, 16384, 32768)
FULL_PX = 32768
INPUT_PRESETS = ("heightmap", "caves", "rocks")
PRESETS = ("render", "artwork", *INPUT_PRESETS)

SCRIPTS = {
    "render": "gen_map_renders.py",
    "artwork": "gen_map_image.py",
    "heightmap": "gen_world_heightmap.py",
    "caves": "gen_world_heightmap.py",
    "rocks": "gen_world_heightmap.py",
}

#: The modules the generators need from the ``gen`` extra.
GEN_MODULES = ("ooz", "texture2ddecoder", "PIL")

#: Seconds per stage of one full 32768 px render of both layers, from its log (2026-10-05).
#: ``fixed`` stages do not scale with the sheet; the rest scale with its area, and ``direct``
#: never drops under its floor because the triangles are the same at any size.
RENDER_STAGE_S = {"prep": 30.0, "sweep": 36.0, "direct": 692.0, "top": 119.0}
RENDER_LAYER_S = {"draw": 355.0, "cut": 122.0}
DIRECT_FLOOR_S = 80.0
TOP_FLOOR_S = 18.0
RENDER_KEEP_BYTES = 830_000_000
RENDER_KEEP_FLOOR = 10_000_000
CACHE_BYTES_FULL = 10_700_000_000
SPARE_BYTES = 2_000_000_000

FIXED = {
    "artwork": {"plain": (180.0, 160_000_000), "enhanced": (900.0, 600_000_000)},
    "heightmap": (900.0, 700_000_000),
    "caves": (120.0, 2_000_000),
    "rocks": (300.0, 60_000_000),
}


class PresetError(ValueError):
    """A preset or option the server will not run, worded for the player."""


class QueueFull(PresetError):
    """Four jobs are queued already."""


class DiskShort(PresetError):
    """The disk lacks what the job keeps plus what it needs while running."""


def _bool(options: dict, key: str, default: bool) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise PresetError(f"{key} is true or false, not {value!r}")
    return value


def normalise(preset: str, options: dict | None) -> dict:
    """``options`` as the preset takes them, every value checked against its whitelist."""
    options = dict(options or {})
    if preset == "render":
        layers = options.get("layers", list(RENDER_LAYERS))
        if (
            not isinstance(layers, list)
            or not layers
            or any(layer not in RENDER_LAYERS for layer in layers)
        ):
            raise PresetError(f"layers is a non-empty list of {', '.join(RENDER_LAYERS)}")
        size = options.get("size", FULL_PX)
        if size not in RENDER_SIZES:
            raise PresetError(f"size is one of {', '.join(map(str, RENDER_SIZES))}")
        recipe = options.get("recipe", "current")
        if recipe not in ("current", "kernel-only"):
            raise PresetError("recipe is current or kernel-only")
        return {
            "layers": [layer for layer in RENDER_LAYERS if layer in layers],
            "size": size,
            "recipe": recipe,
            "top": _bool(options, "top", True),
            "keep_cache": _bool(options, "keep_cache", False),
        }
    if preset == "artwork":
        return {
            "enhance": _bool(options, "enhance", False),
            "tiles_2x": _bool(options, "tiles_2x", True),
        }
    if preset in INPUT_PRESETS:
        return {}
    raise PresetError(f"no preset “{preset}”; known: {', '.join(PRESETS)}")


def _area(size: int) -> float:
    return (size / FULL_PX) ** 2


def _render_seconds(options: dict) -> dict[str, float]:
    area = _area(options["size"])
    kernel = options["recipe"] == "kernel-only"
    stages = {"prep": RENDER_STAGE_S["prep"]}
    if not kernel and not options.get("_cache_hit"):
        stages["sweep"] = RENDER_STAGE_S["sweep"]
        stages["direct"] = max(DIRECT_FLOOR_S, RENDER_STAGE_S["direct"] * area)
        if options["top"]:
            stages["top"] = max(TOP_FLOOR_S, RENDER_STAGE_S["top"] * area)
    for layer in options["layers"]:
        stages[f"draw:{layer}"] = RENDER_LAYER_S["draw"] * area + 2.0
        stages[f"cut:{layer}"] = RENDER_LAYER_S["cut"] * area + 2.0
    return stages


def stage_plan(preset: str, options: dict) -> dict[str, float]:
    """The stages a job passes through and the seconds each is expected to take."""
    if preset == "render":
        return _render_seconds(options)
    if preset == "artwork":
        return {"run": FIXED["artwork"]["enhanced" if options["enhance"] else "plain"][0]}
    return {"run": FIXED[preset][0]}


def _scaled_history(preset: str, options: dict) -> float | None:
    """The last finished job of the same preset, scaled by area, when there is one."""
    for row in reversed(registry.read()["history"]):
        if row.get("preset") != preset or not isinstance(row.get("seconds"), int | float):
            continue
        if preset != "render":
            return float(row["seconds"])
        was = row.get("options") or {}
        if was.get("recipe") != options["recipe"] or not was.get("size"):
            continue
        layers = max(1, len(was.get("layers") or []))
        per_layer = row["seconds"] / layers
        return (
            per_layer
            * len(options["layers"])
            * max(_area(options["size"]) / _area(was["size"]), 0.05)
        )
    return None


def cache_dir(size: int) -> Path:
    return registry.maps_dir() / registry.CACHE_DIR_NAME / f"{size}"


def estimate(preset: str, options: dict) -> dict:
    """``{seconds, keep_bytes, transient_bytes, free_bytes, needs_bytes, ok, reason}``."""
    options = normalise(preset, options)
    if preset == "render":
        area = _area(options["size"])
        cached = cache_dir(options["size"]).is_dir()
        seconds = sum(_render_seconds({**options, "_cache_hit": cached}).values())
        keep = len(options["layers"]) * max(RENDER_KEEP_FLOOR, int(RENDER_KEEP_BYTES * area))
        transient = int(CACHE_BYTES_FULL * area) + keep // max(1, len(options["layers"]))
    elif preset == "artwork":
        seconds, keep = FIXED["artwork"]["enhanced" if options["enhance"] else "plain"]
        transient = keep
    else:
        seconds, keep = FIXED[preset]
        transient = keep
    measured = _scaled_history(preset, options)
    if measured is not None:
        seconds = measured
    local = registry.local_dir()
    try:
        free = shutil.disk_usage(local if local.exists() else config.data_dir()).free
    except OSError:
        free = 0
    needs = keep + transient + SPARE_BYTES
    ok = free >= needs
    return {
        "seconds": round(seconds),
        "keep_bytes": keep,
        "transient_bytes": transient,
        "free_bytes": free,
        "needs_bytes": needs,
        "ok": ok,
        "reason": None
        if ok
        else f"needs {needs / 1e9:.1f} GB free while running, {free / 1e9:.1f} GB free",
        "measured": measured is not None,
    }


def tools_dir() -> Path:
    return config.REPO_ROOT / "tools"


def can_generate() -> dict:
    """Whether this checkout can run the generators here, each check under a millisecond."""
    gen = all(importlib.util.find_spec(name) is not None for name in GEN_MODULES)
    tools = (tools_dir() / SCRIPTS["render"]).is_file()
    try:
        config.game_root()
        game, game_reason = True, None
    except FileNotFoundError as exc:
        game, game_reason = False, str(exc)
    heightfield = (registry.local_dir() / "heightmap" / "meta.json").is_file()
    reason = None
    if not tools:
        reason = "generators come with a source checkout"
    elif not gen:
        reason = (
            "generation needs the gen extra: stop satisfactory-mcp, then run uv sync --extra gen"
        )
    elif not game:
        reason = game_reason
    return {
        "gen": gen,
        "tools": tools,
        "game": game,
        "heightfield": heightfield,
        "ok": reason is None,
        "reason": reason,
    }


def _recipe(options: dict) -> int:
    return (
        RENDER_RECIPE_KERNEL_ONLY if options["recipe"] == "kernel-only" else RENDER_RECIPE_CURRENT
    )


def plan(preset: str, options: dict, job_id: str, cl: int | None, taken: set[str]) -> dict:
    """``{script, argv, produces}`` for one job; ``produces`` maps new type ids to entries."""
    options = normalise(preset, options)
    local = registry.local_dir()
    maps = registry.maps_dir()
    game = str(config.game_root())
    produces: dict[str, dict] = {}
    if preset == "render":
        recipe = _recipe(options)
        argv = [
            "--game", game,
            "--field", str(local / "heightmap"),
            "--out-dir", str(maps),
            "--renders-name", job_id,
            "--size", str(options["size"]),
        ]  # fmt: skip
        for layer in options["layers"]:
            argv += ["--layer", layer]
        if options["recipe"] == "kernel-only":
            argv.append("--kernel-only")
        if not options["top"]:
            argv.append("--no-top")
        if options["keep_cache"] or cache_dir(options["size"]).is_dir():
            argv += ["--cache-dir", str(cache_dir(options["size"])), "--keep-direct"]
        for layer in options["layers"]:
            style = ax.LAYER_STYLE[layer]
            ident = ax.derive_id(STYLES[style]["label"], recipe, cl, taken | set(produces))
            entry = registry.new_entry(
                ident, "render", layer, f"{registry.MAPS_DIR_NAME}/{job_id}/{layer}",
                "meta.json", "generated",
            )  # fmt: skip
            entry.update(status="building", job=job_id, size_px=options["size"])
            produces[ident] = entry
    elif preset == "artwork":
        recipe = 2 if options["enhance"] else 0
        ident = ax.derive_id("artwork", recipe, cl, taken)
        rel = f"{registry.MAPS_DIR_NAME}/{ident}"
        argv = ["--game", game, "--out-dir", str(local / rel)]
        if options["enhance"]:
            argv.append("--enhance")
        if not options["tiles_2x"]:
            argv.append("--no-tiles-2x")
        entry = registry.new_entry(ident, "artwork", "map", rel, "map.json", "generated")
        entry.update(status="building", job=job_id, size_px=8192)
        produces[ident] = entry
    else:
        argv = ["--game", game, "--force"]
        if preset == "caves":
            argv += [
                "--caves",
                "--field",
                str(local / "heightmap"),
                "--caves-dir",
                str(local / "caves"),
            ]
        elif preset == "rocks":
            argv += ["--rocks", "--field", str(local / "heightmap")]
        else:
            argv += ["--out-dir", str(local / "heightmap")]
    return {"script": SCRIPTS[preset], "argv": argv, "produces": produces, "options": options}
