"""What each plane of the terrain field is called on disk, and what its values mean."""

from __future__ import annotations

__all__ = [
    "CACHE_DIR_NAME",
    "DENSITY_NAME",
    "DIR_NAME",
    "DM_PER_M",
    "HEIGHT_NAME",
    "META_NAME",
    "NODATA",
    "PROV_CLIFF",
    "PROV_CLIFF_DIRECT",
    "PROV_CLIFF_VALUES",
    "PROV_FILL",
    "PROV_LANDSCAPE",
    "PROV_NAME",
    "PROV_NAMES",
    "PROV_NODATA",
    "PROV_WATER_NAME",
    "TERRAIN_NAME",
    "TOP_NAME",
    "WATER_DRY",
    "WATER_LEVEL_ONLY",
    "WATER_MEASURED",
    "WATER_NAME",
    "WATER_QUALITY_NAME",
    "WATER_QUALITY_NAMES",
]

#: The directory the generator writes and this module reads, under ``data/local/``.
DIR_NAME = "heightmap"

#: The planes over one grid, plus the sidecar carrying the georeference. Height and water
#: are int16 DECIMETRES of world Z; provenance, water quality and density are uint8 codes.
HEIGHT_NAME = "height.i16.z"
PROV_NAME = "prov.u8.z"
WATER_NAME = "water.i16.z"
WATER_QUALITY_NAME = "waterq.u8.z"
DENSITY_NAME = "density.u8.z"
META_NAME = "meta.json"

#: The bare sculpted landscape, raw uint16 on its own grid (``meta.json`` ``terrain_grid``);
#: 0 is a hole. Rocks and cliffs are meshes and are not in it.
TERRAIN_NAME = "terrain.u16.z"
#: ``height.i16.z`` max-folded with arches and foliage boulders: the highest thing standing.
TOP_NAME = "top.i16.z"

#: Decoded planes as ``.npy`` beside the ``.z`` files, memory-mapped on later loads.
CACHE_DIR_NAME = "cache"

#: The int16 value that means "nothing is known here". Not zero: zero is sea level and a
#: real answer, so a no-data texel read as zero is a flat sea at the map's edge.
NODATA = -32768

#: Which layer answered a texel; the file format, so never renumber (2 is unused). 5 is a
#: cliff texel a source vertex landed in, 4 one the rasteriser interpolated. Only the
#: presence of ``density.u8.z`` announces that split, never a texel being 4.
PROV_NODATA = 0
PROV_LANDSCAPE = 1
PROV_FILL = 3
PROV_CLIFF = 4
PROV_CLIFF_DIRECT = 5

#: Both values that are the cliff layer. Anything asking "is this texel cliff" means this;
#: testing ``== PROV_CLIFF`` alone misses 5.
PROV_CLIFF_VALUES = (PROV_CLIFF, PROV_CLIFF_DIRECT)

PROV_NAMES: dict[int, str] = {
    PROV_NODATA: "no data",
    PROV_LANDSCAPE: "landscape",
    PROV_FILL: "fill",
    PROV_CLIFF: "cliff",
    PROV_CLIFF_DIRECT: "cliff, direct",
}

#: What the water channel is called when a reading has one. Not a provenance value: water
#: is a second surface over the same texel, not a different source for the ground.
PROV_WATER_NAME = "water"

#: ``waterq.u8.z``'s values; the file format, so never renumber. A depth exists only where
#: the ground under the level was measured at 1 m (docs/map/heightfield.md section 19).
WATER_DRY = 0
WATER_MEASURED = 1
WATER_LEVEL_ONLY = 2

WATER_QUALITY_NAMES: dict[int, str] = {
    WATER_DRY: "dry",
    WATER_MEASURED: "water, depth measured",
    WATER_LEVEL_ONLY: "water, depth unknown",
}

DM_PER_M = 10.0
