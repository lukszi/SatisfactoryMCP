"""What a layer's ``meta.json`` says about how it was drawn: the render block and provenance.

A run gathers what every layer says alike once (``RunRecord``); each layer adds its style,
its measurements and its timings (docs/map/renders.md sections 17 and 20).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from mapgen.gamedata.frame import RENDER_PX
from mapgen.lighting.hillshade import SHADE_FLOOR, SHADE_RANGE, SUN_ALTITUDE_DEG, SUN_AZIMUTH_DEG
from mapgen.tiles.rendertext import COMPOSITION_TEXT, Z7_TEXT, sampling_text
from mapgen.tiles.sidecar import build_render_sidecar
from satisfactory_mcp.core.gameassets.provenance import provenance_block
from satisfactory_mcp.core.gameassets.versions import RENDER_RECIPES, STYLES
from satisfactory_mcp.core.jsontypes import JsonObject, JsonValue

__all__ = ["PAINT_INPUTS", "LayerDraw", "RenderFacts", "RunRecord", "layer_sidecar", "render_block"]

#: The provenance inputs only the painted layer is drawn from.
PAINT_INPUTS = frozenset({"paint", "rock_families", "titan_trees"})


@dataclass(frozen=True)
class RenderFacts:
    """``_meta.render`` as every layer of a run says it.

    ``composition`` is the run's half of ``two_regime``; ``water`` carries ``shore`` without
    the style's optics, or None away from the sea.
    """

    size: int
    spacing_m: float
    subsamples: int
    two_regime: bool
    composition: JsonObject
    water: JsonObject
    cut_workers: int
    parallel_check: JsonObject | None
    pillow_version: str


@dataclass(frozen=True)
class RunRecord:
    """Everything a run's layer sidecars say alike, gathered once the run is prepared.

    ``sources`` names every layer's inputs; ``biome_source`` and ``paint_source`` only the
    layers drawn from the biome raster and from the paint store.
    """

    render: RenderFacts
    recipe: int
    kernel_only: bool
    field_meta: JsonObject
    build_raw: JsonObject
    inputs: Mapping[str, JsonObject]
    sources: JsonObject
    biome_source: JsonObject
    paint_source: JsonObject


@dataclass(frozen=True)
class LayerDraw:
    """One layer's own part: its style, the run's measurements, the shore, and its timings."""

    layer: str
    style_id: str
    style_digest: str
    biome: bool
    measured: JsonObject
    shore_optics: JsonValue
    seconds_to_draw: float
    draw_threads: int
    seconds_to_cut: float


def render_block(run: RenderFacts, draw: LayerDraw) -> JsonObject:
    """``_meta.render``: the grid, the two regimes, the light and water rules, the timings."""
    water = dict(run.water)
    shore = water.get("shore")
    if isinstance(shore, dict):
        water["shore"] = {**shore, "optics": draw.shore_optics}
    return {
        "width_px": run.size,
        "height_px": run.size,
        "metres_per_pixel": round(run.spacing_m, 4),
        "sampling": sampling_text(run.spacing_m, run.two_regime),
        "two_regime": {
            "enabled": run.two_regime,
            "subsamples_per_axis": run.subsamples if run.two_regime else None,
            "silhouette_antialiasing": _silhouette_text(run.subsamples),
            "composition": COMPOSITION_TEXT,
            **run.composition,
            **draw.measured,
        },
        "z7": Z7_TEXT if run.size >= RENDER_PX else None,
        "hillshade": (
            f"sun at azimuth {SUN_AZIMUTH_DEG} deg, altitude {SUN_ALTITUDE_DEG} deg, "
            f"shade in [{SHADE_FLOOR}, {SHADE_FLOOR + SHADE_RANGE}], computed at the "
            "output's own spacing"
        ),
        "water": water,
        "seconds_to_draw": round(draw.seconds_to_draw, 1),
        "draw_threads": draw.draw_threads,
        "seconds_to_cut": round(draw.seconds_to_cut, 1),
        "cut_workers": run.cut_workers,
        **({"parallel_cutter_check": run.parallel_check} if run.parallel_check else {}),
        "imaging": {"name": "pillow", "version": run.pillow_version},
    }


def _silhouette_text(subsamples: int) -> str:
    if subsamples == 1:
        return (
            "none: a pixel is rock where a triangle covers its centre, at the "
            "triangle's own height, and ground where none does. Rock heights are "
            "never blurred across a silhouette"
        )
    return f"{subsamples}x{subsamples} sub-samples per output texel, box-folded"


def layer_provenance(run: RunRecord, draw: LayerDraw) -> JsonObject:
    """``_meta.provenance``: the inputs this layer was drawn from, the recipe and the style."""
    painted = draw.layer == "painted"
    inputs: JsonObject = {
        key: value
        for key, value in run.inputs.items()
        if (key != "biome_raster" or draw.biome) and (key not in PAINT_INPUTS or painted)
    }
    recipe_row = RENDER_RECIPES[run.recipe]
    style_row = STYLES[draw.style_id]
    renderer: JsonObject = {
        "family": "render",
        "recipe": run.recipe,
        "version": recipe_row["version"],
        "label": recipe_row["label"],
        "sampler": "catmull-rom" if run.kernel_only else recipe_row["sampler"],
        "two_regime": run.render.two_regime,
        "size_px": run.render.size,
        "subsamples": run.render.subsamples,
    }
    style: JsonObject = {
        "id": draw.style_id,
        "version": style_row["version"],
        "label": style_row["label"],
        "digest": draw.style_digest,
        "tone": style_row["tone"],
    }
    return provenance_block(run.build_raw, inputs, renderer, style)


def layer_sidecar(
    run: RunRecord, draw: LayerDraw, tiles: JsonObject, tiles_2x: JsonObject
) -> JsonObject:
    """The layer's whole ``meta.json``: ``build_render_sidecar`` with this run's blocks."""
    return build_render_sidecar(
        layer=draw.layer,
        recipe=run.recipe,
        field_meta=run.field_meta,
        tiles=tiles,
        tiles_2x=tiles_2x,
        render=render_block(run.render, draw),
        provenance=layer_provenance(run, draw),
        extra={
            **run.sources,
            **(run.biome_source if draw.biome else {}),
            **(run.paint_source if draw.layer == "painted" else {}),
        },
    )
