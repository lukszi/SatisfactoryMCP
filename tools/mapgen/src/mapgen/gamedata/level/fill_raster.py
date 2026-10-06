"""``HeightData_Test``: the 2048 px interface raster that fills outside the landscape."""

from __future__ import annotations

import numpy as np

from satisfactory_mcp.core.gameassets.textures import raw_mip_sizes

__all__ = [
    "BASELINE_BYTES",
    "BASELINE_MIPS",
    "BASELINE_OFFSET_CM",
    "BASELINE_PATH",
    "BASELINE_PX",
    "BASELINE_SCALE_CM_PER_RAW",
    "FILL_FLOOR_CM",
    "decode_baseline",
    "read_baseline",
]


#: The interface raster the fill layer is cut from.
BASELINE_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test.ubulk"
)
BASELINE_PX = 2048

#: The mip chain of that raster, largest-first, at two bytes per texel: 2048 down to 128. A
#: file of another length means the raster was re-cooked, i.e. the game changed.
BASELINE_MIPS = raw_mip_sizes(BASELINE_PX, 5, 2)
BASELINE_BYTES = sum(size for _px, size in BASELINE_MIPS)


#: ``z_cm = scale*raw + offset``, from a robust three-pass fit of the float16 values against
#: the 626 static nodes: 569 inliers, 1.07 m RMS, 3.897 m per quantisation step. Recorded
#: rather than re-fitted per run, because a calibration that moves silently is not one.
BASELINE_SCALE_CM_PER_RAW = 99364.40751843198
BASELINE_OFFSET_CM = -52282.12831764497

#: The fill's no-data test, and the trap in it. ``raw == 0`` is the blank value and decodes
#: to -522.8 m; the world's own floor is -255 m. Anything below this is the raster's blank
#: tail, not sea bed, and testing ``raw > 0`` instead leaks 138,481 texels of it into the
#: field as a false sea floor. A decoded height, so it cannot be read as a raw one.
FILL_FLOOR_CM = -26000.0


def decode_baseline(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The interface raster's float16 texels to world centimetres, and where it says anything.

    Split out from the read so the rule is a pure function a test can hold. The no-data test
    is on the DECODED height against ``FILL_FLOOR_CM``: ``raw > 0`` looks equivalent and is
    not, because the blank value is ``raw == 0`` and it decodes to about -522 m.
    """
    z_cm = values.astype(np.float32) * BASELINE_SCALE_CM_PER_RAW + BASELINE_OFFSET_CM
    return z_cm, z_cm > FILL_FLOOR_CM


def read_baseline(store) -> tuple[np.ndarray, np.ndarray]:
    """``HeightData_Test`` as world centimetres, with the mask of where it says anything.

    The length check is the integrity check: 2048 down to 128 at two bytes a texel is one
    number, and a file that is not that long was re-cooked at another size or mip count.
    """
    if BASELINE_PATH not in store.by_path:
        raise SystemExit(
            "HeightData_Test is not in the container. The interface raster moved or was "
            "renamed, which means the game changed; the fill layer has no source."
        )
    raw = store.read_path(BASELINE_PATH)
    if len(raw) != BASELINE_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in BASELINE_MIPS)
        raise SystemExit(
            f"HeightData_Test.ubulk is {len(raw)} bytes, expected exactly {BASELINE_BYTES} "
            f"-- the mip chain {chain} at two bytes per float16 texel. A different length "
            "means the raster was re-cooked, so refusing to decode mip 0 out of a file "
            "whose layout is no longer known."
        )
    values = np.frombuffer(raw[: BASELINE_PX * BASELINE_PX * 2], dtype="<f2").reshape(
        BASELINE_PX, BASELINE_PX
    )
    return decode_baseline(values)
