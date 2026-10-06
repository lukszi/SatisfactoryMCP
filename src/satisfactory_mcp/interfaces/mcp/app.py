"""The MCP app object, and the state accessors every tool group needs.

Split out so tool modules can register against one ``mcp`` without importing each other.
``server`` imports the tool packages purely for their decorator side effects.

The shared resolvers now live with their domains -- ``domain.factories.select`` and
``domain.spatial.places`` -- and are re-bound here only so the old private names keep
resolving for the tool modules that spell them.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ... import config
from ...core.gameassets import provenance
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...core.schema import NewerSchema
from ...domain import settings
from ...domain.factories.select import resolve_factory as _resolve_factory
from ...domain.planning.solver.scenario import resolve_item
from ...domain.planning.stored.planlog import Actor
from ...domain.session import journal
from ...domain.spatial.places import player_xy as _player_xy
from ...domain.spatial.places import resolve_place as _origin_for
from ...domain.world import pin
from ...domain.world.state import WorldState, load_state

INSTRUCTIONS = (
    "Plans are versioned: read one (list_plans name=) and pass its version as base_rev when "
    "you change it. When the user says 'this', 'here' or 'what I have open', or quotes an "
    "ask: or pin: id, call ui_context first."
)

mcp = FastMCP("satisfactory", instructions=INSTRUCTIONS)

Limit = Annotated[int, Field(default=10, ge=1, le=25, description="max rows (hard cap 25)")]

#: The pin every save-reading tool accepts. The description is resident in 46 tool schemas,
#: so it names the token's shape and nothing else; the contract is docs/mcp-surface.md 10.1i.
AsOf = Annotated[
    str | None,
    Field(default=None, description="pin to one world state: a sav:… token from an earlier answer"),
]

#: The shared ``biomass`` setting (docs/shared-settings.md), overridable per call.
Biomass = Annotated[
    bool | None,
    Field(
        default=None,
        description="count hand-fed biomass burners as generation; omitted: the shared setting",
    ),
]


def shared(key: str) -> tuple:
    """A shared setting's value, and a note when the file could not be read (the default)."""
    try:
        return settings.value(key), ""
    except (NewerSchema, OSError) as exc:
        return settings.SPECS[key].default, f"shared settings unreadable, {key} defaulted: {exc}"


@lru_cache(maxsize=1)
def game() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def _state(
    save: str | None = None, world: str | None = None, as_of: str | None = None
) -> WorldState:
    """The world a tool is asking about, checked against the caller's pin.

    ``as_of`` is a CHECK on whatever ``save``/``world`` resolved to, never a selector of its
    own: it is applied AFTER the save is picked, so the three arguments cannot compete. That
    ordering is the useful one -- ``save=`` names a file and the game rewrites files, so a
    pinned filename goes on resolving happily to a world state the caller has never seen,
    which is the drift ``as_of`` exists to catch. See ``domain/world/pin.py``.
    """
    st = load_state(game(), path=save, world=world)
    pin.check(st.header, as_of)
    return st


def actor(ctx: Context | None) -> Actor:
    """Who is writing: this process, as the client named itself at ``initialize``."""
    try:
        client = ctx.session.client_params.clientInfo.name or ""
    except (AttributeError, ValueError):
        client = ""
    return Actor("chat", client, os.getpid())


def follow(st, ctx: Context | None, tool: str, view: str, params: dict, text: str) -> None:
    """Journal a finder call, so a page that follows chat opens the same World view."""
    try:
        sav = pin.check(st.header, None)
    except Exception:
        sav = ""
    kept = {k: str(v) for k, v in params.items() if v not in (None, "")}
    journal.append(
        st.world_id,
        "world.find",
        actor=actor(ctx),
        sav=sav,
        tool=tool,
        args={"view": view, "params": kept},
        text=text,
    )


def _item_id(query: str) -> str | None:
    return resolve_item(game(), query)


def retired(*renamed: tuple[str, object, str]) -> str | None:
    """The refusal a retired parameter answers with, or ``None`` when none was passed.

    Each triple is ``(old spelling, what the caller passed, new spelling)``. The caller's own
    value is echoed into the rewrite, because the thing that meets this message is a stored
    call or a copied example and the useful answer is the line to write instead.
    """
    for old, value, new in renamed:
        if value is not None:
            return f"! {old}={value!r} is retired -- write {new}={value!r} instead"
    return None


@lru_cache(maxsize=1)
def stale_artifact_notes() -> tuple[str, ...]:
    """Whether the generated tables under ``data/`` still describe the build installed here.

    Cached for the process's life alongside ``game``, and for the same reason: neither the
    install nor a generated table changes under a running server, and this reads six sidecars.

    Not folded into `integrity_notes` next door, which is a pure function of what a caller
    already holds; this one goes to disk. Both are notes and both surface together.
    """
    try:
        return tuple(provenance.stale_artifacts(config.game_root(), config.data_dir()))
    except (FileNotFoundError, OSError):
        # No install to compare against is not drift. `docs_path` raises here on a machine
        # with no game, which is a machine that cannot be told its tables are out of date.
        return ()


#: How many of a channel's warnings are quoted before the rest are counted. Both channels are
#: silent on healthy data, so anything at all is worth reading; past a handful the answer is
#: "this build is not the one this server was written against" and the first few say it.
INTEGRITY_NOTES_SHOWN = 4


def integrity_notes(projection: dict, data: GameData) -> list[str]:
    """What the two normalisation guards found, as notes, or nothing at all.

    Both channels collect drift instead of raising, because one unreadable spline must not
    cost the other 502 pipes and one changed building must not cost the whole docs dump. That
    is only a good trade while somebody is told: unread, they turn a game update into a
    quietly smaller world reported with full confidence. This is where they are told.
    """
    notes = []
    for channel, found in (
        ("this save", list(projection.get("warnings") or [])),
        ("the game's own data", list(data.warnings)),
    ):
        if not found:
            continue
        shown = "; ".join(found[:INTEGRITY_NOTES_SHOWN])
        rest = len(found) - INTEGRITY_NOTES_SHOWN
        notes.append(
            f"{len(found)} problem(s) reading {channel}, so what follows may describe less "
            f"than is really there: {shown}" + (f"; and {rest} more" if rest > 0 else "")
        )
    return notes


#: The domain resolvers under their old private names, for ``server`` and for tests.
_ = (_resolve_factory, _player_xy, _origin_for)
