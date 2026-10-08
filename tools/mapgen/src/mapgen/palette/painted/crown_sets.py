"""The trees a painted ground draws over the crown sprites: the paint store's crowns and the
Titan canopy, its sprites moved onto the style's leaf colour, and what the sidecar says of them.

docs/map/light-and-crowns.md section 36 and docs/map/painted.md section 30.
"""

from __future__ import annotations

from pathlib import Path

from mapgen.palette.painted.shapes import PaintedPalette, PaintMeta, TitanTreesStyle
from mapgen.palette.painted.trees import titan_canopy_levels, titan_colours
from mapgen.sprites.store import SpriteAtlas
from mapgen.terrain.crown_atlas import crown_atlas
from mapgen.terrain.crown_stamp import CrownSet, load_crowns
from mapgen.terrain.render_meshes import TITAN_LEAVES
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = ["crown_sets", "crowns_provenance"]


def crown_sets(
    paint_dir: Path, meta: PaintMeta, palette: PaintedPalette, sprites: SpriteAtlas | None
) -> tuple[CrownSet | None, CrownSet | None]:
    """The crowns when the palette draws them and the Titan canopy when it draws the Titan
    trees, at their opacity, over one atlas of ``sprites``; neither without them."""
    if sprites is None:
        return None, None
    atlas = crown_atlas(sprites)
    crowns = None
    if palette.get("crowns", {}).get("draw", False):
        crowns = load_crowns(paint_dir, meta.get("crowns"), meta["files"], atlas)
    style: TitanTreesStyle = palette.get("titan_trees") or {}
    opacity = float(style.get("opacity", 0.0))
    if not opacity or not len(sprites.titan):
        return crowns, None
    titan = CrownSet(sprites.titan, atlas, opacity)
    species = sorted({int(k) for k in titan.records["species"]})
    target = titan_colours(palette)[TITAN_LEAVES]
    titan.levels = titan_canopy_levels(titan.levels, species, target, palette["crowns"])
    return crowns, titan


def crowns_provenance(
    palette: PaintedPalette,
    meta: PaintMeta,
    sets: tuple[CrownSet | None, CrownSet | None],
    measured: JsonObject,
) -> JsonValue:
    """The sidecar's ``crowns``: what was drawn, by which rule, and the calibration."""
    if not palette.get("crowns", {}).get("draw"):
        return "not drawn by this palette"
    crowns, titan = sets
    block = meta.get("crowns")
    if crowns is None or block is None:
        return "not in this paint store, or no crown sprites"
    return {
        "species": len(block["species"]),
        "trees": len(crowns.records),
        "titan_canopy": 0 if titan is None else len(titan.records),
        "rule": "each species' crown sprite, per-tree yaw, scale and lean, lit by its "
        "normals; tallest over lowest; hidden under a higher surface",
        "calibration": measured,
    }
