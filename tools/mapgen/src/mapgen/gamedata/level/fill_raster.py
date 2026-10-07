"""``HeightData_Test``: the 2048 px interface raster that fills outside the landscape."""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.arrays import BoolMask, F16Grid, F32Grid
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.textures import raw_mip_sizes

__all__ = [
    "FILL_FLOOR_CM",
    "FILL_RASTER_BYTES",
    "FILL_RASTER_MIPS",
    "FILL_RASTER_OFFSET_CM",
    "FILL_RASTER_PATH",
    "FILL_RASTER_PX",
    "FILL_RASTER_SCALE_CM_PER_RAW",
    "decode_fill_raster",
    "read_fill_raster",
]


#: The interface raster the fill layer is cut from.
FILL_RASTER_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test.ubulk"
)
FILL_RASTER_PX = 2048

#: The mip chain of that raster, largest-first, at two bytes per texel: 2048 down to 128. A
#: file of another length means the raster was re-cooked, i.e. the game changed.
FILL_RASTER_MIPS = raw_mip_sizes(FILL_RASTER_PX, 5, 2)
FILL_RASTER_BYTES = sum(size for _px, size in FILL_RASTER_MIPS)


#: ``z_cm = scale*raw + offset``, from a robust three-pass fit of the float16 values against
#: the 626 static nodes: 569 inliers, 1.07 m RMS, 3.897 m per quantisation step. Recorded
#: rather than re-fitted per run, because a calibration that moves silently is not one.
FILL_RASTER_SCALE_CM_PER_RAW = 99364.40751843198
FILL_RASTER_OFFSET_CM = -52282.12831764497

#: The fill's no-data test: a DECODED height at or under this is the raster's blank tail,
#: below the world's -255 m floor. Never ``raw > 0``, which lets the blank value through;
#: ``sidecar_blocks.fill_source`` records what that leaks.
FILL_FLOOR_CM = -26000.0


def decode_fill_raster(values: F16Grid) -> tuple[F32Grid, BoolMask]:
    """The interface raster's float16 texels to world centimetres, and where it says anything.

    Split out from the read so the rule, ``FILL_FLOOR_CM``, is a pure function a test can hold.
    """
    z_cm = values.astype(np.float32) * FILL_RASTER_SCALE_CM_PER_RAW + FILL_RASTER_OFFSET_CM
    return z_cm, z_cm > FILL_FLOOR_CM


def read_fill_raster(store: IoStore) -> tuple[F32Grid, BoolMask]:
    """``HeightData_Test`` as world centimetres, with the mask of where it says anything.

    The length check is the integrity check: 2048 down to 128 at two bytes a texel is one
    number, and a file that is not that long was re-cooked at another size or mip count.
    """
    if FILL_RASTER_PATH not in store.by_path:
        raise SystemExit(
            "HeightData_Test is not in the container. The interface raster moved or was "
            "renamed, which means the game changed; the fill layer has no source."
        )
    raw = store.read_path(FILL_RASTER_PATH)
    if len(raw) != FILL_RASTER_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in FILL_RASTER_MIPS)
        raise SystemExit(
            f"HeightData_Test.ubulk is {len(raw)} bytes, expected exactly {FILL_RASTER_BYTES} "
            f"-- the mip chain {chain} at two bytes per float16 texel. A different length "
            "means the raster was re-cooked, so refusing to decode mip 0 out of a file "
            "whose layout is no longer known."
        )
    values = np.frombuffer(raw[: FILL_RASTER_PX * FILL_RASTER_PX * 2], dtype="<f2").reshape(
        FILL_RASTER_PX, FILL_RASTER_PX
    )
    return decode_fill_raster(values)
