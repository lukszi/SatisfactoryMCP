"""The game's biome raster as the biome layers draw it: read, checked and coloured.

Read once a run, and only when a layer coloured from it is drawn. The pin is scored against
the artwork every run, and the region table checked against the raster
(docs/spatial-and-map.md section 17, "The game ships biome geometry after all").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from mapgen.gamedata.ground.biome import (
    BiomeRaster,
    calibrate_biome,
    read_biome,
    region_table_is_current,
)
from mapgen.palette.styles import (
    BIOME_BLEND_TEXELS,
    BIOME_COLOURS,
    NO_MANS_LAND_RGB,
    UNKNOWN_BIOME_RGB,
    biome_colour_field,
    biome_lookup,
)
from mapgen.tiles.imaging import TileImaging
from satisfactory_mcp.core.arrays import U8Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.maparea import MAP_AREA_CLASS, MAP_AREA_PATH, NO_MANS_LAND
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = ["BiomeInputs", "read_biome_inputs"]


@dataclass(frozen=True)
class BiomeInputs:
    """The biome raster, the areas it draws, their colours, and what the sidecars record.

    Empty when no biome layer is drawn: ``raster`` None, and a one-texel ``width``.
    """

    raster: BiomeRaster | None = None
    drawn: list[str] = field(default_factory=list[str])
    rgb: U8Grid | None = None
    source: JsonObject = field(default_factory=dict[str, JsonValue])
    provenance: JsonObject | None = None

    @property
    def width(self) -> int:
        """Texels across the raster, which the band loop indexes its columns by."""
        return 1 if self.raster is None else self.raster["width"]


def read_biome_inputs(
    store: IoStore,
    scripts: ScriptObjects,
    artwork: Image,
    image_mod: TileImaging,
    build_cl: int | None,
    pyooz_version: str,
) -> BiomeInputs:
    """The biome raster read, its pin scored against the artwork, and its colours."""
    biome = read_biome(store, scripts)
    print(
        f"  {biome['width']}x{biome['width']} palette indices, "
        f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
    )
    calibration = calibrate_biome(biome, artwork, image_mod)
    _print_calibration(calibration)
    agreement = region_table_is_current(biome)
    _print_agreement(agreement)
    table, drawn = biome_lookup(biome)
    provenance: JsonObject = {
        "cl": build_cl,
        "reader_version": READER_VERSIONS["biome_raster"],
        "digest": sha256_hex(
            np.ascontiguousarray(biome["area"]).data,
            json.dumps(biome["assets_by_index"]).encode("utf-8"),
        ),
    }
    source = _biome_source(biome, drawn, calibration, agreement, pyooz_version)
    return BiomeInputs(biome, drawn, biome_colour_field(biome, table), source, provenance)


def _print_calibration(calibration: JsonObject) -> None:
    print(
        f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin "
        f"against {calibration['edge_ratio_at_the_best_rival_shift']} for the best "
        f"shift and {calibration['edge_ratio_at_other_scales']} at other scales -- "
        f"margin {calibration['margin_over_the_best_rival']}x over "
        f"{calibration['sweep']}"
    )
    if not calibration["pin_holds"]:
        print(
            "  WARNING: the pin no longer beats its rivals by the required margin. "
            "The biome texture moved, or the artwork sheet did. The layer is still "
            "drawn -- it is the corners that are in question -- and _meta says so."
        )


def _print_agreement(agreement: JsonObject) -> None:
    if "skipped" in agreement:
        print(f"  region table: {agreement['skipped']}")
        return
    print(
        f"  region table: {agreement['cells_agreeing']} of "
        f"{agreement['cells_compared']} committed cells match this raster "
        f"({agreement['agreement_pct']}%)"
    )
    if not agreement["table_is_current"]:
        print(
            "  WARNING: data/region_names.json is no longer this asset's own "
            "downsample, so it was cut from a different build. Re-run:\n"
            "      uv run --extra gen python tools/gen_region_names.py"
        )


def _biome_source(
    biome: BiomeRaster,
    drawn: list[str],
    calibration: JsonObject,
    agreement: JsonObject,
    pyooz_version: str,
) -> JsonObject:
    """``sources.biome_raster``: the asset, how it was read and checked, and this palette."""
    asset = MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0]
    return {
        "biome_raster": {
            "name": "/Game/" + asset,
            "class": MAP_AREA_CLASS,
            "licence": (
                "Coffee Stain Studios' own asset, read out of the reader's installed "
                "copy of the game. Not committed, not redistributed, and served to "
                "localhost only."
            ),
            "derivation": (
                f"mAreaData, {biome['width']}x{biome['width']} palette indices; "
                "mColorToArea resolves each index to a UFGMapArea object"
            ),
            "areas": [area for area in biome["distinct_areas"]],
            "shipped_palette_rgba": [[c for c in entry] for entry in biome["palette"]],
            "shipped_palette_role": (
                "the game's own UI legend -- flat primaries, cyan, magenta, white. "
                "Decoded for the record and NOT drawn: see palette below, which is this "
                "file's own and was written to look like imagery."
            ),
            "palette": {name: [c for c in BIOME_COLOURS[name]] for name in sorted(BIOME_COLOURS)},
            "palette_blend_texels": BIOME_BLEND_TEXELS,
            "palette_fallback": {
                NO_MANS_LAND: [c for c in NO_MANS_LAND_RGB],
                "an area this file has no colour for": [c for c in UNKNOWN_BIOME_RGB],
            },
            "index_to_area": {str(i): name for i, name in enumerate(drawn)},
            "index_to_asset": {str(i): name for i, name in enumerate(biome["assets_by_index"])},
            "calibration": calibration,
            "region_table_check": agreement,
            "pyooz_version": pyooz_version,
        }
    }
