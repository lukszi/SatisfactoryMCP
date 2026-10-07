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
    ARTWORK_RECIPES,
    HEIGHTFIELD_GENERATOR_VERSION,
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPE_KERNEL_ONLY,
    RENDER_RECIPES,
    STYLES,
)
from ...core.gpu import vulkan_available
from . import axes as ax
from . import registry

__all__ = [
    "PRESETS",
    "DiskShort",
    "PresetError",
    "QueueFull",
    "cache_ready",
    "cached_sizes",
    "can_generate",
    "estimate",
    "normalise",
    "plan",
]

RENDER_LAYERS = ("terrain", "satellite", "painted", "relief", "relief-dark")
#: What a render job draws when it names no layers: the painted one needs the paint input.
DEFAULT_LAYERS = ("terrain", "satellite")
RENDER_SIZES = (1024, 2048, 4096, 8192, 16384, 32768)
FULL_PX = 32768
INPUT_PRESETS = ("heightmap", "caves", "rocks", "paint")
PRESETS = ("render", "artwork", *INPUT_PRESETS)

SCRIPTS = {
    "render": "gen_map_renders.py",
    "artwork": "gen_map_image.py",
    "heightmap": "gen_world_heightmap.py",
    "caves": "gen_world_heightmap.py",
    "rocks": "gen_world_heightmap.py",
    "paint": "gen_paint_layers.py",
}

#: The ``python -m mapgen`` command a preset runs. ``SCRIPTS`` stays: a job records it, and
#: each one is that command's shim.
COMMANDS = {
    "render": "renders",
    "artwork": "artwork",
    "heightmap": "heightmap",
    "caves": "heightmap",
    "rocks": "heightmap",
    "paint": "paint",
}

#: The modules the generators need from the ``gen`` extra.
GEN_MODULES = ("ooz", "texture2ddecoder", "PIL", "zstandard")

#: Seconds per stage of one full 32768 px render of both layers, from its log (2026-10-05).
#: ``fixed`` stages do not scale with the sheet; the rest scale with its area, and ``direct``
#: never drops under its floor because the triangles are the same at any size.
RENDER_STAGE_S = {"prep": 30.0, "sweep": 36.0, "direct": 692.0, "top": 119.0}
#: Per layer, from renders-v7's five lit layers and the 2026-10-06 performance work
#: (docs/maps_contract.md section 4.3): the parallel cut of a layer without the light.
RENDER_LAYER_S = {"cut": 73.0}
#: The draw, one pass over every layer (section 4.3): the ground the layers share, once, and
#: each layer's colour over it. One layer alone takes 340 s, the draw on 8 threads less lean
#: sampling's 12%; five take 0.65 of five drawn one by one (2026-10-07).
RENDER_DRAW_S = {"ground": 150.0, "layer": 190.0}
#: ``--light``: the lighting bake once, on 16 workers (docs/spatial-and-map.md section 29), and
#: per layer the unlit tree cut beside the baked one, ``LIGHT_CUT_FACTOR`` times the cut. The
#: scratch is the light cache while it runs, and the crown occluder the paint store adds to
#: it whatever the layers, written once (section 29, "Scratch").
LIGHT_STAGE_S = 830.0
#: A restyle at a size whose cache keeps a light installs it instead of baking: hard links to
#: the full-size pyramid's 43,690 files, 6.5 to 9.4 s on the reference machine
#: (docs/spatial-and-map.md section 29, "Kept light").
LIGHT_KEPT_S = 10.0
LIGHT_CUT_FACTOR = 1.8
LIGHT_KEEP_BYTES = 1_000_000_000
UNLIT_KEEP_BYTES = 450_000_000
LIGHT_SCRATCH_BYTES = 14_500_000_000
CROWN_SCRATCH_BYTES = 5_370_000_000
DIRECT_FLOOR_S = 80.0
TOP_FLOOR_S = 18.0
RENDER_KEEP_BYTES = 890_000_000
RENDER_KEEP_FLOOR = 10_000_000
#: The raster caches of one full render in the zstd band store: 0.93 GB measured, where the
#: raw layout they replace was 18.5 GB (docs/spatial-and-map.md section 39).
CACHE_BYTES_FULL = 1_000_000_000
SPARE_BYTES = 2_000_000_000

FIXED = {
    "artwork": {"plain": (180.0, 160_000_000), "enhanced": (900.0, 600_000_000)},
    "heightmap": (900.0, 700_000_000),
    "caves": (120.0, 2_000_000),
    "rocks": (300.0, 60_000_000),
    "paint": (150.0, 120_000_000),
}


RESTYLE_NEEDS = (
    "a palette-only restyle needs the raster cache a full render kept at this size; "
    "render once with “keep the raster cache” ticked"
)


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
        layers = options.get("layers", list(DEFAULT_LAYERS))
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
        restyle = _bool(options, "restyle", False)
        if restyle and recipe == "kernel-only":
            raise PresetError(
                "a palette-only restyle draws from the raster cache; kernel-only has none"
            )
        return {
            "layers": [layer for layer in RENDER_LAYERS if layer in layers],
            "size": size,
            "recipe": recipe,
            "top": _bool(options, "top", True),
            "keep_cache": _bool(options, "keep_cache", False),
            "restyle": restyle,
            "light": _bool(options, "light", True),
            "titan_trees": _bool(options, "titan_trees", True),
        }
    if preset == "artwork":
        enhance = _bool(options, "enhance", False)
        if enhance and not vulkan_available():
            raise PresetError("upscaling needs a Vulkan GPU, and none was found at server start")
        return {"enhance": enhance, "tiles_2x": _bool(options, "tiles_2x", True)}
    if preset in INPUT_PRESETS:
        return {}
    raise PresetError(f"no preset “{preset}”; known: {', '.join(PRESETS)}")


def _area(size: int) -> float:
    return (size / FULL_PX) ** 2


def _render_seconds(options: dict) -> dict[str, float]:
    area = _area(options["size"])
    kernel = options["recipe"] == "kernel-only"
    stages = {"prep": RENDER_STAGE_S["prep"]}
    if not kernel and not options.get("_cache_hit") and not options.get("restyle"):
        stages["sweep"] = RENDER_STAGE_S["sweep"]
        stages["direct"] = max(DIRECT_FLOOR_S, RENDER_STAGE_S["direct"] * area)
        if options["top"]:
            stages["top"] = max(TOP_FLOOR_S, RENDER_STAGE_S["top"] * area)
    layers = options["layers"]
    draw = RENDER_DRAW_S["ground"] + RENDER_DRAW_S["layer"] * len(layers)
    stages["draw"] = draw * area + 2.0
    if options.get("light"):
        kept = options.get("restyle") and light_kept(options["size"])
        stages["light"] = (LIGHT_KEPT_S if kept else LIGHT_STAGE_S) * area + 2.0
    cut = RENDER_LAYER_S["cut"] * (LIGHT_CUT_FACTOR if options.get("light") else 1.0)
    for layer in layers:
        stages[f"cut:{layer}"] = cut * area + 2.0
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
        if any(bool(was.get(key)) != options[key] for key in ("restyle", "light")):
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


#: The raster caches a palette-only restyle draws from, by their directory names.
CACHE_PARTS = ("direct.cache", "top.cache", "meshes.cache")
#: The light a lit render keeps beside them, which a restyle of the same surface installs.
KEPT_LIGHT_PART = "light.kept"


def cache_ready(size: int) -> bool:
    """Whether a full render kept every raster a restyle at this size needs."""
    return all((cache_dir(size) / part / "meta.json").is_file() for part in CACHE_PARTS)


def light_kept(size: int) -> bool:
    """Whether a lit render kept its light in the cache at this size."""
    return (cache_dir(size) / KEPT_LIGHT_PART / "meta.json").is_file()


def cached_sizes() -> list[int]:
    return [size for size in RENDER_SIZES if cache_ready(size)]


def estimate(preset: str, options: dict) -> dict:
    """``{seconds, keep_bytes, transient_bytes, free_bytes, needs_bytes, ok, reason}``."""
    options = normalise(preset, options)
    if preset == "render":
        area = _area(options["size"])
        cached = cache_ready(options["size"])
        if options["restyle"] and not cached:
            raise PresetError(RESTYLE_NEEDS)
        seconds = sum(_render_seconds({**options, "_cache_hit": cached}).values())
        per_layer = RENDER_KEEP_BYTES + (UNLIT_KEEP_BYTES if options["light"] else 0)
        keep = len(options["layers"]) * max(RENDER_KEEP_FLOOR, int(per_layer * area))
        keep += int(LIGHT_KEEP_BYTES * area) if options["light"] else 0
        transient = int(CACHE_BYTES_FULL * area) + keep // max(1, len(options["layers"]))
        if options["light"]:
            transient += int((LIGHT_SCRATCH_BYTES + CROWN_SCRATCH_BYTES) * area)
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


def mapgen_src() -> Path:
    """What a generator child needs on its path to import ``mapgen``."""
    return tools_dir() / "mapgen" / "src"


def can_generate() -> dict:
    """Whether this checkout can run the generators here, each check under a millisecond."""
    gen = all(importlib.util.find_spec(name) is not None for name in GEN_MODULES)
    tools = (mapgen_src() / "mapgen" / "cli.py").is_file()
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
        "vulkan": vulkan_available(),
        "ok": reason is None,
        "reason": reason,
    }


def _recipe(options: dict) -> int:
    return (
        RENDER_RECIPE_KERNEL_ONLY if options["recipe"] == "kernel-only" else RENDER_RECIPE_CURRENT
    )


def _planned_axes(family: str, recipe: int, style: str, cl: int | None, size: int) -> dict:
    """What a type being built will be, until its sidecar says so itself."""
    table = RENDER_RECIPES if family == "render" else ARTWORK_RECIPES
    return {
        "game": {"cl": cl},
        "inputs": {"heightfield": {"cl": cl, "generator_version": HEIGHTFIELD_GENERATOR_VERSION}}
        if family == "render"
        else {"artwork_sheet": {"cl": cl}},
        "renderer": {"family": family, "recipe": recipe, "version": table[recipe]["version"],
                     "label": table[recipe]["label"], "size_px": size},
        "style": {"id": style, "version": STYLES[style]["version"], "label": STYLES[style]["label"]},
        "inferred": True,
    }  # fmt: skip


def plan(preset: str, options: dict, job_id: str, cl: int | None, taken: set[str]) -> dict:
    """``{script, argv, produces}`` for one job; ``produces`` maps new type ids to entries."""
    options = normalise(preset, options)
    local = registry.local_dir()
    maps = registry.maps_dir()
    game = str(config.game_root())
    produces: dict[str, dict] = {}
    if preset == "render":
        recipe = _recipe(options)
        if options["restyle"] and not cache_ready(options["size"]):
            raise PresetError(RESTYLE_NEEDS)
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
        argv.append("--light" if options["light"] else "--no-light")
        if not options["titan_trees"]:
            argv.append("--no-titan-trees")
        if options["keep_cache"] or options["restyle"] or cache_dir(options["size"]).is_dir():
            argv += ["--cache-dir", str(cache_dir(options["size"])), "--keep-direct"]
        if options["restyle"]:
            argv.append("--restyle")
        for layer in options["layers"]:
            style = ax.LAYER_STYLE[layer]
            ident = ax.derive_id(STYLES[style]["label"], recipe, cl, taken | set(produces))
            entry = registry.new_entry(
                ident, "render", layer, f"{registry.MAPS_DIR_NAME}/{job_id}/{layer}",
                "meta.json", "generated",
            )  # fmt: skip
            entry.update(status="building", job=job_id, size_px=options["size"])
            entry["axes"] = _planned_axes("render", recipe, style, cl, options["size"])
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
        entry["axes"] = _planned_axes("artwork", recipe, "artwork", cl, 8192)
        produces[ident] = entry
    elif preset == "paint":
        argv = ["--game", game, "--out-dir", str(local / "paint")]
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
    return {
        "script": SCRIPTS[preset],
        "command": COMMANDS[preset],
        "argv": argv,
        "produces": produces,
        "options": options,
    }
