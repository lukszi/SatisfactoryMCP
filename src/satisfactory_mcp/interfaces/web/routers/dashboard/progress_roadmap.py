"""``/api/progress/{gate,alternates}``: the way to the target Space Elevator phase.

The gate sizes each owed part's chain and ranks the next steps; alternates prices each
alternate against the bill's chains. Both are planner solves, kept per save and horizon.
docs/progress_roadmap.md specifies them. Handler names are operation_ids (wire rule 1 of
docs/web-wire.md).
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Annotated

from fastapi import APIRouter, Query, Request

from .....core.singleflight import Singleflight
from .....domain.progression.roadmap import alternates_view, gate_view
from .....domain.progression.roadmap.views import AlternatesView, GateView
from .....domain.world.state import WorldState
from ...serial import require_world

__all__ = ["router"]

router = APIRouter(prefix="/api")

#: Hours a delivery is planned over when the page asks for none.
DEFAULT_HORIZON_H = 20.0

Horizon = Annotated[float, Query(gt=0, le=1000)]

#: (projection, world, labels version, horizon) -> the projection and the answer. The entry
#: holds the projection, so its id is not reused while cached; a reread save is solved again,
#: and a renamed factory changes who makes what.
_GATES: Singleflight[Hashable, tuple[object, GateView]] = Singleflight(maxsize=8)
_ALTERNATES: Singleflight[Hashable, tuple[object, AlternatesView]] = Singleflight(maxsize=8)


def _key(st: WorldState, horizon_h: float) -> Hashable:
    return (id(st.projection), st.world_id, st.labels.version, horizon_h)


@router.get("/progress/gate", response_model=GateView)
def progress_gate(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    horizon_h: Horizon = DEFAULT_HORIZON_H,
) -> GateView:
    """Each part the target phase bills: held, short, who makes it, its locked recipe's
    unlock, its inputs and its chain at the rate that delivers it in ``horizon_h`` hours;
    then at most five next steps."""
    st = require_world(request, save, world)
    return _GATES.get(_key(st, horizon_h), lambda: (st.projection, gate_view(st, horizon_h)))[1]


@router.get("/progress/alternates", response_model=AlternatesView)
def progress_alternates(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    horizon_h: Horizon = DEFAULT_HORIZON_H,
) -> AlternatesView:
    """Every alternate for an item of the bill's chains that cuts machines, power or raw,
    with what it changes and where it comes from: unlocked, a drive on hand, or neither."""
    st = require_world(request, save, world)
    answer = _ALTERNATES.get(
        _key(st, horizon_h), lambda: (st.projection, alternates_view(st, horizon_h))
    )
    return answer[1]
