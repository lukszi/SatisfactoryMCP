"""The wire shapes the node finder builds: its filter choices, and a shipped table's age.

Wire rules, and why these are ``typing_extensions`` TypedDicts: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict

__all__ = ["NodeChoices", "ResourceChoice", "TableAge"]


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
