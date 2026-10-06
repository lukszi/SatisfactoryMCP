"""A map's provenance axes -- data inputs, renderer, style, light -- and the verdicts on them.

Stale means the DATA under a picture is outdated; a newer renderer or palette is only an
offer. Every verdict is computed on read from the sidecar and the inputs on disk, never
stored. docs/maps_contract.md §3 is the specification.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.gameassets.provenance import changelist, read_path
from ...core.gameassets.versions import (
    ARTWORK_RECIPES,
    PROVENANCE_SCHEMA,
    READER_VERSIONS,
    RENDER_RECIPE_CURRENT,
    RENDER_RECIPES,
    STYLES,
)

__all__ = [
    "INPUT_DIRS",
    "axes_from_sidecar",
    "current_state",
    "derive_id",
    "display_name",
    "sort_key",
    "style_tone",
    "verdict",
]

RENDERS_GENERATOR = "tools/gen_map_renders.py"
ARTWORK_GENERATOR = "tools/gen_map_image.py"

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


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _legacy_renders(block: dict) -> dict:
    sources = block.get("sources") if isinstance(block.get("sources"), dict) else {}
    field = sources.get("heightfield") if isinstance(sources.get("heightfield"), dict) else {}
    cl = changelist(field.get("game_version_pinned"))
    recipe = _int(block.get("recipe")) or 0
    known = RENDER_RECIPES.get(recipe, {})
    render = block.get("render") if isinstance(block.get("render"), dict) else {}
    regime = render.get("two_regime") if isinstance(render.get("two_regime"), dict) else {}
    layer = str(block.get("layer") or "")
    inputs: dict = {
        "heightfield": {
            "cl": cl,
            "generator_version": _int(field.get("generator_version")),
            "planes": LEGACY_PLANES.get(recipe, ["height"]),
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
            "label": known.get("label", f"recipe {recipe}"),
            "sampler": known.get("sampler"),
            "two_regime": bool(regime.get("enabled", known.get("two_regime", False))),
            "size_px": _int(render.get("width_px")),
            "subsamples": _int(regime.get("subsamples_per_axis")),
        },
        "style": {"id": style, "version": 1, "label": STYLES.get(style, {}).get("label", style),
                  "digest": None, "inferred": True},
        "inferred": True,
    }  # fmt: skip


def _legacy_artwork(block: dict) -> dict:
    slices = read_path(block, ("sources", "map_slices"))
    slices = slices if isinstance(slices, dict) else {}
    cl = changelist(slices.get("game_version_raw")) or changelist(slices.get("game_version_pinned"))
    tiles = block.get("tiles") if isinstance(block.get("tiles"), dict) else {}
    recipe = _int(read_path(tiles, ("enhancement", "recipe")))
    if recipe is None:
        recipe = 1 if tiles.get("enhanced") else 0
    image = block.get("image") if isinstance(block.get("image"), dict) else {}
    return {
        "schema": PROVENANCE_SCHEMA,
        "game": {"cl": cl},
        "inputs": {"artwork_sheet": {"cl": cl, "reader_version": None, "digest": None,
                                     "inferred": True}},
        "renderer": {"family": "artwork", "recipe": recipe, "version": 1,
                     "label": ARTWORK_RECIPES.get(recipe, {}).get("label", f"recipe {recipe}"),
                     "size_px": _int(image.get("width_px"))},
        "style": {"id": "artwork", "version": 1, "label": "artwork", "digest": None},
        "inferred": True,
    }  # fmt: skip


def axes_from_sidecar(sidecar: object, kind: str = "render") -> dict:
    """The axes a sidecar states, derived from its legacy fields when it has no ``provenance``.

    A sidecar nothing can be read from still answers: its axes are empty and ``inferred``.
    """
    block = sidecar.get("_meta") if isinstance(sidecar, dict) else None
    block = block if isinstance(block, dict) else {}
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


def read_json(path: Path) -> dict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _input_now(local: Path, name: str) -> dict | None:
    folder, sidecar, version_key = INPUT_DIRS[name]
    meta = read_json(local / folder / sidecar)
    if meta is None:
        return None
    pin = read_path(meta, ("sources", "game", "game_version_raw")) or read_path(
        meta, ("sources", "game", "build_raw")
    )
    cl = changelist(pin) or changelist(read_path(meta, ("sources", "game", "game_version_pinned")))
    files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
    return {
        "version": _int(meta.get(version_key)),
        "cl": cl if cl is not None else _int(meta.get("cl")),
        "digest": meta.get("digest") if isinstance(meta.get("digest"), str) else None,
        "planes": sorted(files),
        "transcribed": meta.get("transcribed"),
    }


def current_state(local: Path, game_cl: int | None) -> dict:
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


def _needs(recipe: int, current: dict) -> list[str]:
    """Which inputs have to be rebuilt before ``recipe`` can draw from what is on disk."""
    wanted = RENDER_RECIPES[recipe]["requires"].get("heightfield", {})
    field = current["inputs"].get("heightfield")
    if field is None:
        return ["heightfield"]
    have = set(field["planes"])
    planes = all(PLANE_FILES.get(p, p) in have for p in wanted.get("planes", []))
    if (field["version"] or 0) < wanted.get("min_version", 0) or not planes:
        return ["heightfield"]
    return []


def verdict(axes: dict, current: dict) -> dict:
    """``{stale: [{axis, text}], rerender, restyle, incomplete}`` for one map's axes."""
    stale: list[dict] = []
    inputs = axes.get("inputs") if isinstance(axes.get("inputs"), dict) else {}
    recorded_cls = [_int(v.get("cl")) for v in inputs.values() if isinstance(v, dict)]
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
            had = _int(entry.get("generator_version", entry.get("version")))
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
            had = _int(entry.get("reader_version"))
            now_reader = current["readers"].get(name)
            if had is None:
                incomplete = True
            elif now_reader is not None and now_reader > had:
                stale.append({"axis": name, "text": f"newer {words} (v{had} → v{now_reader})"})
    renderer = axes.get("renderer") if isinstance(axes.get("renderer"), dict) else {}
    rerender = None
    recipe, version = _int(renderer.get("recipe")), _int(renderer.get("version")) or 0
    if renderer.get("family") == "render" and recipe is not None:
        best = RENDER_RECIPES[RENDER_RECIPE_CURRENT]
        if (RENDER_RECIPE_CURRENT, best["version"]) > (recipe, version):
            needs = _needs(RENDER_RECIPE_CURRENT, current)
            label = f"{best['label']} r{RENDER_RECIPE_CURRENT}"
            rerender = {
                "recipe": RENDER_RECIPE_CURRENT,
                "label": label,
                "needs": needs,
                "text": f"newer renderer: {label}"
                + (" after rebuilding the heightfield" if needs else ""),
            }
    style = axes.get("style") if isinstance(axes.get("style"), dict) else {}
    known_style = STYLES.get(str(style.get("id")))
    restyle = bool(
        known_style
        and _int(style.get("version")) is not None
        and known_style["version"] > style["version"]
    )
    return {"stale": stale, "rerender": rerender, "restyle": restyle, "incomplete": incomplete}


def data_cl(axes: dict) -> int | None:
    """The build the picture's data came from: the heightfield's, else the oldest input's."""
    inputs = axes.get("inputs") if isinstance(axes.get("inputs"), dict) else {}
    field = inputs.get("heightfield")
    if isinstance(field, dict) and _int(field.get("cl")) is not None:
        return field["cl"]
    known = [_int(v.get("cl")) for v in inputs.values() if isinstance(v, dict)]
    known = [cl for cl in known if cl is not None]
    return min(known) if known else _int(read_path(axes, ("game", "cl")))


def _hf_version(axes: dict) -> int | None:
    return _int(read_path(axes, ("inputs", "heightfield", "generator_version")))


def renderer_label(axes: dict) -> str:
    renderer = axes.get("renderer") if isinstance(axes.get("renderer"), dict) else {}
    recipe = renderer.get("recipe")
    label = str(renderer.get("label") or "unknown")
    if renderer.get("family") == "artwork" and recipe == 0:
        return "plain"
    return f"{label} r{recipe}" if recipe is not None else label


def style_label(axes: dict) -> str:
    style = axes.get("style") if isinstance(axes.get("style"), dict) else {}
    return str(style.get("label") or style.get("id") or "unknown")


def style_tone(axes: dict) -> str:
    """``light`` or ``dark``: the sidecar's word, else the style table's, else light."""
    style = axes.get("style") if isinstance(axes.get("style"), dict) else {}
    tone = style.get("tone") or STYLES.get(str(style.get("id")), {}).get("tone")
    return tone if tone in ("light", "dark") else "light"


def data_label(axes: dict) -> str:
    cl = data_cl(axes)
    hf = _hf_version(axes)
    text = f"data {cl if cl is not None else '?'}"
    return text + (f"/hf v{hf}" if hf is not None else "")


def light_label(axes: dict) -> str | None:
    """The light model a layer drawn unlit is relit with; ``None`` for one drawn lit."""
    light = axes.get("light") if isinstance(axes.get("light"), dict) else None
    return str(light.get("label") or light.get("id") or "lit") if light else None


def display_name(axes: dict, with_size: bool = False) -> str:
    """``{style} · {renderer} · data {cl}/hf v{n}``, then the light and the size when present."""
    parts = [style_label(axes), renderer_label(axes), data_label(axes)]
    if light_label(axes):
        parts.append(str(light_label(axes)))
    size = _int(read_path(axes, ("renderer", "size_px")))
    if with_size and size:
        parts.append(f"{size} px")
    return " · ".join(parts)


def sort_key(axes: dict, ident: str) -> tuple:
    """Style, then data (newest build, then highest input versions), renderer, style version."""
    renderer = axes.get("renderer") if isinstance(axes.get("renderer"), dict) else {}
    style = axes.get("style") if isinstance(axes.get("style"), dict) else {}
    return (
        style_label(axes),
        -(data_cl(axes) or 0),
        -(_hf_version(axes) or 0),
        -(_int(renderer.get("recipe")) or 0),
        -(_int(renderer.get("version")) or 0),
        -(_int(style.get("version")) or 0),
        -(_int(renderer.get("size_px")) or 0),
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
