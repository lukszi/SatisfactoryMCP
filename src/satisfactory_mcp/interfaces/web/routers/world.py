"""The two endpoints a page opens with: which worlds exist, and what one of them is.

``/api/worlds`` is the only route on the whole surface that does not read through the
injected loader -- it scans the save directory itself, because the picker's job is to say
what is there before anything has been chosen.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.saveio import projection as proj
from ....domain.world import pin
from ..serial import Biomass, _fail, _state, _xyz

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
        return _fail(f"could not scan saves: {exc}", 404)
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


class PlayerPosition(TypedDict):
    """Where the player last stood, or three nulls -- never a missing branch.

    A save with no pawn, as a dedicated-server world has, sends three nulls rather than
    dropping the key: the page branches on ``x_m === null`` to decide whether there is a
    you-are-here to draw at all, and all three go null together.
    """

    x_m: float | None
    y_m: float | None
    z_m: float | None


class GeneratorTotal(TypedDict):
    """One generator class, counted and summed. A value of ``PowerSummary.by_generator``."""

    name: str
    count: int
    mw: float


class PowerSummary(TypedDict):
    """The scalar fields of ``WorldState.power_report()`` the page can use.

    Declaration order is the order ``domain/power/report.py`` returns them in. The domain
    also returns the starved-generator list, which rule 3 drops here: it is a text-surface
    answer and the page has no place for it.

    ``utilisation`` is never null: it is ``measured / draw``, and ``1.0`` when nothing draws
    at all -- a factory with nothing built is fully utilised in the only sense the ratio has.
    """

    generation_mw: float
    draw_mw: float
    headroom_mw: float
    measured_draw_mw: float
    measured_headroom_mw: float
    monitored: int
    unmonitored: int
    utilisation: float
    by_generator: dict[str, GeneratorTotal]
    #: Generator classes Docs carries no entry for. Sorted, and empty on a world that has
    #: none, which is a measurement rather than a gap.
    unmodellable: list[str]
    paused_count: int
    #: What ``?biomass=exclude`` left out: wired, unpaused biomass burners and their MW.
    biomass_generators: int
    biomass_mw: float


class ProgressionSummary(TypedDict):
    """``WorldState.progression()`` verbatim, on the same terms as ``PowerSummary``.

    ``game_phase`` and ``target_phase`` are ``null`` on the pre-1.0 saves that carry no
    phase at all; ``highest_complete_tier`` is ``null`` when not one tier is finished, which
    is different from tier 0 and there is no tier 0.

    ``milestones_by_tier`` is keyed by the tier NUMBER, which JSON spells as a string, and is
    left an open map: the tiers are the game's, and a game update adds one.
    """

    game_phase: str | None
    target_phase: str | None
    phase_costs_remaining: dict[str, dict[str, int]]
    milestones_by_tier: dict[int, str]
    highest_complete_tier: int | None
    purchased_schematics: int
    available_recipes: int


class SummaryResponse(TypedDict):
    """What ``/api/summary`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``header`` is the save header the sidecar read, forwarded whole and typed as the open map
    it is: the key set is the SIDECAR's contract, and spelling it out here would delete any
    fourteenth key the parser learns to read. ``power`` and ``progression`` are the opposite
    case and are spelled out in full, because each is a literal ``return {...}`` in the
    domain with a fixed key set.
    """

    header: dict[str, Any]
    #: This world state's token, the same one the MCP tools print and take back as ``as_of=``.
    #: On the wire beside ``age_note`` -- which already contains it -- so a client reads the
    #: identity as a field rather than out of a sentence.
    save_token: str
    age_note: str
    power: PowerSummary
    progression: ProgressionSummary
    player: PlayerPosition


@router.get("/summary", response_model=SummaryResponse)
def summary(
    request: Request,
    save: str | None = None,
    world: str | None = None,
    biomass: Biomass = "exclude",
) -> Any:
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    return {
        "header": st.header,
        # Recorded as well as sent: a token the page shows and the assistant is then handed
        # has to be one the ledger recognises, or the pin refusal cannot tell "yours is
        # stale" from "you invented it". See domain/world/pin.py.
        "save_token": pin.remember(st.header),
        "age_note": st.age_note,
        "power": st.power_report(biomass=biomass == "include"),
        "progression": st.progression(),
        "player": _xyz(st.player_position()),
    }
