"""The game's biome raster as the painted layer reads it: read, checked and named.

Read once a run, and only when the painted layer is drawn. The pin is scored against
the artwork every run, and the region table checked against the raster
(docs/map/renders.md section 17, "The game ships biome geometry after all").
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from mapgen.gamedata.ground.biome import (
    BiomeRaster,
    area_names,
    calibrate_biome,
    read_biome,
    region_table_is_current,
)
from mapgen.tiles.imaging import TileImaging
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.maparea import MAP_AREA_CLASS, MAP_AREA_PATH
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import sha256_hex
from satisfactory_mcp.core.gameassets.versions import READER_VERSIONS
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

if TYPE_CHECKING:
    from PIL.Image import Image

__all__ = ["BiomeInputs", "read_biome_inputs"]


@dataclass(frozen=True)
class BiomeInputs:
    """The biome raster, each index's area name, and what the sidecars record.

    Empty without the painted layer: ``raster`` None, and a one-texel ``width``.
    """

    raster: BiomeRaster | None = None
    drawn: list[str] = field(default_factory=list[str])
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
    """The biome raster read, its pin scored against the artwork, and its areas named."""
    biome = read_biome(store, scripts)
    print(
        f"  {biome['width']}x{biome['width']} palette indices, "
        f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
    )
    calibration = calibrate_biome(biome, artwork, image_mod)
    _print_calibration(calibration)
    agreement = region_table_is_current(biome)
    _print_agreement(agreement)
    drawn = area_names(biome)
    provenance: JsonObject = {
        "cl": build_cl,
        "reader_version": READER_VERSIONS["biome_raster"],
        "digest": sha256_hex(
            np.ascontiguousarray(biome["area"]).data,
            json.dumps(biome["assets_by_index"]).encode("utf-8"),
        ),
    }
    source = _biome_source(biome, drawn, calibration, agreement, pyooz_version)
    return BiomeInputs(biome, drawn, source, provenance)


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
    """``sources.biome_raster``: the asset, how it was read and checked, and its areas."""
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
                "Decoded for the record and never drawn."
            ),
            "index_to_area": {str(i): name for i, name in enumerate(drawn)},
            "index_to_asset": {str(i): name for i, name in enumerate(biome["assets_by_index"])},
            "calibration": calibration,
            "region_table_check": agreement,
            "pyooz_version": pyooz_version,
        }
    }
