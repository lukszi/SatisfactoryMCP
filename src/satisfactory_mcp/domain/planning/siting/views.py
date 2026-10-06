"""The wire shapes of a site preview: one candidate pad and everything it would meet.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing_extensions import TypedDict

__all__ = [
    "SiteBuilt",
    "SiteCandidate",
    "SiteFit",
    "SiteLoss",
    "SitePreviewNode",
    "SitePreviewResponse",
    "SiteTerrain",
    "SiteTrunk",
    "SiteValue",
]


class SitePreviewNode(TypedDict):
    instance: str
    resource: str
    x_m: float
    y_m: float


class SiteTrunk(TypedDict):
    """One trunk: ``run_m`` node to node and ``to_site_m`` the leg to the pad, straight lines;
    ``lift_m`` and ``pumps`` are null without a ground height."""

    name: str
    carrier: str
    members: int
    run_m: float
    to_site_m: float
    lift_m: float | None
    pumps: int | None


class SiteTerrain(TypedDict):
    z_min_m: float | None
    z_median_m: float | None
    z_max_m: float | None
    slope_mean_deg: float | None
    slope_p90_deg: float | None
    roughness_m: float | None
    submerged_pct: float
    nodata_pct: float
    stride: int
    water_m: float | None
    water_below_m: float | None
    cave_pct: float


class SiteCandidate(TypedDict):
    name: str
    kind: str
    machines: int
    bbox_m: list[float] | None


class SiteBuilt(TypedDict):
    """What counts as built for the plan with its pad here: ``mode`` is auto, picked, world or
    none, empty when the plan does not solve."""

    mode: str
    confidence: str
    figure: str
    where: str
    hint: str
    area: str
    built: int | None
    total: int
    current: int
    count: int
    stage_text: str
    candidates: list[SiteCandidate]


class SiteLoss(TypedDict):
    now: int
    here: int
    total: int
    text: str


class SiteValue(TypedDict):
    """A ``site`` op value, ready to push."""

    schema: int
    origin_m: list[float | None]
    yaw_deg: float
    footprint_m: list[float]
    footprint_source: str
    origin_label: str
    when: str


class SiteFit(TypedDict):
    name: str
    machines: int
    value: SiteValue


class SitePreviewResponse(TypedDict):
    """One candidate pad. ``now`` is the stored site's figure; ``nodes`` and ``content_bbox_m``
    come only with ``first=1``. Outside the map square only ``where`` is filled."""

    key: str
    rev: int
    save_id: str
    token: str
    x_m: float
    y_m: float
    yaw_deg: float
    w_m: float
    d_m: float
    source: str
    sited: bool
    in_map: bool
    in_content: bool
    region: str
    z_m: float | None
    z_note: str
    terrain: SiteTerrain | None
    terrain_note: str
    slabs: list[str]
    on_pad: int
    planned: int
    trunks: list[SiteTrunk]
    placeless: list[str]
    built: SiteBuilt
    now: SiteBuilt
    basis: str
    loses: SiteLoss | None
    fits: list[SiteFit]
    overlaps: list[str]
    nodes: list[SitePreviewNode] | None
    content_bbox_m: list[float] | None
    failure: str
