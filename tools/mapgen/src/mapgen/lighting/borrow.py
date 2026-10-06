"""The artwork borrow: the drawn map's ink detail and province tint, and its record."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

import numpy as np
from scipy import ndimage

from mapgen.gamedata.frame import BOUNDS_M
from satisfactory_mcp.core.arrays import BoolMask, F32Grid, I8Grid, U8Grid
from satisfactory_mcp.core.gameassets.container import SHEET_PX
from satisfactory_mcp.core.jsontypes import JsonObject
from satisfactory_mcp.domain.spatial import heightfield as hf

if TYPE_CHECKING:
    from PIL import Image

__all__ = [
    "BORROW_CLAMP",
    "BORROW_DETAIL_SIGMAS",
    "BORROW_DETAIL_SIGMA_PX",
    "BORROW_DETAIL_SOFTEN_PX",
    "BORROW_FEATHER_M",
    "BORROW_GAIN",
    "BORROW_INK_PX",
    "BORROW_LUMA",
    "BORROW_PROVENANCE",
    "ProvinceBorrow",
    "artwork_detail",
    "borrow_metadata",
    "coarse_province",
]


#: The provinces coarser than the artwork: both cliff values, and fill. Not landscape.
BORROW_PROVENANCE = (*hf.PROV_CLIFF_VALUES, hf.PROV_FILL)

#: The borrow's fade across a province boundary, in metres.
BORROW_FEATHER_M = 6.0

#: High-pass sigma in artwork pixels: only detail finer than about 7 m comes across.
BORROW_DETAIL_SIGMA_PX = 8.0

#: Soften, then ``tanh`` at this many sigmas: borrow the shading, not the ink.
BORROW_DETAIL_SOFTEN_PX = 1.6
BORROW_DETAIL_SIGMAS = 1.2

#: Strokes narrower than this, in artwork pixels, dark or light, are ink: gone before the high pass.
BORROW_INK_PX = 7.0

#: How much reaches the picture, and how far it may push a pixel; picked by looking.
BORROW_GAIN = 0.30
BORROW_CLAMP = (0.74, 1.26)

#: Rec. 601 luma: the artwork's light crosses, its colour never does.
BORROW_LUMA: F32Grid = np.array([0.299, 0.587, 0.114], np.float32)


class ProvinceBorrow(TypedDict):
    """Where the borrow applies: the coarse provinces, their share of the field, the feather."""

    provinces: list[str]
    share_of_the_field: float
    feather_m: float
    role: str


def _disk(diameter_px: float) -> BoolMask:
    r = (diameter_px - 1) / 2.0
    yy, xx = np.mgrid[-int(r) : int(r) + 1, -int(r) : int(r) + 1]
    return xx * xx + yy * yy <= r * r + 0.5


def _high_pass(luma: F32Grid) -> F32Grid:
    high = luma - ndimage.gaussian_filter(luma, BORROW_DETAIL_SIGMA_PX, mode="nearest")
    return ndimage.gaussian_filter(high, BORROW_DETAIL_SOFTEN_PX, mode="nearest")


def artwork_detail(sheet: U8Grid | Image.Image) -> tuple[I8Grid, JsonObject]:
    """The artwork's luminance high pass as int8 (67 MB, not 268), and its scaling.

    A grey closing, then an opening, first removes every stroke narrower than
    ``BORROW_INK_PX``, dark or light: the ink. The scale stays the spread of the sheet as
    drawn, so the shading lends as much as it did with the ink in.
    """
    rgb = np.asarray(sheet, np.float32)
    luma = rgb @ BORROW_LUMA
    spread = float(_high_pass(luma).std())
    if BORROW_INK_PX > 1:
        disk = _disk(BORROW_INK_PX)
        luma = ndimage.grey_closing(luma, footprint=disk, mode="nearest")
        luma = ndimage.grey_opening(luma, footprint=disk, mode="nearest")
    high = _high_pass(luma)
    detail = (np.tanh(high / max(spread * BORROW_DETAIL_SIGMAS, 1e-6)) * 127.0).astype(np.int8)
    return detail, {
        "role": (
            "the artwork sheet's own luminance minus its Gaussian blur, i.e. everything the "
            "drawn map says below about "
            f"{BORROW_DETAIL_SIGMA_PX * (BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / SHEET_PX:.1f}"
            " m and nothing above it"
        ),
        "sheet_px": SHEET_PX,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / SHEET_PX, 4),
        "high_pass_sigma_px": BORROW_DETAIL_SIGMA_PX,
        "ink_closing_px": BORROW_INK_PX,
        "soften_sigma_px": BORROW_DETAIL_SOFTEN_PX,
        "luma_weights": [float(value) for value in BORROW_LUMA],
        "measured_std": round(spread, 4),
        "tanh_knee_at_sigmas": BORROW_DETAIL_SIGMAS,
        "why_tanh": (
            "the artwork is a drawing and a drawing has strokes -- every rock formation is "
            "outlined in hard dark ink. A soft clip lets the mid-tones (the shading) through "
            "almost linearly and saturates the outliers (the ink), which is the difference "
            "between borrowing light and tracing lines"
        ),
        "stored_as": "int8, +-127 at full saturation",
    }


def coarse_province(field: hf.Field) -> tuple[U8Grid, ProvinceBorrow]:
    """Where the field is coarser than the artwork: a feathered uint8 mask at 1 m."""
    inside = np.isin(field.provenance_plane, BORROW_PROVENANCE)
    share = float(inside.mean())
    feather = ndimage.gaussian_filter(
        inside.astype(np.float32), BORROW_FEATHER_M * 100.0 / field.spacing_cm, mode="nearest"
    )
    return (np.clip(feather, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), {
        "provinces": [hf.PROV_NAMES[value] for value in BORROW_PROVENANCE],
        "share_of_the_field": round(100 * share, 2),
        "feather_m": BORROW_FEATHER_M,
        "role": (
            "1 where the field's own province is coarser than the artwork -- rasterised "
            "collision hulls, or a 3.9 m block raster -- 0 over the landscape layer, which "
            "is continuous geometry and keeps shading of its own, and a Gaussian ramp "
            "between them so the provenance byte is never itself drawn"
        ),
    }


def borrow_metadata(detail_meta: JsonObject, province_meta: ProvinceBorrow) -> JsonObject:
    """The render sidecar's ``artwork_detail`` source: what the borrow read and where."""
    return {
        "artwork_detail": {
            "name": f"the game's own {SHEET_PX} px map sheet, from its four BC1 slices",
            "licence": (
                "Coffee Stain Studios' own artwork, read out of the reader's installed copy "
                "of the game. Its LUMINANCE only, high-passed, and multiplied into shading "
                "-- no pixel of it is drawn and no colour of it crosses. Not committed, not "
                "redistributed, and served to localhost only."
            ),
            **detail_meta,
            "applied_where": {
                "provinces": list(province_meta["provinces"]),
                "share_of_the_field": province_meta["share_of_the_field"],
                "feather_m": province_meta["feather_m"],
                "role": province_meta["role"],
            },
            "gain": BORROW_GAIN,
            "clamp": list(BORROW_CLAMP),
            "reading": (
                "the field is one resolution but not one accuracy. Over the landscape "
                "province -- 45.3% of it -- the geometry is continuous and its own shading "
                "is the best there is, so nothing is borrowed. Over cliff and fill it is "
                "rasterised hulls and 3.9 m blocks, which is why those provinces read as "
                "melted wax when drawn from the field alone, and the artwork drew the same "
                "ground at 0.92 m."
            ),
        }
    }
