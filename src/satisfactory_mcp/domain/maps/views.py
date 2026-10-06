"""The wire shapes this package builds: a map's freshness, a job's options, cost and checks.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Literal, NotRequired

from typing_extensions import TypedDict

__all__ = [
    "Layer",
    "MapCanGenerate",
    "MapEstimateResponse",
    "MapFreshness",
    "MapJobOptions",
    "MapRerender",
    "MapStaleAxis",
]


Layer = Literal["terrain", "satellite", "painted", "relief", "relief-dark"]


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
    recipe: NotRequired[Literal["current", "kernel-only"]]
    top: NotRequired[bool]
    keep_cache: NotRequired[bool]
    restyle: NotRequired[bool]
    light: NotRequired[bool]
    enhance: NotRequired[bool]
    tiles_2x: NotRequired[bool]
    titan_trees: NotRequired[bool]


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
