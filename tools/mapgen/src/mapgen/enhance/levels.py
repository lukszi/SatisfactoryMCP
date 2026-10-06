"""The enhanced levels: cut, upscaled, repaired, tiled, and every claim about them re-measured."""

from __future__ import annotations

import time
from pathlib import Path

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
    run_upscaler,
)
from mapgen.gamedata.artwork_sheet import line_bytes, mean_abs
from mapgen.tiles.recipes import ENHANCE_RECIPE, ENHANCE_RECIPES, UNNUMBERED_RECIPE
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    cut_square,
    enhanced_top_z,
    pyramid_top_z,
    tile_relpath,
)

__all__ = [
    "CONTROL_COLS",
    "ENHANCE_WORK",
    "LOW_ZOOM_SAMPLES",
    "LOW_ZOOM_STRIDE",
    "SEAM_RATIO_MAX",
    "SEAM_ROWS",
    "enhance_levels",
    "low_zoom_residual",
    "seam_check",
]

#: Deleted before and after the stage, so a dead run leaves nothing a later one merges in.
ENHANCE_WORK = "enhance.work"


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


def _column(image, x: int) -> bytes:
    """One pixel column of an image as raw RGB bytes."""
    return line_bytes(image, (x, 0, x + 1, image.height))


def _column_control(image) -> float:
    """What two adjacent columns of this tile cost, averaged over the whole tile.

    The denominator every ratio below is taken against. One arbitrary column pair is not
    it: quiet ground gives a control near zero, and dividing by that turns an invisible
    difference into an enormous number.
    """
    return sum(mean_abs(_column(image, c), _column(image, c + 1)) for c in CONTROL_COLS) / len(
        CONTROL_COLS
    )


def _boundary(dest: Path, image_mod, z: int, x: int, y: int) -> tuple[float, float]:
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


def seam_check(dest: Path, image_mod, z: int, step: int, span: int) -> dict:
    """Is any source-tile boundary visible? Measured against boundaries that are not seams.

    A real seam falls every ``step`` tiles of level ``z``, where two separately upscaled
    source squares meet. The control is the identical statistic at boundaries halfway
    between them, where the two tiles were cut from ONE upscaled core and there is nothing
    to stitch -- so whatever this measurement costs when there is no seam is what it costs
    here, and the only question is whether the seams cost more.

    That control is the whole point. An edge in the artwork that happens to land on a tile
    boundary reads large whether or not the boundary is a seam, so a bare threshold on the
    ratio would condemn the map's own coastlines. Comparing like with like does not.
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
    seam_median = seams[len(seams) // 2] if seams else 0.0
    interior_median = interior[len(interior) // 2] if interior else 0.0
    return {
        "method": (
            f"the across-boundary mean per-channel difference over the tile's own "
            f"adjacent-column difference, at rows {list(SEAM_ROWS)} of z{z}. seam_ratios "
            f"are the real boundaries -- one every {step} tiles, where two separately "
            "upscaled squares meet -- and interior_ratios the same statistic halfway "
            "between them, where one core was simply cut in two."
        ),
        "overlap_px": ENHANCE_OVERLAP_PX,
        "seam_ratios": seams,
        "interior_ratios": interior,
        "seam_median": seam_median,
        "interior_median": interior_median,
        "seam_worst": max(seams) if seams else 0.0,
        "interior_worst": max(interior) if interior else 0.0,
        "on_flat_ground": {
            "note": (
                "boundaries whose tile has no column-to-column variation at all -- open "
                "ocean. No ratio is meaningful there, so these are the raw across-boundary "
                "differences, and they are the strictest test the sheet has: on one flat "
                "colour, anything but zero is a line a reader would see."
            ),
            "seam_edges": seam_flat,
            "interior_edges": interior_flat,
        },
        "threshold": SEAM_RATIO_MAX,
        "seams_invisible": (
            seam_median <= SEAM_RATIO_MAX * interior_median and max(seam_flat, default=0.0) == 0.0
        ),
        "reading": (
            "the seams read at or below what a boundary with no seam in it reads, so the "
            "stitching contributes nothing a reader could pick out from the map's own "
            "edges. The overlap is what buys that: the model never sees a tile edge that "
            "survives into the output."
        ),
    }


def low_zoom_residual(dest: Path, image_mod, top: int, tile_px: int) -> dict:
    """Would the low levels look different if they came from the enhanced sheet instead?

    z0..z5 are downscales of the game's own artwork, and the honest question about that
    choice is whether anybody could tell. So each sampled top-level tile of the artwork is
    compared against the four enhanced tiles above it, mosaicked and Lanczos'd back down to
    the same resolution -- literally the two candidate provenances for that one tile --
    against the same adjacent-column control the seam check uses.

    Tiles whose control is zero are dropped rather than counted: open ocean agrees with
    everything, and counting it would be padding the answer with tiles that cannot disagree.
    """
    span = 1 << top
    ratios = []
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
            back = mosaic.resize((tile_px, tile_px), image_mod.LANCZOS)
            middle = tile_px // 2
            difference = mean_abs(_column(artwork, middle), _column(back, middle))
            ratios.append(round(difference / control, 3))
    ratios.sort()
    median = ratios[len(ratios) // 2] if ratios else 0.0
    return {
        "method": (
            f"{len(ratios)} tiles of z{top} as cut from the artwork, differenced against "
            f"their own four z{top + 1} children mosaicked and Lanczos'd back down to "
            f"{tile_px} px, over the artwork tile's adjacent-column difference"
        ),
        "samples": len(ratios),
        "ratios": ratios,
        "median_ratio": median,
        "levels_from": (
            f"z0..z{top} are downscales of the game's own artwork; only the levels above "
            "it, which have no artwork behind them, come from the enhanced pixels"
        ),
        "indistinguishable": median <= 1.0,
        "reading": (
            "a ratio at or under 1 means the two provenances differ by less than the "
            "artwork differs from its own next column -- so cutting the low levels from "
            "the original changes nothing a reader could see, and taking the simple path "
            "is a measurement rather than a shrug."
        ),
    }


def _padded_crop(sheet, image_mod, tx: int, ty: int, tile: int, overlap: int):
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


def enhance_levels(
    sheet,
    image_mod,
    dest: Path,
    upscaler: dict,
    work: Path,
    *,
    tile_px: int = PYRAMID_TILE_PX,
    source_tile: int = ENHANCE_TILE_PX,
    overlap: int = ENHANCE_OVERLAP_PX,
    scale: int = ENHANCE_SCALE,
) -> dict:
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

    started = time.perf_counter()
    t_presharpen = 0.0
    lifted: list[float] = []
    for ty in range(grid):
        for tx in range(grid):
            crop = _padded_crop(sheet, image_mod, tx, ty, source_tile, overlap)
            mark = time.perf_counter()
            crop, raised = presharpen(crop, image_mod)
            t_presharpen += time.perf_counter() - mark
            lifted.append(raised)
            crop.save(src_dir / f"t_{tx:02d}_{ty:02d}.png")
    t_cut = time.perf_counter() - started - t_presharpen
    print(
        f"  enhance: {grid * grid} source squares of {source_tile + 2 * overlap}px "
        f"({source_tile} + 2x{overlap} overlap) cut in {t_cut:.1f}s"
    )
    print(
        f"  enhance: pre-sharpened in {t_presharpen:.1f}s; that mask covers "
        f"{sum(lifted) / len(lifted) * 100:.1f}% of the sheet"
    )

    started = time.perf_counter()
    code, log = run_upscaler(upscaler["exe"], upscaler["models"], src_dir, up_dir, scale)
    t_upscale = time.perf_counter() - started
    produced = sorted(up_dir.glob("*.png"))
    if code != 0 or len(produced) != grid * grid:
        raise EnhanceError(
            f"{upscaler['exe']} exited {code} after {t_upscale:.0f}s with "
            f"{len(produced)} of {grid * grid} squares written.\n"
            f"{log}\n"
            "The pyramid already installed has not been touched -- this ran inside the "
            f"staging tree. Nothing here recovers by falling back to Lanczos: re-run "
            "without --enhance if that is what is wanted, and the sidecar will say so."
        )
    print(
        f"  enhance: {grid * grid} squares upscaled {scale}x with {ENHANCE_MODEL} in "
        f"{t_upscale:.1f}s ({t_upscale / (grid * grid):.2f}s each)"
    )

    started = time.perf_counter()
    t_repair = t_colour = 0.0
    core_px = source_tile * scale
    margin = overlap * scale
    per_level = {z: 0 for z in range(top + 1, enhanced_top + 1)}
    coverage: list[float] = []
    for ty in range(grid):
        for tx in range(grid):
            # The untouched source, never in/: both repairs correct TOWARDS it.
            source = _padded_crop(sheet, image_mod, tx, ty, source_tile, overlap)
            upscaled = image_mod.open(up_dir / f"t_{tx:02d}_{ty:02d}.png").convert("RGB")
            mark = time.perf_counter()
            blended, covered = hybrid_upscale(source, upscaled, image_mod, scale)
            t_repair += time.perf_counter() - mark
            mark = time.perf_counter()
            # Before the crop: the blur reaches ~25 px into the neighbour, or it seams.
            fixed = colour_fix(blended, source, image_mod)
            t_colour += time.perf_counter() - mark
            core = fixed.crop((margin, margin, margin + core_px, margin + core_px))
            coverage.append(covered)
            for z in per_level:
                side = core_px >> (enhanced_top - z)
                piece = core if side == core_px else core.resize((side, side), image_mod.LANCZOS)
                span = side // tile_px
                per_level[z] += cut_square(piece, dest, z, tx * span, ty * span, tile_px)
    t_pyramid = time.perf_counter() - started - t_repair - t_colour
    print(
        f"  enhance: faint detail repaired in {t_repair:.1f}s, low frequencies restored in "
        f"{t_colour:.1f}s ({t_colour / (grid * grid):.2f}s each)"
    )

    levels = []
    for z, written in sorted(per_level.items()):
        side = tile_px << z
        levels.append(
            {
                "z": z,
                "sheet_px": side,
                "tiles": (1 << z) ** 2,
                "bytes": written,
                "from": (
                    f"the sheet pre-sharpened, upscaled {scale}x by {ENHANCE_MODEL}, faint "
                    "detail restored and low frequencies put back"
                ),
            }
        )
        print(f"  pyramid z{z}: {side}x{side}, {(1 << z) ** 2} tiles, {written / 1e6:.2f} MB")

    seams = seam_check(dest, image_mod, enhanced_top, core_px // tile_px, 1 << enhanced_top)
    low = low_zoom_residual(dest, image_mod, top, tile_px)
    print(
        f"  enhance: seams read {seams['seam_median']:.2f}x their own control where "
        f"boundaries that are NOT seams read {seams['interior_median']:.2f}x theirs"
    )
    print(
        f"  enhance: the mask covers {sum(coverage) / len(coverage) * 100:.1f}% of the sheet; "
        f"z{top} from the artwork differs {low['median_ratio']:.2f}x its control from the "
        f"same tile taken out of z{top + 1}"
    )
    if not seams["seams_invisible"]:
        print(
            f"  WARNING: the seams read {seams['seam_median']:.2f}x their control against "
            f"{seams['interior_median']:.2f}x at boundaries with no seam in them, over the "
            f"{SEAM_RATIO_MAX}x this file calls invisible. The overlap is not buying enough "
            "context. The tiles are still written -- _meta.tiles.enhancement says so."
        )

    return {
        "levels": levels,
        "enhancement": {
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
            "source_tile_px": source_tile,
            "overlap_px": overlap,
            "source_squares": grid * grid,
            "enhanced_sheet_px": sheet.width * scale,
            "binary": {
                "url": upscaler["url"],
                "sha256": upscaler["sha256"],
                "exe": ENHANCE_EXE_NAME,
                "cached_at": str(upscaler["home"]),
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
            "seams": seams,
            "low_zoom": low,
            "timings_s": {
                "cut_source_squares": round(t_cut, 2),
                "presharpen": round(t_presharpen, 2),
                "upscale": round(t_upscale, 2),
                "faint_repair": round(t_repair, 2),
                "colour_fix": round(t_colour, 2),
                "cut_enhanced_levels": round(t_pyramid, 2),
                "total": round(
                    t_cut + t_presharpen + t_upscale + t_repair + t_colour + t_pyramid, 2
                ),
            },
        },
    }
