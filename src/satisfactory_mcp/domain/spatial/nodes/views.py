"""The shapes the node finder builds: its filter choices, a shipped table's age, the open water
beside the nodes, and one ranked site.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict

__all__ = ["NodeChoices", "ResourceChoice", "SiteRow", "TableAge", "WaterSummary"]


class TableAge(TypedDict):
    """Whether a shipped map table is older than the save; built by the domain's ``table_age``.

    ``moved`` and ``unjoinable`` count rows in the reply they travel with (nodes only);
    ``observed_from``/``observed_matches`` are the collectible table's (null for nodes).
    """

    table: Literal["nodes", "collectibles"]
    behind: bool
    gap: str | None
    moved: int
    unjoinable: int
    observed_from: str | None
    observed_matches: bool | None
    notes: list[str]


class ResourceChoice(TypedDict):
    id: str
    name: str
    nodes: int


class NodeChoices(TypedDict):
    resources: list[ResourceChoice]
    purities: list[str]
    kinds: list[str]


class WaterSummary(TypedDict):
    """The open water a save draws from: pumps per water body, and sea level off the pumps.

    ``per_pump_m3_min`` is null when the game data has no Water Extractor; the sea level and
    its span are null when no pump has a position.
    """

    bodies: dict[str, int]
    pumps: int
    per_pump_m3_min: float | None
    sea_level_m: float | None
    sea_level_span_m: float | None


class SiteRow(TypedDict):
    """One ranked site as the tool and the page both list it; ``x``/``y`` in centimetres.

    The terrain columns are null without a terrain field, ``to_infra_m`` with nothing built
    and ``alt_m`` with no consumer to measure from.
    """

    score: float
    region: str | None
    grid: str
    x: float
    y: float
    selector: str
    nodes: int
    untapped: float
    spread_m: float
    to_infra_m: float | None
    purity: float
    alt_m: float | None
    rough_m: float | None
    slope_deg: float | None
    wet_pct: float | None
