"""Whether a machine set is fed from a real source, walked upstream by ``trace``.

A source is an extractor inside the set, or an extractor or a production machine that the
set's belts and pipes reach upstream outside it. Storage is walked through, so a box counts
only when something real feeds it. docs/frontend_vision.md §9.5 has what the walk cannot see.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from .model import kind_of
from .trace import trace

__all__ = ["FED", "NOT_FED", "TRANSPORT", "VERDICTS", "feeding"]

FED = "fed"
NOT_FED = "not fed"
TRANSPORT = "transport"
VERDICTS = (FED, NOT_FED, TRANSPORT)


def _transport(actor: str) -> bool:
    cls = actor.rpartition("_")[0] if actor.rpartition("_")[2].isdigit() else actor
    return kind_of(cls) == "transport" or "DockingStation" in cls


def feeding(state, game: GameData, machines: list[str]) -> str:
    """``FED``, ``NOT_FED``, or ``TRANSPORT`` when the walk ends at a station it cannot see past."""
    for m in machines:
        building = game.buildings.get(state.graph.cls.get(m, ""))
        if building is not None and building.is_extractor:
            return FED
    walk = trace(state, game, list(machines), "up")
    inside = set(machines)
    if any(
        r.kind in ("extractor", "production") and r.instance not in inside for r in walk.reached
    ):
        return FED
    if any(_transport(node) for node in walk.nodes):
        return TRANSPORT
    return NOT_FED
