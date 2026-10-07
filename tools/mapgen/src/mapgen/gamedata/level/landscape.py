"""The cooked landscape: component heights stitched into one frame, and their seams."""

from __future__ import annotations

import struct
from collections.abc import Iterable, Sequence
from typing import NamedTuple, TypeAlias, TypedDict

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, U16Grid

__all__ = [
    "LANDSCAPE_COMPONENT_QUADS",
    "LANDSCAPE_N",
    "LANDSCAPE_ORIGIN_Z_CM",
    "LANDSCAPE_PER_UNIT",
    "LANDSCAPE_SCALE_CM",
    "LANDSCAPE_SECTION_ORIGIN",
    "LANDSCAPE_ZERO",
    "SEAM_DISAGREEMENT_MAX",
    "TRANSFORM_TOLERANCE",
    "LandscapeFrame",
    "LandscapeSweep",
    "Proxy",
    "drop_offsets",
    "grass_data_heights",
    "landscape_frame",
]


#: ``ComponentSizeQuads`` is 127, so a landscape component is 128x128 height samples.
LANDSCAPE_N = 128

#: The height encoding, and what this run refuses to proceed without: the proxies state
#: their own scale and origin, and a build that disagrees with these gets an error rather
#: than a field wrong by a factor.
LANDSCAPE_SCALE_CM = 100.0
LANDSCAPE_ORIGIN_Z_CM = 100.0
LANDSCAPE_ZERO = 32768.0
LANDSCAPE_PER_UNIT = 128.0

#: How far a measured proxy transform may sit from those numbers before the run stops. A
#: hundredth of a centimetre: floating-point noise in a double, and nothing else.
TRANSFORM_TOLERANCE = 0.01


#: ``LandscapeSectionOffset - location / scale`` on every proxy, in landscape quads. The
#: terrain plane's georeference is derived from it, so a cook that moves it must fail here.
LANDSCAPE_SECTION_ORIGIN = 508.0

#: Quads per landscape component: neighbouring components share one edge row of samples.
LANDSCAPE_COMPONENT_QUADS = LANDSCAPE_N - 1

#: Shared edge samples on which two components' GrassData disagree. 46 on build 502094;
#: far more means the stitch is reading something other than heights.
SEAM_DISAGREEMENT_MAX = 100


#: A landscape proxy's ``(origin x, origin y, z offset, scale x, scale y, scale z)``.
Proxy: TypeAlias = tuple[float, float, float, float, float, float]


class LandscapeSweep(TypedDict):
    """What the stitch reads of ``sweep_levels``' record."""

    packages: int
    components: list[tuple[int, int, U16Grid]]
    proxies: list[Proxy]


class LandscapeFrame(TypedDict):
    """What ``landscape_frame`` returns: the stitched raster, its georeference, its holes."""

    raw: U16Grid
    seam_disagreements: int
    z_cm: F32Grid
    good: BoolMask
    width: int
    height: int
    x0_cm: float
    y0_cm: float
    scale_cm: float
    origin_z_cm: float
    components: int
    coverage: float
    hole_texels: int
    hole_blobs: int


class _ProxyTransform(NamedTuple):
    origin_x: float
    origin_y: float
    origin_z: float
    scale_x: float
    scale_y: float
    scale_z: float


class _Stitched(NamedTuple):
    raw: U16Grid
    covered: BoolMask
    seam_disagreements: int
    min_x: int
    min_y: int


def grass_data_heights(tail: bytes) -> U16Grid | None:
    """The ``128*128`` uint16 height samples out of a ``LandscapeComponent``'s tail.

    Past the property tags the export carries a bool, a GUID and a float, then the
    ``GrassData`` map: an element count, a ``TMap`` of that many 8-byte entries, and the
    ``TArray<uint8>`` whose first ``2*NumElements`` bytes are the heights. Every offset is
    read from a length in the blob; a component that is not 128 samples square is skipped
    rather than reinterpreted.
    """
    try:
        num = struct.unpack_from("<I", tail, 24)[0]
        entries = struct.unpack_from("<I", tail, 28)[0]
        pos = 32 + 8 * entries
        total = struct.unpack_from("<I", tail, pos)[0]
        pos += 4
    except struct.error:
        return None
    want = LANDSCAPE_N * LANDSCAPE_N
    if num != want or total < 2 * num or pos + 2 * num > len(tail):
        return None
    return np.frombuffer(tail, dtype="<u2", count=num, offset=pos).reshape(LANDSCAPE_N, LANDSCAPE_N)


def _agreed(values: Iterable[float], label: str) -> float:
    """The one value every proxy states, to a thousandth; a refusal when they disagree."""
    distinct = sorted({round(v, 3) for v in values})
    if len(distinct) != 1:
        raise SystemExit(
            f"the landscape proxies disagree about {label}: {distinct[:6]}. This file "
            "stitches one frame with one transform, and cannot stitch several."
        )
    return distinct[0]


def _proxy_transform(proxies: Sequence[Sequence[float]]) -> _ProxyTransform:
    """The proxies' shared origin, Z offset and scale, checked against the encoding."""
    found = _ProxyTransform(
        _agreed((p[0] for p in proxies), "their world origin in X"),
        _agreed((p[1] for p in proxies), "their world origin in Y"),
        _agreed((p[2] for p in proxies), "their Z offset"),
        _agreed((p[3] for p in proxies), "their X scale"),
        _agreed((p[4] for p in proxies), "their Y scale"),
        _agreed((p[5] for p in proxies), "their Z scale"),
    )
    for measured, expected, label in (
        (found.scale_x, LANDSCAPE_SCALE_CM, "X scale"),
        (found.scale_y, LANDSCAPE_SCALE_CM, "Y scale"),
        (found.scale_z, LANDSCAPE_SCALE_CM, "Z scale"),
        (found.origin_z, LANDSCAPE_ORIGIN_Z_CM, "Z offset"),
        (found.origin_x, LANDSCAPE_SECTION_ORIGIN, "section origin in X"),
        (found.origin_y, LANDSCAPE_SECTION_ORIGIN, "section origin in Y"),
    ):
        if abs(measured - expected) > TRANSFORM_TOLERANCE:
            raise SystemExit(
                f"the landscape's {label} is {measured}, not the {expected} this file was "
                "measured against. The height encoding depends on it, so decoding anyway "
                "would produce a field that is wrong by a factor rather than by an offset."
            )
    return found


def _stitch(components: Sequence[tuple[int, int, U16Grid]]) -> _Stitched:
    """The components dropped onto one raster by section base, shared edges compared."""
    xs = [c[0] for c in components]
    ys = [c[1] for c in components]
    min_x, min_y = min(xs), min(ys)
    off_lattice = sum(
        1
        for x, y in zip(xs, ys, strict=True)
        if (x - min_x) % LANDSCAPE_COMPONENT_QUADS or (y - min_y) % LANDSCAPE_COMPONENT_QUADS
    )
    if off_lattice:
        raise SystemExit(
            f"{off_lattice} landscape components are not on the {LANDSCAPE_COMPONENT_QUADS}-quad "
            "lattice. The component size changed, so the shared edges this stitch assumes "
            "are not shared any more."
        )
    width = max(xs) + LANDSCAPE_N - min_x
    height = max(ys) + LANDSCAPE_N - min_y
    raw = np.zeros((height, width), dtype="<u2")
    covered = np.zeros((height, width), dtype=bool)
    seam_disagreements = 0
    for base_x, base_y, heights in components:
        row, col = base_y - min_y, base_x - min_x
        cut = (slice(row, row + LANDSCAPE_N), slice(col, col + LANDSCAPE_N))
        seam_disagreements += int((covered[cut] & (raw[cut] != heights)).sum())
        raw[cut] = heights
        covered[cut] = True
    if seam_disagreements > SEAM_DISAGREEMENT_MAX:
        raise SystemExit(
            f"{seam_disagreements} shared edge samples disagree between neighbouring "
            f"components, against at most {SEAM_DISAGREEMENT_MAX}. The GrassData read is "
            "off, or the cook stopped keeping it in step with the heights."
        )
    return _Stitched(raw, covered, seam_disagreements, min_x, min_y)


def landscape_frame(sweep: LandscapeSweep) -> LandscapeFrame:
    """Stitch the components into one raster and pin it to the world. Nothing resampled.

    That the proxies all state the same origin, scale and Z offset is checked rather than
    assumed: a build that split the landscape into frames with different transforms would
    otherwise stitch into a plausible, wrong field.
    """
    components = sweep["components"]
    proxies = sweep["proxies"]
    if not components or not proxies:
        raise SystemExit(
            "no LandscapeComponent or no LandscapeStreamingProxy was found in "
            f"{sweep['packages']} packages. The landscape moved or was renamed, which means "
            "the game changed; nothing here can be trusted until that is looked at."
        )
    transform = _proxy_transform(proxies)
    stitched = _stitch(components)
    raw, covered = stitched.raw, stitched.covered
    height, width = raw.shape

    # raw == 0 inside a component that IS present is a landscape hole -- a cave mouth or a
    # deliberately cut-out section -- not a height of -255 m. Left as no data for the cliff
    # layer to fill or for nothing to.
    hole = covered & (raw == 0)
    good = covered & ~hole
    _labelled, blobs = ndimage.label(hole)

    z_cm = (
        raw.astype(np.float32) - LANDSCAPE_ZERO
    ) / LANDSCAPE_PER_UNIT * transform.scale_z + transform.origin_z
    raw[~covered] = 0
    return {
        "raw": raw,
        "seam_disagreements": stitched.seam_disagreements,
        "z_cm": z_cm,
        "good": good,
        "width": width,
        "height": height,
        "x0_cm": (stitched.min_x - transform.origin_x) * transform.scale_x,
        "y0_cm": (stitched.min_y - transform.origin_y) * transform.scale_y,
        "scale_cm": transform.scale_x,
        "origin_z_cm": transform.origin_z,
        "components": len(components),
        "coverage": float(covered.mean()),
        "hole_texels": int(hole.sum()),
        "hole_blobs": int(blobs),
    }


def drop_offsets(frame: LandscapeFrame) -> tuple[int, int]:
    """Where the landscape frame lands in the output grid, in whole texels.

    Asserted rather than rounded into: the landscape is a 1 m grid and so is the output, so
    a fractional offset means one of the two moved, and resampling would smooth a real
    heightfield to cover it.
    """
    dx = (frame["x0_cm"] - ORIGIN_X_CM) / SPACING_CM
    dy = (frame["y0_cm"] - ORIGIN_Y_CM) / SPACING_CM
    for value, axis in ((dx, "X"), (dy, "Y")):
        if abs(value - round(value)) > 1e-6:
            raise SystemExit(
                f"the landscape frame sits {value:.4f} texels from the output origin in "
                f"{axis}, which is not a whole number. The landscape and the output grid "
                "are both 1 m, so this cannot be dropped in index-aligned any more, and "
                "this file will not silently resample a real heightfield to hide that."
            )
    return round(dx), round(dy)
