"""The shapes this package builds: the manifest and its entries, a job's options and plan, a
map's freshness, cost and checks.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypeAlias

from typing_extensions import TypedDict

from ...core.jsontypes import JsonObject, JsonValue

__all__ = [
    "ArtworkOptions",
    "GeneratorPlan",
    "InputOptions",
    "JobOptions",
    "Layer",
    "MapAxes",
    "MapCanGenerate",
    "MapEntry",
    "MapEstimateResponse",
    "MapFreshness",
    "MapInputNow",
    "MapInputsState",
    "MapJobOptions",
    "MapRegistryDoc",
    "MapRerender",
    "MapStaleAxis",
    "MapViewRow",
    "MapsView",
    "PyramidStats",
    "RenderOptions",
    "RenderRecipe",
]


Layer = Literal["terrain", "satellite", "painted", "relief", "relief-dark"]
RenderRecipe = Literal["current", "kernel-only"]

#: A map's provenance axes as its sidecar states them (docs/maps_contract.md §3): JSON, read
#: through ``axes.dict_at``.
MapAxes: TypeAlias = JsonObject


class MapStaleAxis(TypedDict):
    axis: str
    text: str


class MapRerender(TypedDict):
    """A newer renderer this map could be drawn with; ``needs`` are inputs to rebuild first."""

    recipe: int
    label: str
    needs: list[str]
    text: str


class MapFreshness(TypedDict):
    """``stale`` is outdated DATA (amber); ``rerender`` and ``restyle`` are offers (neutral)."""

    stale: list[MapStaleAxis]
    rerender: MapRerender | None
    restyle: bool
    incomplete: bool


class MapCanGenerate(TypedDict):
    gen: bool
    tools: bool
    game: bool
    heightfield: bool
    vulkan: bool
    ok: bool
    reason: str | None


class MapJobOptions(TypedDict):
    layers: NotRequired[list[Layer]]
    size: NotRequired[int]
    recipe: NotRequired[RenderRecipe]
    top: NotRequired[bool]
    keep_cache: NotRequired[bool]
    restyle: NotRequired[bool]
    light: NotRequired[bool]
    enhance: NotRequired[bool]
    tiles_2x: NotRequired[bool]
    titan_trees: NotRequired[bool]


class RenderOptions(TypedDict):
    """A render job's options, each one checked and defaulted."""

    layers: list[Layer]
    size: int
    recipe: RenderRecipe
    top: bool
    keep_cache: bool
    restyle: bool
    light: bool
    titan_trees: bool


class ArtworkOptions(TypedDict):
    enhance: bool
    tiles_2x: bool


class InputOptions(TypedDict):
    """The input presets take no options."""


JobOptions: TypeAlias = RenderOptions | ArtworkOptions | InputOptions


class MapEstimateResponse(TypedDict):
    """What a job would cost: wall time, disk kept, disk needed while it runs."""

    seconds: int
    keep_bytes: int
    transient_bytes: int
    free_bytes: int
    needs_bytes: int
    ok: bool
    reason: str | None
    measured: bool


class PyramidStats(TypedDict):
    """What a pyramid's sidecar says: axes, bytes on disk, depth of both trees, when written."""

    axes: MapAxes
    bytes: int
    max_z: int | None
    max_2x_z: int | None
    created: float | None


class MapEntry(TypedDict):
    """One type in the manifest (docs/maps_contract.md §2)."""

    label: str | None
    kind: str
    layer: str
    generator: str
    dir: str
    sidecar: str
    axes: MapAxes
    bytes: int
    max_z: int | None
    max_2x_z: int | None
    created: float | None
    status: str
    origin: str
    job: str | None
    replaces: str | None
    in_switcher: bool
    size_px: NotRequired[int]


class MapRegistryDoc(TypedDict):
    """``data/local/maps/manifest.json``. A history row is what a finished job left."""

    schema: int
    version: int
    default: str | None
    types: dict[str, MapEntry]
    history: list[JsonValue]


class MapInputNow(TypedDict):
    """An input directory's sidecar as it reads now."""

    version: int | None
    cl: int | None
    digest: str | None
    planes: list[str]
    transcribed: JsonValue


class MapInputsState(TypedDict):
    """What every input is now: the installed build, each input directory, each reader."""

    game_cl: int | None
    inputs: dict[str, MapInputNow | None]
    readers: dict[str, int]


class MapViewRow(TypedDict):
    """One type as the registry's view computes it: live axes, status, freshness, names."""

    id: str
    entry: MapEntry
    axes: MapAxes
    status: str
    freshness: MapFreshness
    name: str
    title: str


class MapsView(TypedDict):
    version: int
    default: str | None
    types: list[MapViewRow]
    history: list[JsonValue]
    current: MapInputsState


class GeneratorPlan(TypedDict):
    """One job's command: ``produces`` maps the new type ids to their entries."""

    script: str
    command: str
    argv: list[str]
    produces: dict[str, MapEntry]
    options: JobOptions
