"""The wire shapes the track view builds: ``TrackResponse`` and ``FeedersResponse``.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

from ...factories.views import StateCount

__all__ = [
    "Feeder",
    "FeedersResponse",
    "TrackBuiltAt",
    "TrackBuiltCandidate",
    "TrackCost",
    "TrackMachine",
    "TrackNeighbour",
    "TrackPower",
    "TrackResponse",
    "TrackRow",
    "TrackSite",
    "TrackSiteRow",
    "TrackStage",
    "TrackStageRow",
    "TrackStartup",
    "TrackTarget",
]


class TrackMachine(TypedDict):
    instance: str
    x_m: float | None
    y_m: float | None


class TrackTarget(TypedDict):
    node: str
    x_m: float | None
    y_m: float | None
    m: float | None


class TrackRow(TypedDict):
    """One build job. ``verb`` is ok, unpause, setrecipe or build; ``build_max`` and ``have_min``
    are null when the count is exact; ``running`` is null when no matched machine is monitored."""

    id: str
    kind: str
    step: int
    stages: list[int]
    process: str
    building: str
    recipe_id: str | None
    item: str | None
    need: int
    have: int
    have_min: int | None
    build: int
    build_max: int | None
    verb: str
    count: int
    reuse: int
    running: int | None
    states: list[StateCount]
    new_building: bool
    note: str
    delta_mw: float
    act: list[TrackMachine]
    targets: list[TrackTarget]
    bbox_m: list[float] | None
    selectors: str


class TrackStageRow(TypedDict):
    row: str
    label: str
    building: str
    machines: int
    total: int
    built: int
    built_max: int
    running: int | None
    states: list[StateCount]
    draw_mw: float
    generation_mw: float
    to_build: int


class TrackStage(TypedDict):
    """One startup wave matched against the save; ``state`` is the server's phrase for it."""

    index: int
    machines: int
    built: int
    built_max: int
    running: int | None
    dark: int
    complete: bool
    state: str
    draw_mw: float
    generation_mw: float
    available_before: float
    available_after: float
    fill_s: float
    waits_for_fill: bool
    states: list[StateCount]
    rows: list[TrackStageRow]
    bbox_m: list[float] | None


class TrackStartup(TypedDict):
    ok: bool
    headroom_mw: float
    headroom_source: str
    plant_draw_mw: float
    plant_generation_mw: float
    minimum_slice_mw: float
    warnings: list[str]


class TrackPower(TypedDict):
    generation_mw: float
    draw_mw: float
    headroom_mw: float
    measured_headroom_mw: float
    biomass: bool


class TrackCost(TypedDict):
    item: str
    name: str
    need: float
    stock: float
    short: float
    lines: int


class TrackNeighbour(TypedDict):
    label: str
    count: int


class TrackSiteRow(TypedDict):
    name: str
    planned: int
    standing: int


class TrackSite(TypedDict):
    text: str
    planned_total: int
    standing_total: int
    rows: list[TrackSiteRow]


class TrackBuiltCandidate(TypedDict):
    """One owner of matching machines at the plan's site: a named factory or an unnamed
    cluster (``proposal`` is its index in this save only)."""

    kind: str
    name: str
    proposal: int | None
    machines: int
    rate_share: float
    bbox_m: list[float] | None


class TrackBuiltAt(TypedDict):
    """Where the plan's built machines were found and the progress figure
    (docs/planner-p4_contract.md §5.2, ``built_at``). ``built`` is null when the plan has no
    site; ``built_max`` differs from ``built`` only when the finding is unsure."""

    mode: str
    confidence: str
    text: str
    figure: str
    hint: str
    fallback: str
    area: str
    picked: str
    built: int | None
    built_max: int | None
    total: int
    percent: float | None
    percent_max: float | None
    candidates: list[TrackBuiltCandidate]
    missing: list[str]
    also_here: list[str]
    foreign: list[str]
    node_owner: str
    labels_version: int
    token: str


class TrackResponse(TypedDict):
    """One plan version's diff and startup stages against this save, from one solve.

    Not feasible is a 200 with ``feasible: false`` and empty lists; a count-as-built factory
    with no machines left is a 200 with ``scope_error`` and empty lists."""

    key: str
    rev: int
    name: str
    feasible: bool
    empty: bool
    headline: str
    cause: str
    save_id: str
    age_note: str
    written_ago: str | None
    plan_id: str
    scope: str
    scope_note: str
    scope_error: str
    drift_note: str
    headroom_mw: float | None
    current: int
    count: int
    partition_id: str
    stage_text: str
    to_build: int
    to_build_max: int
    actionable: int
    unpause: int
    setrecipe: int
    rows: list[TrackRow]
    stages: list[TrackStage]
    startup: TrackStartup
    power: TrackPower
    cost: list[TrackCost]
    neighbours: list[TrackNeighbour]
    site: TrackSite | None
    notes: list[str]
    caveats: list[str]
    monitored: int
    built_at: TrackBuiltAt


class Feeder(TypedDict):
    name: str
    instance: str
    x_m: float | None
    y_m: float | None
    mw: float
    region: str


class FeedersResponse(TypedDict):
    feeders: list[Feeder]
    total_mw: float
    text: str
