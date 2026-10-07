"""The 1 m terrain field: its on-disk format, and a loader that is only a loader.

``tools/gen_world_heightmap.py`` cuts the field from the reader's own installed game into the
gitignored ``data/local/heightmap/``. **Absent is the normal case**: with no field
``load_field()`` returns ``None`` and every caller carries on without terrain. What each plane
holds is stated at its constant in ``planes``; how the field is read is in
docs/map/heightfield.md sections 22 to 24.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

from .... import config
from ....core.jsontypes import JsonValue
from . import cave_masks
from .codec import (
    ZLIB_LEVEL,
    decode_i16,
    decode_u8,
    decode_u16,
    encode_i16,
    encode_u8,
    encode_u16,
)
from .field import Field
from .meta import TerrainGrid
from .planes import (
    CACHE_DIR_NAME,
    DENSITY_NAME,
    DIR_NAME,
    DM_PER_M,
    HEIGHT_NAME,
    META_NAME,
    NODATA,
    PROV_CLIFF,
    PROV_CLIFF_DIRECT,
    PROV_CLIFF_VALUES,
    PROV_FILL,
    PROV_LANDSCAPE,
    PROV_NAME,
    PROV_NAMES,
    PROV_NODATA,
    PROV_WATER_NAME,
    TERRAIN_NAME,
    TOP_NAME,
    WATER_DRY,
    WATER_LEVEL_ONLY,
    WATER_MEASURED,
    WATER_NAME,
    WATER_QUALITY_NAME,
    WATER_QUALITY_NAMES,
)
from .readings import (
    AMBIGUOUS_M,
    SURFACES,
    Area,
    NearWater,
    Reading,
    Surface,
    SurfaceOrFloor,
    Surfaces,
)

__all__ = [
    "AMBIGUOUS_M",
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
    "SURFACES",
    "TERRAIN_NAME",
    "TOP_NAME",
    "WATER_DRY",
    "WATER_LEVEL_ONLY",
    "WATER_MEASURED",
    "WATER_NAME",
    "WATER_QUALITY_NAME",
    "WATER_QUALITY_NAMES",
    "ZLIB_LEVEL",
    "Area",
    "Field",
    "NearWater",
    "Reading",
    "Surface",
    "SurfaceOrFloor",
    "Surfaces",
    "TerrainGrid",
    "decode_i16",
    "decode_u8",
    "decode_u16",
    "encode_i16",
    "encode_u8",
    "encode_u16",
    "field_dir",
    "load_field",
]


#: The loaded field, keyed by its directory and its sidecar's mtime, so a regenerated field is
#: picked up without a restart while a repeated question costs a dictionary lookup.
_CACHE: dict[tuple[str, int, bool], Field] = {}


def field_dir(local_dir: Path | None = None) -> Path:
    """Where the field lives. Resolved at call time so a test can point it elsewhere."""
    if local_dir is not None:
        return Path(local_dir)
    return config.data_dir() / "local" / DIR_NAME


def load_field(local_dir: Path | None = None, *, cache: bool = True) -> Field | None:
    """The terrain field, or ``None`` if this machine has none.

    Every failure (no directory, a sidecar that will not parse, a raster whose length
    disagrees with it) is ``None`` rather than an exception: "no terrain" is a complete
    answer, and the generator is what diagnoses a broken field.
    """
    directory = field_dir(local_dir)
    meta_path = directory / META_NAME
    try:
        stamp = meta_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(directory), stamp, cache)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        meta: JsonValue = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(meta, dict):
            return None
        field = Field(
            meta, directory, cache=cache, caves_dir=directory.parent / cave_masks.DIR_NAME
        )
    except (OSError, ValueError, TypeError, KeyError, zlib.error):
        return None
    _CACHE.clear()
    _CACHE[key] = field
    return field
