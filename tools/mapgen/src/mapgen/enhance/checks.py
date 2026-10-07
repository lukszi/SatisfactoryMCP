"""The claims about the enhanced levels, re-measured: seams, and the low levels' provenance.

Each check reads tiles already cut, and compares like with like: a boundary that is a seam
against one that is not, the artwork's tile against the same tile from the enhanced level.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mapgen.enhance.pixels import ImageModule
from mapgen.enhance.upscaler import ENHANCE_OVERLAP_PX
from mapgen.gamedata.artwork_sheet import line_bytes, mean_abs
from satisfactory_mcp.core.gameassets.pyramid import tile_relpath
from satisfactory_mcp.core.jsontypes import JsonObject

if TYPE_CHECKING:
    from PIL import Image

__all__ = [
    "CONTROL_COLS",
    "LOW_ZOOM_SAMPLES",
    "LOW_ZOOM_STRIDE",
    "SEAM_RATIO_MAX",
    "SEAM_ROWS",
    "LowZoomCheck",
    "SeamCheck",
    "low_zoom_residual",
    "seam_check",
]

#: Where the seam check reads, in tile rows of the enhanced top level, and the columns its
#: control averages over. One arbitrary column pair is too noisy a denominator: on quiet
#: ground it is near zero and any seam divides into it enormously.
SEAM_ROWS = (32, 64, 96)
CONTROL_COLS = (32, 64, 96, 128, 160, 192, 224)

#: How much worse than a boundary that is NOT a seam a real seam may read. An edge in the
#: artwork that lands on a boundary costs the same whether the boundary is a seam or not,
#: which is why the comparison is against that and not against zero.
SEAM_RATIO_MAX = 1.5

#: Where the low-zoom check samples, in tiles of the original top level. Tiles that are
#: flat ocean are dropped rather than counted as agreement.
LOW_ZOOM_STRIDE = 5
LOW_ZOOM_SAMPLES = 12


def _column(image: Image.Image, x: int) -> bytes:
    """One pixel column of an image as raw RGB bytes."""
    return line_bytes(image, (x, 0, x + 1, image.height))


def _column_control(image: Image.Image) -> float:
    """What two adjacent columns of this tile cost, averaged over the whole tile.

    The denominator every ratio below is taken against. One arbitrary column pair is not
    it: quiet ground gives a control near zero, and dividing by that turns an invisible
    difference into an enormous number.
    """
    return sum(mean_abs(_column(image, c), _column(image, c + 1)) for c in CONTROL_COLS) / len(
        CONTROL_COLS
    )


def _boundary(dest: Path, image_mod: ImageModule, z: int, x: int, y: int) -> tuple[float, float]:
    """The across-boundary difference at tile ``(x, y)``, and that tile's own control."""
    left = image_mod.open(dest / tile_relpath(z, x - 1, y))
    right = image_mod.open(dest / tile_relpath(z, x, y))
    edge = mean_abs(_column(left, left.width - 1), _column(right, 0))
    return round(edge, 4), round(_column_control(right), 4)


def _split_boundaries(samples: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Ratios where the ground has variation, absolute differences where it has none.

    A tile of open ocean has a control of exactly zero, so those samples are reported as
    raw differences rather than divided into infinities. On one flat colour, anything but
    zero is a visible line.
    """
    live = sorted(round(edge / control, 3) for edge, control in samples if control)
    flat = sorted(edge for edge, control in samples if not control)
    return live, flat


def _median(values: list[float]) -> float:
    return values[len(values) // 2] if values else 0.0


@dataclass(frozen=True)
class SeamCheck:
    """Seam ratios at level ``z``, where squares meet every ``step`` tiles, beside the control.

    The control is the identical statistic at boundaries halfway between the seams, where two
    tiles were cut from ONE upscaled core and there is nothing to stitch.
    """

    z: int
    step: int
    seams: list[float]
    interior: list[float]
    seam_flat: list[float]
    interior_flat: list[float]

    @property
    def seam_median(self) -> float:
        return _median(self.seams)

    @property
    def interior_median(self) -> float:
        return _median(self.interior)

    @property
    def invisible(self) -> bool:
        """At or under the control's median times SEAM_RATIO_MAX, and zero on flat ground."""
        flat_worst = max(self.seam_flat, default=0.0)
        return self.seam_median <= SEAM_RATIO_MAX * self.interior_median and flat_worst == 0.0

    def record(self) -> JsonObject:
        """The sidecar's ``seams`` block."""
        return {
            "method": (
                f"the across-boundary mean per-channel difference over the tile's own "
                f"adjacent-column difference, at rows {list(SEAM_ROWS)} of z{self.z}. "
                f"seam_ratios are the real boundaries -- one every {self.step} tiles, where "
                "two separately upscaled squares meet -- and interior_ratios the same "
                "statistic halfway between them, where one core was simply cut in two."
            ),
            "overlap_px": ENHANCE_OVERLAP_PX,
            "seam_ratios": list(self.seams),
            "interior_ratios": list(self.interior),
            "seam_median": self.seam_median,
            "interior_median": self.interior_median,
            "seam_worst": max(self.seams) if self.seams else 0.0,
            "interior_worst": max(self.interior) if self.interior else 0.0,
            "on_flat_ground": {
                "note": (
                    "boundaries whose tile has no column-to-column variation at all -- open "
                    "ocean. No ratio is meaningful there, so these are the raw across-boundary "
                    "differences, and they are the strictest test the sheet has: on one flat "
                    "colour, anything but zero is a line a reader would see."
                ),
                "seam_edges": list(self.seam_flat),
                "interior_edges": list(self.interior_flat),
            },
            "threshold": SEAM_RATIO_MAX,
            "seams_invisible": self.invisible,
            "reading": (
                "the seams read at or below what a boundary with no seam in it reads, so the "
                "stitching contributes nothing a reader could pick out from the map's own "
                "edges. The overlap is what buys that: the model never sees a tile edge that "
                "survives into the output."
            ),
        }


def seam_check(dest: Path, image_mod: ImageModule, z: int, step: int, span: int) -> SeamCheck:
    """Is any source-tile boundary visible? Measured against boundaries that are not seams.

    An edge in the artwork that happens to land on a tile boundary reads large whether or
    not the boundary is a seam, so a bare threshold on the ratio would condemn the map's own
    coastlines. Comparing like with like does not.
    """
    seams, seam_flat = _split_boundaries(
        [_boundary(dest, image_mod, z, x, y) for x in range(step, span, step) for y in SEAM_ROWS]
    )
    interior, interior_flat = _split_boundaries(
        [
            _boundary(dest, image_mod, z, x, y)
            for x in range(step // 2, span, step)
            for y in SEAM_ROWS
        ]
    )
    return SeamCheck(z, step, seams, interior, seam_flat, interior_flat)


@dataclass(frozen=True)
class LowZoomCheck:
    """Level ``top`` cut from the artwork against the same tiles taken out of ``top + 1``."""

    top: int
    tile_px: int
    ratios: list[float]

    @property
    def median_ratio(self) -> float:
        return _median(self.ratios)

    def record(self) -> JsonObject:
        """The sidecar's ``low_zoom`` block."""
        top, tile_px = self.top, self.tile_px
        return {
            "method": (
                f"{len(self.ratios)} tiles of z{top} as cut from the artwork, differenced "
                f"against their own four z{top + 1} children mosaicked and Lanczos'd back down "
                f"to {tile_px} px, over the artwork tile's adjacent-column difference"
            ),
            "samples": len(self.ratios),
            "ratios": list(self.ratios),
            "median_ratio": self.median_ratio,
            "levels_from": (
                f"z0..z{top} are downscales of the game's own artwork; only the levels above "
                "it, which have no artwork behind them, come from the enhanced pixels"
            ),
            "indistinguishable": self.median_ratio <= 1.0,
            "reading": (
                "a ratio at or under 1 means the two provenances differ by less than the "
                "artwork differs from its own next column -- so cutting the low levels from "
                "the original changes nothing a reader could see, and taking the simple path "
                "is a measurement rather than a shrug."
            ),
        }


def low_zoom_residual(dest: Path, image_mod: ImageModule, top: int, tile_px: int) -> LowZoomCheck:
    """Would the low levels look different if they came from the enhanced sheet instead?

    Each sampled top-level tile of the artwork is compared against the four enhanced tiles
    above it, mosaicked and Lanczos'd back down to the same resolution -- the two candidate
    provenances for that one tile -- against the seam check's adjacent-column control.
    Tiles whose control is zero, open ocean, cannot disagree and are dropped, not counted.
    """
    span = 1 << top
    ratios: list[float] = []
    for x in range(2, span, LOW_ZOOM_STRIDE):
        for y in range(2, span, LOW_ZOOM_STRIDE):
            if len(ratios) >= LOW_ZOOM_SAMPLES:
                break
            artwork = image_mod.open(dest / tile_relpath(top, x, y)).convert("RGB")
            control = _column_control(artwork)
            if not control:
                continue
            mosaic = image_mod.new("RGB", (tile_px * 2, tile_px * 2))
            for dx in (0, 1):
                for dy in (0, 1):
                    child = image_mod.open(dest / tile_relpath(top + 1, 2 * x + dx, 2 * y + dy))
                    mosaic.paste(child.convert("RGB"), (dx * tile_px, dy * tile_px))
            back = mosaic.resize((tile_px, tile_px), image_mod.Resampling.LANCZOS)
            middle = tile_px // 2
            difference = mean_abs(_column(artwork, middle), _column(back, middle))
            ratios.append(round(difference / control, 3))
    ratios.sort()
    return LowZoomCheck(top, tile_px, ratios)
