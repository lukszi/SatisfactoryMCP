"""The painted palette a render draws with: its ``derived_keys`` wearing the derived colours.

The colours come from ``targets.derived.json`` when its stamp matches the store, the area map
and the calibration block, and are derived in the run otherwise; a key the derive gate declines
keeps its screenshot. A store without daylight keeps every screenshot target.
docs/map/calibration.md section 31.
"""

from __future__ import annotations

from pathlib import Path

from mapgen.palette.painted.albedo import load_paint_meta
from mapgen.palette.painted.derive.scene import area_grid, scene_from_store
from mapgen.palette.painted.derive.targets import (
    TARGETS_NAME,
    derive,
    read_targets,
    stamp_of,
    with_targets,
)
from mapgen.palette.painted.shapes import BiomeGrid, FieldPlanes, PaintedPalette
from mapgen.palette.styles import palette_digest
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = ["calibrated_palette"]


def calibrated_palette(
    palette: PaintedPalette,
    digest: str,
    paint_dir: Path,
    biome: tuple[BiomeGrid, list[str]],
    field: FieldPlanes | None,
) -> tuple[PaintedPalette, str, JsonObject]:
    """The palette with its derived keys applied, its digest, and what the sidecar records.

    ``biome`` is the area raster and the names it is drawn as.
    """
    keys = palette["calibration"].get("derived_keys", [])
    meta = load_paint_meta(paint_dir)
    if not keys or meta is None:
        return palette, digest, {"source": "screenshot", "derived_keys": len(keys)}
    if not meta.get("lighting"):
        version = meta.get("generator_version")
        why = f"paint store generator {version} keeps no daylight; re-run mapgen paint"
        print(f"  derived targets not applied: {why}")
        return palette, digest, {"source": "screenshot", "why": why}
    grid = meta["grid"]
    areas = area_grid(biome[0], biome[1], field, (grid["height"], grid["width"]))
    stamp = stamp_of(meta.get("digest"), areas.digest(), palette["calibration"])
    hexes = read_targets(paint_dir, stamp)
    source = TARGETS_NAME
    if hexes is None:
        hexes = derive(scene_from_store(paint_dir, meta, areas), palette["calibration"]).hexes()
        source = "derived in this run"
    merged, applied, declined = with_targets(palette, hexes)
    kept: list[JsonValue] = [key for key in keys if key not in applied]
    print(
        f"  {len(applied)} derived targets from {source}; {len(kept)} kept their screenshot, "
        f"{len(declined)} of them by the derive gate"
    )
    block: JsonObject = {
        "source": source,
        "applied": {k: hexes[k] for k in applied},
        "kept": kept,
        "declined": {k: {"derived": hexes[k], "why": why} for k, why in declined.items()},
        "stamp": stamp,
    }
    return merged, palette_digest(merged), block
