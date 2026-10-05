"""The render sidecar's record of the artwork borrow: what it read, and where it applies."""

from __future__ import annotations

from mapgen.lighting.hillshade import BORROW_CLAMP, BORROW_GAIN
from satisfactory_mcp.core.gameassets.container import SHEET_PX

__all__ = ["borrow_metadata"]


def borrow_metadata(detail_meta: dict, province_meta: dict) -> dict:
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
            "applied_where": province_meta,
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
