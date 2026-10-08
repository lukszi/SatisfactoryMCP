"""A map's provenance axes -- data inputs, renderer, style, light -- and the verdicts on them.

Stale means the DATA under a picture is outdated; a newer renderer or palette is only an
offer. Every verdict is computed on read from the sidecar and the inputs on disk, never
stored. docs/maps_contract.md §3 is the specification.
"""

from __future__ import annotations

import json
import re
from collections import ChainMap
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast

from ...core.gameassets.provenance import changelist, read_path
from ...core.gameassets.versions import (
    ARTWORK_RECIPES,
    PROVENANCE_SCHEMA,
    READER_VERSIONS,
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPES,
    RETIRED_STYLES,
    STYLES,
)
from ...core.jsontypes import JsonObject, JsonValue
from .views import MapAxes, MapFreshness, MapInputNow, MapInputsState, MapRerender, MapStaleAxis

__all__ = [
    "INPUT_DIRS",
    "axes_from_sidecar",
    "current_state",
    "derive_id",
    "dict_at",
    "display_name",
    "freshness",
    "int_or_none",
    "sort_key",
    "style_tone",
]

RENDERS_GENERATOR = "tools/gen_map_renders.py"
ARTWORK_GENERATOR = "tools/gen_map_image.py"

Tone = Literal["light", "dark"]

#: The version tables, read as the mappings they are; the styles with the retired ones, whose
#: maps are still listed and served.
RENDER_TABLE: Mapping[int, Mapping[str, object]] = RENDER_RECIPES
ARTWORK_TABLE: Mapping[int, Mapping[str, object]] = ARTWORK_RECIPES
STYLE_TABLE: Mapping[str, Mapping[str, object]] = ChainMap(STYLES, RETIRED_STYLES)

#: The planes each legacy recipe opened, for sidecars written before ``provenance`` existed.
LEGACY_PLANES = {
    1: ["height"],
    2: ["height", "prov", "water", "waterq"],
    3: ["height", "prov", "water", "waterq", "density"],
    4: ["height", "prov", "water", "waterq", "density", "terrain"],
    5: ["height", "prov", "water", "waterq", "density", "terrain"],
}

LAYER_STYLE = {
    "terrain": "terrain-hypsometric",
    "satellite": "satellite-biome",
    "painted": "satellite-painted",
    "relief": "relief-muted",
    "relief-dark": "relief-night",
    "map": "artwork",
}

#: The inputs with a directory of their own under ``data/local``, as (dir, sidecar, version key).
INPUT_DIRS = {
    "heightfield": ("heightmap", "meta.json", "generator_version"),
    "caves": ("caves", "meta.json", "caves_version"),
    "rocks": ("heightmap", "rocks.json", "rocks_version"),
    "paint": ("paint", "meta.json", "generator_version"),
}

INPUT_NAMES = {
    "heightfield": "heightfield",
    "caves": "cave masks",
    "rocks": "rock collision",
    "paint": "paint layers",
    "biome_raster": "biome raster reader",
    "artwork_sheet": "artwork sheet reader",
    "cliff_geometry": "cliff geometry reader",
    "render_meshes": "render mesh reader",
    "river_splines": "river spline reader",
    "rock_families": "rock family reader",
    "titan_trees": "titan tree reader",
    "waterfalls": "waterfall reader",
}


def int_or_none(value: object) -> int | None:
    """``value`` when it is a plain int, never a bool; else ``None``."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _text_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


def dict_at(obj: object, *keys: str) -> JsonObject:
    """The dict at ``obj[k1][k2]...``, or ``{}`` where any step is missing or not a dict."""
    for key in keys:
        obj = cast(JsonObject, obj).get(key) if isinstance(obj, dict) else None
    return cast(JsonObject, obj) if isinstance(obj, dict) else {}


def _legacy_renders(block: JsonObject) -> MapAxes:
    sources = dict_at(block, "sources")
    field = dict_at(sources, "heightfield")
    cl = changelist(field.get("game_version_pinned"))
    recipe = int_or_none(block.get("recipe")) or 0
    known = RENDER_TABLE.get(recipe, {})
    render = dict_at(block, "render")
    regime = dict_at(render, "two_regime")
    layer = str(block.get("layer") or "")
    inputs: JsonObject = {
        "heightfield": {
            "cl": cl,
            "generator_version": int_or_none(field.get("generator_version")),
            "planes": [*LEGACY_PLANES.get(recipe, ["height"])],
            "digest": None,
        }
    }
    for name, key in (("artwork_sheet", "artwork_detail"), ("biome_raster", "biome_raster")):
        if key in sources:
            inputs[name] = {"cl": cl, "reader_version": None, "digest": None, "inferred": True}
    if "cliff_geometry" in sources:
        inputs["cliff_geometry"] = {"cl": cl, "reader_version": None, "inferred": True}
    style = LAYER_STYLE.get(layer, layer or "unknown")
    return {
        "schema": PROVENANCE_SCHEMA,
        "game": {"cl": cl},
        "inputs": inputs,
        "renderer": {
            "family": "render",
            "recipe": recipe,
            "version": 1,
            "label": str(known.get("label", f"recipe {recipe}")),
            "sampler": _text_or_none(known.get("sampler")),
            "two_regime": bool(regime.get("enabled", known.get("two_regime", False))),
            "size_px": int_or_none(render.get("width_px")),
            "subsamples": int_or_none(regime.get("subsamples_per_axis")),
        },
        "style": {"id": style, "version": 1,
                  "label": str(STYLE_TABLE.get(style, {}).get("label", style)),
                  "digest": None, "inferred": True},
        "inferred": True,
    }  # fmt: skip


def _legacy_artwork(block: JsonObject) -> MapAxes:
    slices = dict_at(block, "sources", "map_slices")
    cl = changelist(slices.get("game_version_raw")) or changelist(slices.get("game_version_pinned"))
    tiles = dict_at(block, "tiles")
    recipe = int_or_none(read_path(tiles, ("enhancement", "recipe")))
    if recipe is None:
        recipe = 1 if tiles.get("enhanced") else 0
    image = dict_at(block, "image")
    return {
        "schema": PROVENANCE_SCHEMA,
        "game": {"cl": cl},
        "inputs": {"artwork_sheet": {"cl": cl, "reader_version": None, "digest": None,
                                     "inferred": True}},
        "renderer": {"family": "artwork", "recipe": recipe, "version": 1,
                     "label": str(ARTWORK_TABLE.get(recipe, {}).get("label", f"recipe {recipe}")),
                     "size_px": int_or_none(image.get("width_px"))},
        "style": {"id": "artwork", "version": 1, "label": "artwork", "digest": None},
        "inferred": True,
    }  # fmt: skip


def axes_from_sidecar(sidecar: object, kind: str = "render") -> MapAxes:
    """The axes a sidecar states, derived from its legacy fields when it has no ``provenance``.

    A sidecar nothing can be read from still answers: its axes are empty and ``inferred``.
    """
    block = dict_at(sidecar, "_meta")
    native = block.get("provenance")
    if isinstance(native, dict) and isinstance(native.get("renderer"), dict):
        return {**native, "inferred": False}
    generator = str(block.get("generator") or "")
    if generator == RENDERS_GENERATOR:
        return _legacy_renders(block)
    if generator == ARTWORK_GENERATOR or kind == "artwork":
        return _legacy_artwork(block)
    return {
        "schema": PROVENANCE_SCHEMA,
        "game": {"cl": None},
        "inputs": {},
        "renderer": {"family": kind, "recipe": None, "version": None, "label": "unknown"},
        "style": {"id": "unknown", "version": None, "label": kind},
        "inferred": True,
    }


def read_json(path: Path) -> JsonObject | None:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _input_now(local: Path, name: str) -> MapInputNow | None:
    folder, sidecar, version_key = INPUT_DIRS[name]
    meta = read_json(local / folder / sidecar)
    if meta is None:
        return None
    pin = read_path(meta, ("sources", "game", "game_version_raw")) or read_path(
        meta, ("sources", "game", "build_raw")
    )
    cl = changelist(pin) or changelist(read_path(meta, ("sources", "game", "game_version_pinned")))
    files = dict_at(meta, "files")
    return {
        "version": int_or_none(meta.get(version_key)),
        "cl": cl if cl is not None else int_or_none(meta.get("cl")),
        "digest": _text_or_none(meta.get("digest")),
        "planes": sorted(files),
        "transcribed": meta.get("transcribed"),
    }


def current_state(local: Path, game_cl: int | None) -> MapInputsState:
    """What every input is now: the installed build and each input directory's sidecar."""
    return {
        "game_cl": game_cl,
        "inputs": {name: _input_now(local, name) for name in INPUT_DIRS},
        "readers": dict(READER_VERSIONS),
    }


PLANE_FILES = {
    "height": "height.i16.z",
    "prov": "prov.u8.z",
    "water": "water.i16.z",
    "waterq": "waterq.u8.z",
    "density": "density.u8.z",
    "terrain": "terrain.u16.z",
    "top": "top.i16.z",
}


def _inputs_to_rebuild(recipe: int, current: MapInputsState) -> list[str]:
    """Which inputs have to be rebuilt before ``recipe`` can draw from what is on disk."""
    wanted = dict_at(RENDER_TABLE[recipe], "requires", "heightfield")
    field = current["inputs"].get("heightfield")
    if field is None:
        return ["heightfield"]
    have = set(field["planes"])
    needed = wanted.get("planes")
    names = [str(p) for p in needed] if isinstance(needed, list) else []
    planes = all(PLANE_FILES.get(p, p) in have for p in names)
    if (field["version"] or 0) < (int_or_none(wanted.get("min_version")) or 0) or not planes:
        return ["heightfield"]
    return []


def _stale_inputs(axes: MapAxes, current: MapInputsState) -> tuple[list[MapStaleAxis], bool]:
    """``(stale, incomplete)``: what is out of date under a picture, and whether its record
    is too thin to say."""
    stale: list[MapStaleAxis] = []
    inputs = dict_at(axes, "inputs")
    recorded_cls = [int_or_none(v.get("cl")) for v in inputs.values() if isinstance(v, dict)]
    known_cls = [cl for cl in recorded_cls if cl is not None]
    game_cl = current.get("game_cl")
    if game_cl is not None and known_cls and min(known_cls) < game_cl:
        stale.append({"axis": "game", "text": f"older game build ({min(known_cls)} → {game_cl})"})
    incomplete = bool(axes.get("inferred"))
    for name, entry in inputs.items():
        if not isinstance(entry, dict):
            continue
        words = INPUT_NAMES.get(name, name)
        if name in INPUT_DIRS:
            now = current["inputs"].get(name)
            had = int_or_none(entry.get("generator_version", entry.get("version")))
            if now is None or had is None or now["version"] is None:
                incomplete = incomplete or had is None
                continue
            if now["version"] > had:
                stale.append({"axis": name, "text": f"newer {words} (v{had} → v{now['version']})"})
            elif entry.get("digest") and now["digest"] and entry["digest"] != now["digest"]:
                stale.append({"axis": name, "text": f"{words} changed"})
            if not entry.get("digest"):
                incomplete = True
        else:
            had = int_or_none(entry.get("reader_version"))
            now_reader = current["readers"].get(name)
            if had is None:
                incomplete = True
            elif now_reader is not None and now_reader > had:
                stale.append({"axis": name, "text": f"newer {words} (v{had} → v{now_reader})"})
    return stale, incomplete


def _rerender_offer(axes: MapAxes, current: MapInputsState) -> MapRerender | None:
    """The current renderer, offered where it is newer than the one that drew this picture."""
    renderer = dict_at(axes, "renderer")
    recipe = int_or_none(renderer.get("recipe"))
    version = int_or_none(renderer.get("version")) or 0
    if renderer.get("family") != "render" or recipe is None:
        return None
    best = RENDER_TABLE[RENDER_RECIPE_CURRENT]
    if (RENDER_RECIPE_CURRENT, int_or_none(best.get("version")) or 0) <= (recipe, version):
        return None
    needs = _inputs_to_rebuild(RENDER_RECIPE_CURRENT, current)
    label = f"{best['label']} r{RENDER_RECIPE_CURRENT}"
    return {
        "recipe": RENDER_RECIPE_CURRENT,
        "label": label,
        "needs": needs,
        "text": f"newer renderer: {label}" + (" after rebuilding the heightfield" if needs else ""),
    }


def _restyle_offer(axes: MapAxes) -> bool:
    """Whether the style table has a newer version of this picture's palette."""
    style = dict_at(axes, "style")
    known_style = STYLE_TABLE.get(str(style.get("id")))
    had = int_or_none(style.get("version"))
    newest = int_or_none(known_style.get("version")) if known_style else None
    return had is not None and newest is not None and newest > had


def freshness(axes: MapAxes, current: MapInputsState) -> MapFreshness:
    """``{stale: [{axis, text}], rerender, restyle, incomplete}`` for one map's axes; a
    retired style is offered neither, since nothing draws it now."""
    stale, incomplete = _stale_inputs(axes, current)
    retired = str(dict_at(axes, "style").get("id")) in RETIRED_STYLES
    return {
        "stale": stale,
        "rerender": None if retired else _rerender_offer(axes, current),
        "restyle": not retired and _restyle_offer(axes),
        "incomplete": incomplete,
    }


def data_changelist(axes: MapAxes) -> int | None:
    """The build the picture's data came from: the heightfield's, else the oldest input's."""
    inputs = dict_at(axes, "inputs")
    field_cl = int_or_none(dict_at(inputs, "heightfield").get("cl"))
    if field_cl is not None:
        return field_cl
    recorded = [int_or_none(v.get("cl")) for v in inputs.values() if isinstance(v, dict)]
    known = [cl for cl in recorded if cl is not None]
    return min(known) if known else int_or_none(read_path(axes, ("game", "cl")))


def _heightfield_version(axes: MapAxes) -> int | None:
    return int_or_none(read_path(axes, ("inputs", "heightfield", "generator_version")))


def renderer_label(axes: MapAxes) -> str:
    renderer = dict_at(axes, "renderer")
    recipe = renderer.get("recipe")
    label = str(renderer.get("label") or "unknown")
    if renderer.get("family") == "artwork" and recipe == 0:
        return "plain"
    return f"{label} r{recipe}" if recipe is not None else label


def style_label(axes: MapAxes) -> str:
    style = dict_at(axes, "style")
    return str(style.get("label") or style.get("id") or "unknown")


def style_tone(axes: MapAxes) -> Tone:
    """``light`` or ``dark``: the sidecar's word, else the style table's, else light."""
    style = dict_at(axes, "style")
    tone = style.get("tone") or STYLE_TABLE.get(str(style.get("id")), {}).get("tone")
    return "dark" if tone == "dark" else "light"


def data_label(axes: MapAxes) -> str:
    cl = data_changelist(axes)
    heightfield_version = _heightfield_version(axes)
    text = f"data {cl if cl is not None else '?'}"
    return text + (f"/hf v{heightfield_version}" if heightfield_version is not None else "")


def light_label(axes: MapAxes) -> str | None:
    """The light model a layer drawn unlit is relit with; ``None`` for one drawn lit."""
    light = dict_at(axes, "light")
    return str(light.get("label") or light.get("id") or "lit") if light else None


def display_name(axes: MapAxes, with_size: bool = False) -> str:
    """``{style} · {renderer} · data {cl}/hf v{n}``, then the light and the size when present."""
    parts = [style_label(axes), renderer_label(axes), data_label(axes)]
    if light_label(axes):
        parts.append(str(light_label(axes)))
    size = int_or_none(read_path(axes, ("renderer", "size_px")))
    if with_size and size:
        parts.append(f"{size} px")
    return " · ".join(parts)


def sort_key(axes: MapAxes, ident: str) -> tuple[str, int, int, int, int, int, int, str]:
    """Style, then data (newest build, then highest input versions), renderer, style version."""
    renderer = dict_at(axes, "renderer")
    style = dict_at(axes, "style")
    return (
        style_label(axes),
        -(data_changelist(axes) or 0),
        -(_heightfield_version(axes) or 0),
        -(int_or_none(renderer.get("recipe")) or 0),
        -(int_or_none(renderer.get("version")) or 0),
        -(int_or_none(style.get("version")) or 0),
        -(int_or_none(renderer.get("size_px")) or 0),
        ident,
    )


ID_SHAPE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def derive_id(style: str, recipe: int | None, cl: int | None, taken: set[str]) -> str:
    """``{style}-r{recipe}-{cl}``, then ``-2``, ``-3`` while that is taken. A handle, not a version."""
    slug = re.sub(r"[^a-z0-9]+", "-", style.lower()).strip("-")[:16] or "map"
    base = f"{slug}-r{recipe if recipe is not None else 'x'}-{cl if cl is not None else 'x'}"
    base = base[:36]
    ident, n = base, 2
    while ident in taken:
        ident, n = f"{base}-{n}", n + 1
    return ident
