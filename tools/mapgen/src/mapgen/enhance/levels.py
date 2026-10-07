"""The enhanced levels: cut, upscaled, repaired and tiled; ``checks`` re-measures the claims."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from mapgen.enhance.checks import (
    SEAM_RATIO_MAX,
    LowZoomCheck,
    SeamCheck,
    low_zoom_residual,
    seam_check,
)
from mapgen.enhance.pixels import (
    COLOUR_FIX_SIGMA,
    FAINT_FEATHER,
    FAINT_GROW,
    FAINT_HI,
    FAINT_LO,
    FAINT_WINDOW,
    PRESHARPEN_AMOUNT,
    PRESHARPEN_EDGE,
    PRESHARPEN_HI,
    PRESHARPEN_NEIGHBOURS,
    PRESHARPEN_ON,
    PRESHARPEN_ROUNDS,
    PRESHARPEN_SIGMA,
    ImageModule,
    colour_fix,
    hybrid_upscale,
    presharpen,
)
from mapgen.enhance.upscaler import (
    ENHANCE_EXE_NAME,
    ENHANCE_MODEL,
    ENHANCE_OVERLAP_PX,
    ENHANCE_SCALE,
    ENHANCE_TILE_PX,
    EnhanceError,
    Upscaler,
    run_upscaler,
)
from mapgen.tiles.recipes import ENHANCE_RECIPE, ENHANCE_RECIPES, UNNUMBERED_RECIPE
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    cut_square,
    enhanced_top_z,
    level_record,
    pyramid_top_z,
)
from satisfactory_mcp.core.jsontypes import JsonObject

if TYPE_CHECKING:
    from PIL import Image

__all__ = ["ENHANCE_WORK", "enhance_levels"]

#: Deleted before and after the stage, so a dead run leaves nothing a later one merges in.
ENHANCE_WORK = "enhance.work"


def _padded_crop(
    sheet: Image.Image, image_mod: ImageModule, tx: int, ty: int, tile: int, overlap: int
) -> Image.Image:
    """One source square plus ``overlap`` px of context on every side.

    Off the sheet -- only at its outer border -- the pad is black. Every padded pixel is
    cropped away after upscaling, so the pad only ever affects what the model sees as
    context at the world's edge, which is open ocean.
    """
    x0, y0 = tx * tile, ty * tile
    box = (x0 - overlap, y0 - overlap, x0 + tile + overlap, y0 + tile + overlap)
    clamped = (
        max(0, box[0]),
        max(0, box[1]),
        min(sheet.width, box[2]),
        min(sheet.height, box[3]),
    )
    crop = sheet.crop(clamped).convert("RGB")
    if crop.size != (tile + 2 * overlap, tile + 2 * overlap):
        pad = image_mod.new("RGB", (tile + 2 * overlap, tile + 2 * overlap))
        pad.paste(crop, (clamped[0] - box[0], clamped[1] - box[1]))
        crop = pad
    return crop


@dataclass(frozen=True)
class _Squares:
    """The sheet as the model's grid: ``grid`` squares a side, each ``tile`` px plus overlap."""

    sheet: Image.Image
    image_mod: ImageModule
    grid: int
    tile: int
    overlap: int

    def crop(self, tx: int, ty: int) -> Image.Image:
        return _padded_crop(self.sheet, self.image_mod, tx, ty, self.tile, self.overlap)

    def names(self) -> list[tuple[int, int, str]]:
        """Each square's column, row and file name, row by row."""
        grid = range(self.grid)
        return [(tx, ty, f"t_{tx:02d}_{ty:02d}.png") for ty in grid for tx in grid]


class _CutSquares(NamedTuple):
    seconds: float
    presharpen_s: float
    lifted: list[float]


class _CutLevels(NamedTuple):
    written: dict[int, int]
    coverage: list[float]
    repair_s: float
    colour_s: float
    seconds: float


def _cut_source_squares(squares: _Squares, src_dir: Path) -> _CutSquares:
    """Each source square with its overlap, pre-sharpened, written as the model's input."""
    started = time.perf_counter()
    presharpen_s = 0.0
    lifted: list[float] = []
    for tx, ty, name in squares.names():
        crop = squares.crop(tx, ty)
        mark = time.perf_counter()
        crop, raised = presharpen(crop, squares.image_mod)
        presharpen_s += time.perf_counter() - mark
        lifted.append(raised)
        crop.save(src_dir / name)
    seconds = time.perf_counter() - started - presharpen_s
    count, side = squares.grid**2, squares.tile + 2 * squares.overlap
    print(
        f"  enhance: {count} source squares of {side}px "
        f"({squares.tile} + 2x{squares.overlap} overlap) cut in {seconds:.1f}s"
    )
    print(
        f"  enhance: pre-sharpened in {presharpen_s:.1f}s; that mask covers "
        f"{sum(lifted) / len(lifted) * 100:.1f}% of the sheet"
    )
    return _CutSquares(seconds, presharpen_s, lifted)


def _upscale_squares(
    upscaler: Upscaler, src_dir: Path, up_dir: Path, count: int, scale: int
) -> float:
    """Run the binary over every square; its seconds, or EnhanceError for a short run."""
    started = time.perf_counter()
    code, log = run_upscaler(upscaler.exe, upscaler.models, src_dir, up_dir, scale)
    seconds = time.perf_counter() - started
    produced = sorted(up_dir.glob("*.png"))
    if code != 0 or len(produced) != count:
        raise EnhanceError(
            f"{upscaler.exe} exited {code} after {seconds:.0f}s with "
            f"{len(produced)} of {count} squares written.\n"
            f"{log}\n"
            "The pyramid already installed has not been touched -- this ran inside the "
            f"staging tree. Nothing here recovers by falling back to Lanczos: re-run "
            "without --enhance if that is what is wanted, and the sidecar will say so."
        )
    print(
        f"  enhance: {count} squares upscaled {scale}x with {ENHANCE_MODEL} in "
        f"{seconds:.1f}s ({seconds / count:.2f}s each)"
    )
    return seconds


def _repair_and_cut_levels(
    squares: _Squares, up_dir: Path, dest: Path, levels: range, scale: int, tile_px: int
) -> _CutLevels:
    """Repair each upscaled square towards its source and cut its core into ``levels``."""
    started = time.perf_counter()
    repair_s = colour_s = 0.0
    core_px = squares.tile * scale
    margin = squares.overlap * scale
    enhanced_top = levels.stop - 1
    written = {z: 0 for z in levels}
    coverage: list[float] = []
    image_mod = squares.image_mod
    for tx, ty, name in squares.names():
        # The untouched source, never in/: both repairs correct TOWARDS it.
        source = squares.crop(tx, ty)
        upscaled = image_mod.open(up_dir / name).convert("RGB")
        mark = time.perf_counter()
        blended, covered = hybrid_upscale(source, upscaled, image_mod, scale)
        repair_s += time.perf_counter() - mark
        mark = time.perf_counter()
        # Before the crop: the blur reaches ~25 px into the neighbour, or it seams.
        fixed = colour_fix(blended, source, image_mod)
        colour_s += time.perf_counter() - mark
        core = fixed.crop((margin, margin, margin + core_px, margin + core_px))
        coverage.append(covered)
        for z in written:
            side = core_px >> (enhanced_top - z)
            lanczos = image_mod.Resampling.LANCZOS
            piece = core if side == core_px else core.resize((side, side), lanczos)
            span = side // tile_px
            written[z] += cut_square(piece, dest, z, tx * span, ty * span, tile_px)
    seconds = time.perf_counter() - started - repair_s - colour_s
    count = squares.grid**2
    print(
        f"  enhance: faint detail repaired in {repair_s:.1f}s, low frequencies restored in "
        f"{colour_s:.1f}s ({colour_s / count:.2f}s each)"
    )
    return _CutLevels(written, coverage, repair_s, colour_s, seconds)


def _report(seams: SeamCheck, low: LowZoomCheck, coverage: list[float]) -> None:
    top = low.top
    print(
        f"  enhance: seams read {seams.seam_median:.2f}x their own control where "
        f"boundaries that are NOT seams read {seams.interior_median:.2f}x theirs"
    )
    print(
        f"  enhance: the mask covers {sum(coverage) / len(coverage) * 100:.1f}% of the sheet; "
        f"z{top} from the artwork differs {low.median_ratio:.2f}x its control from the "
        f"same tile taken out of z{top + 1}"
    )
    if not seams.invisible:
        print(
            f"  WARNING: the seams read {seams.seam_median:.2f}x their control against "
            f"{seams.interior_median:.2f}x at boundaries with no seam in them, over the "
            f"{SEAM_RATIO_MAX}x this file calls invisible. The overlap is not buying enough "
            "context. The tiles are still written -- _meta.tiles.enhancement says so."
        )


def _enhancement_record(
    squares: _Squares,
    upscaler: Upscaler,
    scale: int,
    timings: tuple[_CutSquares, float, _CutLevels],
    checks: tuple[SeamCheck, LowZoomCheck],
) -> JsonObject:
    """The sidecar's ``enhancement`` block: the recipe, the binary, each pass, the checks."""
    cut, upscale_s, levels = timings
    lifted, coverage = cut.lifted, levels.coverage
    return {
        "recipe": ENHANCE_RECIPE,
        "recipe_name": ENHANCE_RECIPES[ENHANCE_RECIPE],
        "recipe_history": {str(n): text for n, text in ENHANCE_RECIPES.items()},
        "recipe_role": (
            "which pipeline cut the tiles beside this sidecar. The no-silent-downgrade "
            "guard compares these numbers rather than a boolean, so re-cutting an older "
            "recipe's tiles with a newer one reads as the upgrade it is; a pyramid whose "
            "sidecar says enhanced and names no recipe was cut by recipe "
            f"{UNNUMBERED_RECIPE}."
        ),
        "model": ENHANCE_MODEL,
        "scale": scale,
        "source_tile_px": squares.tile,
        "overlap_px": squares.overlap,
        "source_squares": squares.grid**2,
        "enhanced_sheet_px": squares.sheet.width * scale,
        "binary": {
            "url": upscaler.url,
            "sha256": upscaler.sha256,
            "exe": ENHANCE_EXE_NAME,
            "cached_at": str(upscaler.home),
            "licence": (
                "Real-ESRGAN ncnn-Vulkan, BSD-3-Clause, by Xintao Wang et al. A "
                "prebuilt binary run offline at generation time; not vendored, not a "
                "dependency of this project, and no part of it is in the output."
            ),
        },
        "presharpen": {
            "rule": (
                "input = source * (1 - m) + unsharp(source) * m, m from presharpen_mask "
                "on the source luma alone, fed to the model in place of the source"
            ),
            "rounds": PRESHARPEN_ROUNDS,
            "sigma_px": PRESHARPEN_SIGMA,
            "amount": PRESHARPEN_AMOUNT,
            "window_px": FAINT_WINDOW,
            "grow_px": FAINT_GROW,
            "band": [FAINT_LO, PRESHARPEN_HI],
            "feather_px": FAINT_FEATHER,
            "mask_on_above": PRESHARPEN_ON,
            "dilation": (
                f"one passive round: a pixel joins the mask only if at least "
                f"{PRESHARPEN_NEIGHBOURS} of its 8 neighbours are already in it"
            ),
            "edge_feather_px": PRESHARPEN_EDGE,
            "mask_coverage": round(sum(lifted) / len(lifted), 4),
            "why": (
                "repairing the output cannot put back a stroke the model never drew, and "
                "a mark 4 to 10 luma below its surroundings is under the model's floor. "
                "Raising the weak band by about a third on the way IN carries it over, "
                "and the model keeps some 85% of what it is handed against 79% of a mark "
                "it half-missed. The band stops at "
                f"{PRESHARPEN_HI} rather than the repair's {FAINT_HI} because amplifying "
                "a mid stroke gives the model more contrast to expand, not less."
            ),
        },
        "hybrid": {
            "rule": (
                "output = anime * (1 - w) + lanczos * w, w from faint_mask on the "
                "source luma alone, upsampled bilinearly"
            ),
            "window_px": FAINT_WINDOW,
            "grow_px": FAINT_GROW,
            "band": [FAINT_LO, FAINT_HI],
            "feather_px": FAINT_FEATHER,
            "mask_coverage": round(sum(coverage) / len(coverage), 4),
            "why": (
                "the model's one measured defect is expanded contrast: it deepens strong "
                "strokes and erases the faintest ones. Lanczos is the only candidate "
                "that keeps weak strokes at full depth, so it is used exactly where they "
                "are and nowhere else."
            ),
        },
        "colour_fix": {
            "rule": (
                "output = out - blur(out, sigma) + blur(lanczos(source), sigma), all "
                "three channels, at the 4x output's own resolution"
            ),
            "sigma_px": COLOUR_FIX_SIGMA,
            "sigma_measured_in": "pixels of the enhanced output, not of the source",
            "why": (
                "the model may decide detail the source does not resolve; it may not "
                "decide what colour a flat fill is, and it drifts them by up to a whole "
                "level of the map's own palette. Swapping the low band back halves that "
                "for no measurable sharpness, which is the largest single improvement "
                f"the second bake-off round found. Sigma exceeds a stroke's width at "
                f"{scale}x or the fix would blur back the sharpening it protects."
            ),
        },
        "seams": checks[0].record(),
        "low_zoom": checks[1].record(),
        "timings_s": {
            "cut_source_squares": round(cut.seconds, 2),
            "presharpen": round(cut.presharpen_s, 2),
            "upscale": round(upscale_s, 2),
            "faint_repair": round(levels.repair_s, 2),
            "colour_fix": round(levels.colour_s, 2),
            "cut_enhanced_levels": round(levels.seconds, 2),
            "total": round(
                cut.seconds
                + cut.presharpen_s
                + upscale_s
                + levels.repair_s
                + levels.colour_s
                + levels.seconds,
                2,
            ),
        },
    }


def enhance_levels(
    sheet: Image.Image,
    image_mod: ImageModule,
    dest: Path,
    upscaler: Upscaler,
    work: Path,
    *,
    tile_px: int = PYRAMID_TILE_PX,
    source_tile: int = ENHANCE_TILE_PX,
    overlap: int = ENHANCE_OVERLAP_PX,
    scale: int = ENHANCE_SCALE,
) -> JsonObject:
    """Add the upscaled levels to ``dest``, and measure everything the sidecar claims.

    The enhanced sheet is never held whole: at 32768 px it would be 3.2 GB of RGB, which is
    both more than a machine wants to spend and the exact overflow that makes the binary
    segfault. It exists only as sixty-four cores, each cut into its z7 tiles and its z6
    ones as soon as it is blended and then dropped.

    Four stages, and the model is only the second. The squares written to ``in/`` are
    pre-sharpened and are the model's input alone; every stage that needs the untouched
    source re-cuts it from the sheet rather than reading them back, because a repair
    measured against an already-sharpened square would be measuring its own work.
    """
    top = pyramid_top_z(sheet.width, tile_px)
    enhanced_top = enhanced_top_z(sheet.width, scale, tile_px)
    grid = sheet.width // source_tile
    if grid * source_tile != sheet.width:
        raise EnhanceError(
            f"a {sheet.width} px sheet does not divide into {source_tile} px squares, so the "
            "upscaler cannot be fed without a partial tile"
        )
    src_dir, up_dir = work / "in", work / "out"
    for directory in (src_dir, up_dir):
        directory.mkdir(parents=True)
    squares = _Squares(sheet, image_mod, grid, source_tile, overlap)
    cut = _cut_source_squares(squares, src_dir)
    upscale_s = _upscale_squares(upscaler, src_dir, up_dir, grid * grid, scale)
    enhanced = range(top + 1, enhanced_top + 1)
    levels = _repair_and_cut_levels(squares, up_dir, dest, enhanced, scale, tile_px)
    source = (
        f"the sheet pre-sharpened, upscaled {scale}x by {ENHANCE_MODEL}, faint "
        "detail restored and low frequencies put back"
    )
    records: list[JsonObject] = [
        level_record(z, written, source, tile_px) for z, written in sorted(levels.written.items())
    ]
    core_px = source_tile * scale
    seams = seam_check(dest, image_mod, enhanced_top, core_px // tile_px, 1 << enhanced_top)
    low = low_zoom_residual(dest, image_mod, top, tile_px)
    _report(seams, low, levels.coverage)
    return {
        "levels": list(records),
        "enhancement": _enhancement_record(
            squares, upscaler, scale, (cut, upscale_s, levels), (seams, low)
        ),
    }
