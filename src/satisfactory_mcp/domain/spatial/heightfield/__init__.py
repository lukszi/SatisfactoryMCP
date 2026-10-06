"""The 1 m terrain field: the on-disk format, and the loader that is only a loader.

``tools/gen_world_heightmap.py`` cuts the field out of the reader's own installed game and
writes it to the gitignored ``data/local/heightmap/``; this package is the byte format both
sides agree on and a reader over it. **Absent is the normal case** -- the repository ships
no terrain, so ``load_field()`` returns ``None`` on any machine where nobody ran the
generator, and every caller carries on without one. A raster is ``zlib`` over raw bytes,
the int16 ones row-delta first; each plane is decoded once into a memory-mapped ``.npy``
under ``cache/``. What each plane means is stated at the constant that names it, in
``planes``; the georeference is in ``meta.json``. Surfaces and hints:
docs/spatial-and-map.md section 22; rock heights from the collision pack beside the planes:
section 24.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

from .... import config
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
from .readings import AMBIGUOUS_M, SURFACES, Area, NearWater, Reading, Surface, Surfaces

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
    "Surfaces",
    "decode_i16",
    "decode_u8",
    "decode_u16",
    "encode_i16",
    "encode_u8",
    "encode_u16",
    "field_dir",
    "load_field",
]


#: Loaded fields, keyed by the directory and its sidecar's mtime, so a regenerated field is
#: picked up without a restart while a repeated question costs a dictionary lookup.
_CACHE: dict[tuple[str, int, bool], Field] = {}


def field_dir(local_dir: Path | None = None) -> Path:
    """Where the field lives. Resolved at call time so a test can point it elsewhere."""
    if local_dir is not None:
        return Path(local_dir)
    return config.data_dir() / "local" / DIR_NAME


def load_field(local_dir: Path | None = None, *, cache: bool = True) -> Field | None:
    """The terrain field, or ``None`` if this machine has none.

    Every failure mode -- no directory, no sidecar, a sidecar that will not parse, a raster
    whose length disagrees with it -- returns ``None`` rather than raising: the caller asked
    "is there terrain here", and "no" is a complete answer. A broken field is diagnosed by
    the generator, not here.
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
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
