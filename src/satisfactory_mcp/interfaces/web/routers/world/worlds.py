"""The two endpoints a page opens with: which worlds exist, and what one of them is.

``/api/worlds`` is the only route on the whole surface that does not read through the
injected loader -- it scans the save directory itself, because the picker's job is to say
what is there before anything has been chosen.

Handler names are operation_ids; wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Request
from typing_extensions import TypedDict

from .....core.saveio import projection as proj
from .....core.saveio.schema import SaveHeader
from .....domain.power.views import PowerReport
from .....domain.progression.views import ProgressionSummary
from .....domain.world import pin
from ...serial import Biomass, PlayerPosition, error_response, require_world, xyz_m

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- worlds


class SaveRow(TypedDict):
    """One save file, cut to the five keys the picker reads -- of the header's thirteen.

    The handler forwards the sidecar's save headers whole and this model is what trims
    them, so the eight it does not declare are DELETED from every row on the wire:
    ``save_identifier`` (already spent server-side, grouping the rows -- ``world_id``
    carries it), ``save_header_version``, ``save_version``, ``build_version``,
    ``save_datetime_ticks``, ``is_modded``, ``is_creative`` and ``size``. A client that
    wants a save's full header asks ``/api/summary``, which forwards it whole.

    ``path`` is the pin -- ``?save=`` takes it back verbatim -- ``filename`` is how the pin
    is spelled in the URL fragment, and ``mtime_ns`` orders the dropdown.
    """

    path: str
    filename: str
    session_name: str
    play_duration_s: int
    mtime_ns: int


class WorldRow(TypedDict):
    """One world: ``asdict(World)``, plus the newest save's headline figures hoisted on.

    ``mtime`` is the newest save's ``mtime_ns`` in SECONDS, and is the one place this
    surface speaks epoch seconds: it is what the server's "newest first" sorted by.
    ``play_duration_s`` is the maximum across the world's saves.
    """

    world_id: str
    session_name: str
    saves: list[SaveRow]
    mtime: float
    newest_filename: str
    play_duration_s: int


class UnsupportedFile(TypedDict):
    """A file the scan could not read: which one, and the parser's own reason.

    The sidecar says five things about such a file; ``path``, ``mtime_ns`` and ``size`` are
    filtered off the wire on the same terms as the save rows' eight.
    """

    filename: str
    reason: str


class WorldsResponse(TypedDict):
    """What ``/api/worlds`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    Both keys are always present together: the only reply without them is the error
    branch, which returns a ``JSONResponse`` and skips this model entirely.
    """

    worlds: list[WorldRow]
    unsupported: list[UnsupportedFile]


@router.get("/worlds", response_model=WorldsResponse)
def worlds() -> Any:
    """Every world the save directory holds, newest first."""
    try:
        found, unsupported = proj.list_worlds()
    except Exception as exc:
        return error_response(f"could not scan saves: {exc}", 404)
    rows = []
    for w in found:
        newest = w.newest
        rows.append(
            {
                **asdict(w),
                "mtime": newest.get("mtime_ns", 0) / 1e9,
                "newest_filename": newest.get("filename"),
                "play_duration_s": w.max_play_duration_s,
            }
        )
    return {"worlds": rows, "unsupported": list(unsupported)}


# -------------------------------------------------------------------- summary


class SummaryResponse(TypedDict):
    """What ``/api/summary`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``header`` is the save header the sidecar read, whole: its key set is the sidecar's
    contract, ``SaveHeader`` in ``core/saveio/schema.py``, so a key the parser learns to read
    is added there and reaches this reply. ``power`` and ``progression`` are the domain's
    answers verbatim.
    """

    header: SaveHeader
    #: This world state's token, the same one the MCP tools print and take back as ``as_of=``.
    #: On the wire beside ``age_note`` -- which already contains it -- so a client reads the
    #: identity as a field rather than out of a sentence.
    save_token: str
    age_note: str
    power: PowerReport
    progression: ProgressionSummary
    player: PlayerPosition


@router.get("/summary", response_model=SummaryResponse)
def summary(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    biomass: Biomass = "exclude",
) -> Any:
    st = require_world(request, save, world)
    return {
        "header": st.header,
        # Recorded as well as sent: a token the page shows and the assistant is then handed
        # has to be one the ledger recognises, or the pin refusal cannot tell "yours is
        # stale" from "you invented it". See domain/world/pin.py.
        "save_token": pin.remember(st.header),
        "age_note": st.age_note,
        "power": st.power_report(biomass=biomass == "include"),
        "progression": st.progression(),
        "player": xyz_m(st.player_position()),
    }
