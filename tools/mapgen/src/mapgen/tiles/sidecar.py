"""The render layer's ``meta.json``: what the web API reads, and the build it pins."""

from __future__ import annotations

from datetime import UTC, datetime

from mapgen.gamedata.frame import BOUNDS_M
from mapgen.tiles.recipes import RECIPE, RECIPES
from satisfactory_mcp.core.gameassets.provenance import read_str_path
from satisfactory_mcp.domain.spatial import heightfield as hf

__all__ = [
    "FIELD_PIN_PATH",
    "RENDER_SIDECAR_NAME",
    "build_sidecar",
    "pinned_field_build",
]

RENDER_SIDECAR_NAME = "meta.json"

#: Where a layer sidecar records the heightfield build it was drawn from.
FIELD_PIN_PATH = ("sources", "heightfield", "game_version_pinned")


def pinned_field_build(sidecar: dict) -> str | None:
    """The heightfield build an existing layer sidecar names, or None if it names none."""
    return read_str_path(sidecar.get("_meta"), FIELD_PIN_PATH)


def build_sidecar(
    *,
    layer: str,
    field_meta: dict,
    tiles: dict,
    render: dict,
    extra: dict,
    recipe: int = RECIPE,
    tiles_2x: dict | None = None,
    provenance: dict | None = None,
) -> dict:
    """The file the web API reads for this layer, shaped like ``map.json``, plus provenance."""
    build = ((field_meta.get("sources") or {}).get("game") or {}).get("game_version_pinned")
    return {
        **BOUNDS_M,
        "_meta": {
            "description": (
                f"The {layer} base layer: a render of this world drawn from the 1 m "
                "heightfield, and the pyramid cut from it. All of it is local: data/local/ "
                "is gitignored and no map imagery is ever committed to this repository."
            ),
            "bounds": (
                "metres, game axes -- +X east, +Y south. The corners of the in-game map "
                "square, the same frame data/local/map.json pins, so a page can swap base "
                "layers without touching its tile grid."
            ),
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": recipe,
            "recipe_description": RECIPES[recipe],
            "transcribed": datetime.now(UTC).date().isoformat(),
            "sources": {
                "heightfield": {
                    "name": f"data/local/{hf.DIR_NAME}/",
                    "generator": field_meta.get("generator"),
                    "generator_version": field_meta.get("generator_version"),
                    "grid": field_meta.get("grid"),
                    "game_version_pinned": build,
                    "role": "every pixel's height, and the relief and water on it",
                },
                **extra,
            },
            "render": render,
            "tiles": tiles,
            **({"tiles_2x": tiles_2x} if tiles_2x else {}),
            **({"provenance": provenance} if provenance else {}),
            "staleness": (
                "sources.heightfield.game_version_pinned is the build the field under these "
                "pixels was cut from. tools/gen_map_renders.py refuses to replace this layer "
                "unless the field now on disk names the same build; --force says it anyway. "
                "Terrain moves every patch, and a render that quietly disagrees with the "
                "node tables beside it is exactly the drift this project announces."
            ),
        },
    }
