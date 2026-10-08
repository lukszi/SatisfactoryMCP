"""Generation presets: a request names a preset and enumerated options, never a command line.

Every argument is built here from a whitelist, and every path in it is chosen by the server
under ``data/local``; the install is named only by ``--game``. The estimates come from the
stage timings of one measured full render, scaled by area, until a finished job of the same
preset has left real ones in the manifest's history. docs/maps_contract.md §4.
"""

from __future__ import annotations

import importlib.util
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ... import config
from ...core.gameassets.versions import (
    HEIGHTFIELD_GENERATOR_VERSION,
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPE_KERNEL_ONLY,
)
from ...core.gpu import vulkan_available
from ...core.jsontypes import is_object_list
from . import axes as ax
from . import registry
from .views import (
    ArtworkOptions,
    GeneratorPlan,
    InputOptions,
    JobOptions,
    Layer,
    MapAxes,
    MapCanGenerate,
    MapEntry,
    MapEstimateResponse,
    RenderOptions,
    RenderRecipe,
)

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

RENDER_LAYERS: tuple[Layer, ...] = ("terrain", "painted", "relief-dark")
#: What a render job draws when it names no layers. The painted one needs the paint input,
#: which the page queues first where it is missing.
DEFAULT_LAYERS: tuple[Layer, ...] = ("terrain", "painted")
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

#: Seconds per stage of one full 32768 px render on the CPU, from the log of a cold, lit render
#: of the three layers (2026-10-08, docs/maps_contract.md section 4.3): ``prep``, and ``paint``
#: on top of it when the painted layer is drawn; ``top`` runs on to the draw. ``direct`` and
#: ``top`` scale with the sheet's area and never drop under their floors: the triangles are
#: the same at any size.
RENDER_STAGE_S = {"prep": 31.0, "paint": 153.0, "sweep": 34.0, "direct": 39.0, "top": 157.0}
#: Per layer: the wait for its trees once the draw and the light are done, since the bands
#: are cut as they settle (section 4.3).
RENDER_LAYER_S = {"cut": 0.2}
#: The draw, one pass over every layer with the light baking beside it: the ground the layers
#: share, once, and each layer's colour over it. The three layers' 918 s (2026-10-08), split as
#: the 2026-10-07 windows split it, 150 s of ground to 190 a layer.
RENDER_DRAW_S = {"ground": 191.0, "layer": 242.5}
#: ``--light``: the bake's rows left once the draw is done, 314 of its 1,136 s on 14 workers
#: (2026-10-08). The scratch is the light cache while it runs, and the crown occluder the paint
#: store adds to it whatever the layers, written once (docs/spatial-and-map.md section 29,
#: "Scratch").
LIGHT_STAGE_S = 314.0
#: A restyle at a size whose cache keeps a light installs it instead of baking: hard links to
#: the full-size pyramid's 43,690 files, 6.5 to 9.4 s on the reference machine
#: (docs/spatial-and-map.md section 29, "Kept light").
LIGHT_KEPT_S = 10.0
#: The light's pyramid at full size, its folded horizon tiles at q95: 4.06 GB (2026-10-08).
LIGHT_KEEP_BYTES = 4_060_000_000
UNLIT_KEEP_BYTES = 450_000_000
LIGHT_SCRATCH_BYTES = 15_570_000_000
CROWN_SCRATCH_BYTES = 5_370_000_000
#: The default-sun terms a lit render that keeps its cache moves out of the scratch into
#: ``light.kept/``, 4 bytes a pixel; the kept tiles are hard links to the map's own.
KEPT_TERMS_BYTES = 4 * FULL_PX * FULL_PX
#: The direct and top stages at 2048 (2026-10-08).
DIRECT_FLOOR_S = 35.0
TOP_FLOOR_S = 88.0
RENDER_KEEP_BYTES = 890_000_000
RENDER_KEEP_FLOOR = 10_000_000
#: The raster caches of one full render in the zstd band store: 2.23 GB measured (2026-10-08),
#: 1.25 GB of it the overhangs' undersides and floors (docs/spatial-and-map.md section 39).
CACHE_BYTES_FULL = 2_230_000_000
SPARE_BYTES = 2_000_000_000

#: Seconds and bytes kept of the presets that do not scale: the artwork, plain or upscaled,
#: and the inputs.
FIXED_ARTWORK = {"plain": (180.0, 160_000_000), "enhanced": (900.0, 600_000_000)}
FIXED = {
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


def _bool(options: Mapping[str, object], key: str, default: bool) -> bool:
    value = options.get(key, default)
    if not isinstance(value, bool):
        raise PresetError(f"{key} is true or false, not {value!r}")
    return value


def _render_options(options: Mapping[str, object]) -> RenderOptions:
    asked_layers = options.get("layers", list(DEFAULT_LAYERS))
    layers = asked_layers if is_object_list(asked_layers) else []
    if not layers or any(layer not in RENDER_LAYERS for layer in layers):
        raise PresetError(f"layers is a non-empty list of {', '.join(RENDER_LAYERS)}")
    asked_size = options.get("size", FULL_PX)
    size = next((s for s in RENDER_SIZES if s == asked_size), None)
    if size is None:
        raise PresetError(f"size is one of {', '.join(map(str, RENDER_SIZES))}")
    asked_recipe = options.get("recipe", "current")
    recipe: RenderRecipe
    if asked_recipe == "current":
        recipe = "current"
    elif asked_recipe == "kernel-only":
        recipe = "kernel-only"
    else:
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


def _artwork_options(options: Mapping[str, object]) -> ArtworkOptions:
    enhance = _bool(options, "enhance", False)
    if enhance and not vulkan_available():
        raise PresetError("upscaling needs a Vulkan GPU, and none was found at server start")
    return {"enhance": enhance, "tiles_2x": _bool(options, "tiles_2x", True)}


def _input_options(preset: str) -> InputOptions:
    if preset not in INPUT_PRESETS:
        raise PresetError(f"no preset “{preset}”; known: {', '.join(PRESETS)}")
    return {}


def normalise(preset: str, options: Mapping[str, object] | None) -> JobOptions:
    """``options`` as the preset takes them, every value checked against its whitelist."""
    if preset == "render":
        return _render_options(options or {})
    if preset == "artwork":
        return _artwork_options(options or {})
    return _input_options(preset)


def _area(size: float) -> float:
    return (size / FULL_PX) ** 2


def _render_seconds(options: RenderOptions, cache_hit: bool = False) -> dict[str, float]:
    area = _area(options["size"])
    kernel = options["recipe"] == "kernel-only"
    layers = options["layers"]
    paint = RENDER_STAGE_S["paint"] if "painted" in layers else 0.0
    stages = {"prep": RENDER_STAGE_S["prep"] + paint}
    if not kernel and not cache_hit and not options.get("restyle"):
        stages["sweep"] = RENDER_STAGE_S["sweep"]
        stages["direct"] = max(DIRECT_FLOOR_S, RENDER_STAGE_S["direct"] * area)
        if options["top"]:
            stages["top"] = max(TOP_FLOOR_S, RENDER_STAGE_S["top"] * area)
    draw = RENDER_DRAW_S["ground"] + RENDER_DRAW_S["layer"] * len(layers)
    stages["draw"] = draw * area + 2.0
    if options.get("light"):
        kept = options.get("restyle") and light_kept(options["size"])
        stages["light"] = (LIGHT_KEPT_S if kept else LIGHT_STAGE_S) * area + 2.0
    for layer in layers:
        stages[f"cut:{layer}"] = RENDER_LAYER_S["cut"] * area + 2.0
    return stages


def stage_plan(preset: str, options: Mapping[str, object]) -> dict[str, float]:
    """The stages a job passes through and the seconds each is expected to take.

    ``options`` are a job's, as ``normalise`` left them."""
    if preset == "render":
        return _render_seconds(cast(RenderOptions, options))
    if preset == "artwork":
        return {"run": FIXED_ARTWORK["enhanced" if options["enhance"] else "plain"][0]}
    return {"run": FIXED[preset][0]}


def _scaled_history(preset: str, render: RenderOptions | None) -> float | None:
    """The last finished job of the same preset, scaled by area, when there is one."""
    for row in reversed(registry.read()["history"]):
        if not isinstance(row, dict):
            continue
        seconds = row.get("seconds")
        if row.get("preset") != preset or not isinstance(seconds, int | float):
            continue
        if render is None:
            return float(seconds)
        was = ax.dict_at(row, "options")
        size = was.get("size")
        if was.get("recipe") != render["recipe"] or not isinstance(size, int | float) or not size:
            continue
        if (
            bool(was.get("restyle")) != render["restyle"]
            or bool(was.get("light")) != render["light"]
        ):
            continue
        layers = was.get("layers")
        per_layer = seconds / max(1, len(layers) if isinstance(layers, list) else 0)
        return per_layer * len(render["layers"]) * max(_area(render["size"]) / _area(size), 0.05)
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


def keeps_cache(options: RenderOptions) -> bool:
    """Whether a render job keeps its raster caches, and with the light its terms, for later
    runs at its size: asked to, a restyle, or a cache of that size already there."""
    return options["keep_cache"] or options["restyle"] or cache_dir(options["size"]).is_dir()


def _keeps_new_light(options: RenderOptions) -> bool:
    """Whether a lit job leaves a kept light where none is: it keeps its raster caches
    (``keeps_cache``) or draws the kernel only, which deletes neither."""
    keeps = keeps_cache(options) or options["recipe"] == "kernel-only"
    return options["light"] and keeps and not light_kept(options["size"])


def _render_cost(options: RenderOptions) -> tuple[float, int, int]:
    """``(seconds, bytes kept, bytes needed while it runs)`` of one render job."""
    area = _area(options["size"])
    cached = cache_ready(options["size"])
    if options["restyle"] and not cached:
        raise PresetError(RESTYLE_NEEDS)
    seconds = sum(_render_seconds(options, cache_hit=cached).values())
    per_layer = RENDER_KEEP_BYTES + (UNLIT_KEEP_BYTES if options["light"] else 0)
    keep = len(options["layers"]) * max(RENDER_KEEP_FLOOR, int(per_layer * area))
    keep += int(LIGHT_KEEP_BYTES * area) if options["light"] else 0
    transient = int(CACHE_BYTES_FULL * area) + keep // max(1, len(options["layers"]))
    if options["light"]:
        transient += int((LIGHT_SCRATCH_BYTES + CROWN_SCRATCH_BYTES) * area)
    if _keeps_new_light(options):
        # The terms move out of the scratch, so they are kept rather than needed twice.
        terms = int(KEPT_TERMS_BYTES * area)
        keep, transient = keep + terms, transient - terms
    return seconds, keep, transient


def estimate(preset: str, options: Mapping[str, object] | None) -> MapEstimateResponse:
    """``{seconds, keep_bytes, transient_bytes, free_bytes, needs_bytes, ok, reason}``."""
    render = _render_options(options or {}) if preset == "render" else None
    if render is not None:
        seconds, keep, transient = _render_cost(render)
    elif preset == "artwork":
        art = _artwork_options(options or {})
        seconds, keep = FIXED_ARTWORK["enhanced" if art["enhance"] else "plain"]
        transient = keep
    else:
        _input_options(preset)
        seconds, keep = FIXED[preset]
        transient = keep
    measured = _scaled_history(preset, render)
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


def can_generate() -> MapCanGenerate:
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


def _planned_axes(family: str, recipe: int, style: str, cl: int | None, size: int) -> MapAxes:
    """What a type being built will be, until its sidecar says so itself."""
    known = (ax.RENDER_TABLE if family == "render" else ax.ARTWORK_TABLE)[recipe]
    palette = ax.STYLE_TABLE[style]
    return {
        "game": {"cl": cl},
        "inputs": {"heightfield": {"cl": cl, "generator_version": HEIGHTFIELD_GENERATOR_VERSION}}
        if family == "render"
        else {"artwork_sheet": {"cl": cl}},
        "renderer": {"family": family, "recipe": recipe,
                     "version": ax.int_or_none(known["version"]),
                     "label": str(known["label"]), "size_px": size},
        "style": {"id": style, "version": ax.int_or_none(palette["version"]),
                  "label": str(palette["label"])},
        "inferred": True,
    }  # fmt: skip


def _building(entry: MapEntry, job_id: str, size: int, axes: MapAxes) -> MapEntry:
    entry["status"] = "building"
    entry["job"] = job_id
    entry["size_px"] = size
    entry["axes"] = axes
    return entry


def _render_plan(
    options: RenderOptions, job_id: str, cl: int | None, taken: set[str]
) -> tuple[list[str], dict[str, MapEntry]]:
    local = registry.local_dir()
    game = str(config.game_root())
    recipe = (
        RENDER_RECIPE_KERNEL_ONLY if options["recipe"] == "kernel-only" else RENDER_RECIPE_CURRENT
    )
    if options["restyle"] and not cache_ready(options["size"]):
        raise PresetError(RESTYLE_NEEDS)
    argv = [
        "--game", game,
        "--field", str(local / "heightmap"),
        "--out-dir", str(registry.maps_dir()),
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
    if keeps_cache(options):
        argv += ["--cache-dir", str(cache_dir(options["size"])), "--keep-direct"]
    if options["restyle"]:
        argv.append("--restyle")
    produces: dict[str, MapEntry] = {}
    for layer in options["layers"]:
        style = ax.LAYER_STYLE[layer]
        name = str(ax.STYLE_TABLE[style]["label"])
        ident = ax.derive_id(name, recipe, cl, taken | set(produces))
        entry = registry.new_entry(
            ident, "render", layer, f"{registry.MAPS_DIR_NAME}/{job_id}/{layer}",
            "meta.json", "generated",
        )  # fmt: skip
        axes = _planned_axes("render", recipe, style, cl, options["size"])
        produces[ident] = _building(entry, job_id, options["size"], axes)
    return argv, produces


def _artwork_plan(
    options: ArtworkOptions, job_id: str, cl: int | None, taken: set[str]
) -> tuple[list[str], dict[str, MapEntry]]:
    local = registry.local_dir()
    game = str(config.game_root())
    recipe = 2 if options["enhance"] else 0
    ident = ax.derive_id("artwork", recipe, cl, taken)
    rel = f"{registry.MAPS_DIR_NAME}/{ident}"
    argv = ["--game", game, "--out-dir", str(local / rel)]
    if options["enhance"]:
        argv.append("--enhance")
    if not options["tiles_2x"]:
        argv.append("--no-tiles-2x")
    entry = registry.new_entry(ident, "artwork", "map", rel, "map.json", "generated")
    axes = _planned_axes("artwork", recipe, "artwork", cl, 8192)
    return argv, {ident: _building(entry, job_id, 8192, axes)}


def _input_argv(preset: str) -> list[str]:
    local = registry.local_dir()
    game = str(config.game_root())
    if preset == "paint":
        return ["--game", game, "--out-dir", str(local / "paint")]
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
    return argv


def plan(
    preset: str, options: Mapping[str, object] | None, job_id: str, cl: int | None, taken: set[str]
) -> GeneratorPlan:
    """``{script, argv, produces}`` for one job; ``produces`` maps new type ids to entries."""
    checked: JobOptions
    if preset == "render":
        render = _render_options(options or {})
        argv, produces = _render_plan(render, job_id, cl, taken)
        checked = render
    elif preset == "artwork":
        art = _artwork_options(options or {})
        argv, produces = _artwork_plan(art, job_id, cl, taken)
        checked = art
    else:
        checked = _input_options(preset)
        argv, produces = _input_argv(preset), {}
    return {
        "script": SCRIPTS[preset],
        "command": COMMANDS[preset],
        "argv": argv,
        "produces": produces,
        "options": checked,
    }
