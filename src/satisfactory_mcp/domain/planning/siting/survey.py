"""What stands on a sited pad, counted by building class against the plan's bill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData
from .record import Siting

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState


@dataclass(frozen=True)
class SurveyRow:
    """One building class: how many the plan wants vs how many stand on the site."""

    cls: str
    name: str
    planned: int
    standing: int


@dataclass(frozen=True)
class SiteSurvey:
    """Counts by building class inside the sited footprint, against the plan's bill.

    Deliberately APPROXIMATE, and named so wherever it prints: a machine is counted by
    its class alone -- not its recipe, not its clock, not whether it is wired to anything.
    The identity-matched truth is ``build_diff``'s job; this answers the narrower question
    a siting makes askable at all: "is what stands on THIS pad the right shape".
    """

    rows: list[SurveyRow]
    planned_total: int
    standing_total: int


def survey(game: GameData, st: WorldState, sit: Siting, processes: list[dict]) -> SiteSurvey | None:
    """Count what stands inside the footprint, per building class, against the plan.

    ``None`` when the siting has no footprint: an origin alone marks a spot but bounds
    nothing, and a survey over an unbounded area would just be the whole save again.
    """
    if not sit.has_footprint:
        return None
    planned: dict[str, int] = {}
    for p in processes:
        cls = p.get("building_id") or ""
        if cls:
            planned[cls] = planned.get(cls, 0) + int(p.get("machines") or 0)
    standing: dict[str, int] = {}
    # The same records factory_map and describe_location read: machines, extractors and
    # generators, each with its save position. Belts and foundations are not in the
    # projection's census and are deliberately out of scope here.
    for record in st._all_records():
        pos = record.get("pos")
        if pos and sit.contains_cm(pos[0], pos[1]):
            cls = record.get("cls") or ""
            standing[cls] = standing.get(cls, 0) + 1
    classes = sorted(
        set(planned) | set(standing),
        key=lambda c: (-planned.get(c, 0), -standing.get(c, 0), c),
    )
    rows = [
        SurveyRow(
            cls=c,
            name=game.buildings[c].name if c in game.buildings else c,
            planned=planned.get(c, 0),
            standing=standing.get(c, 0),
        )
        for c in classes
    ]
    return SiteSurvey(
        rows=rows,
        planned_total=sum(planned.values()),
        standing_total=sum(standing.values()),
    )
