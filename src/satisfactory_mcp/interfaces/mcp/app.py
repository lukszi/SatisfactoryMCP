"""The MCP app object, and the state accessors every tool group needs.

Split out so tool modules can register against one ``mcp`` without importing each other.
``server`` imports the tool packages purely for their decorator side effects.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from functools import lru_cache

from mcp.server.fastmcp import Context, FastMCP

from ... import config
from ...core.gameassets import provenance
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...core.gamedata.search import resolve_item
from ...core.schema import NewerSchema
from ...domain import settings
from ...domain.factories.select import SelectorError
from ...domain.planning.stored.planlog import Actor
from ...domain.session import journal
from ...domain.world import pin
from ...domain.world.state import WorldState, load_state
from ...presenters.text import primitives as render

INSTRUCTIONS = (
    "Plans are versioned: read one (list_plans name=) and pass its version as base_rev when "
    "you change it. When the user says 'this', 'here' or 'what I have open', or quotes an "
    "ask: or pin: id, call ui_context first."
)

mcp = FastMCP("satisfactory", instructions=INSTRUCTIONS)


class Refusal(Exception):
    """A tool's answer that ends the call early; ``tool`` returns its text verbatim."""


def tool(**kwargs) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a text tool on ``mcp``, answering a ``Refusal`` or ``SelectorError`` as text.

    ``structured_output=False`` because a ``-> str`` tool otherwise echoes its whole payload
    into ``structuredContent`` (docs/mcp-surface.md §10).
    """

    def register(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def answer(*args, **kw) -> str:
            try:
                return fn(*args, **kw)
            except Refusal as exc:
                return str(exc)
            except SelectorError as exc:
                return f"! {exc}"

        mcp.tool(structured_output=False, **kwargs)(answer)
        return answer

    return register


def shared_setting(key: str) -> tuple[object, str]:
    """A shared setting's value, and a note when the file could not be read (the default)."""
    try:
        return settings.value(key), ""
    except (NewerSchema, OSError) as exc:
        return settings.SPECS[key].default, f"shared settings unreadable, {key} defaulted: {exc}"


def biomass_setting(biomass: bool | None) -> tuple[bool, str]:
    """``biomass`` as the caller gave it, else the shared setting and its unread note."""
    if biomass is not None:
        return biomass, ""
    return shared_setting("biomass")


@lru_cache(maxsize=1)
def game() -> GameData:
    """Normalized game data. ~90 ms cold, so built once in-process, no disk cache."""
    return normalize(load_docs(config.docs_path()))


def load_world(
    save: str | None = None,
    world: str | None = None,
    as_of: str | None = None,
    *,
    purpose: str = "",
) -> WorldState:
    """The world a tool asks about, checked against the caller's pin; else a ``Refusal``.

    ``as_of`` checks whatever ``save``/``world`` resolved to and never selects a save of its
    own (``domain/world/pin.py``). ``purpose`` ends the refusal with why the save was needed.
    """
    try:
        st = load_state(game(), path=save, world=world)
        pin.check(st.header, as_of)
    except Exception as exc:
        raise Refusal(f"could not read save: {exc}{purpose}") from exc
    return st


def load_world_or_none(
    save: str | None = None, world: str | None = None, as_of: str | None = None
) -> tuple[WorldState | None, str]:
    """``load_world`` for a tool that answers without a save: the world, or None and why."""
    try:
        return load_world(save, world, as_of), ""
    except Refusal as exc:
        return None, str(exc.__cause__ if exc.__cause__ is not None else exc)


def save_token(st) -> str:
    """The token naming ``st``'s world state, or '' when it carries none."""
    try:
        return st.token
    except Exception:
        return ""


def actor(ctx: Context | None) -> Actor:
    """Who is writing: this process, as the client named itself at ``initialize``."""
    try:
        client = ctx.session.client_params.clientInfo.name or ""
    except (AttributeError, ValueError):
        client = ""
    return Actor("chat", client, os.getpid())


def journal_world_find(
    st, ctx: Context | None, tool: str, view: str, params: dict, text: str
) -> None:
    """Journal a finder call, so a page that follows chat opens the same World view."""
    kept = {k: str(v) for k, v in params.items() if v not in (None, "")}
    journal.append(
        st.world_id,
        "world.find",
        actor=actor(ctx),
        sav=save_token(st),
        tool=tool,
        args={"view": view, "params": kept},
        text=text,
    )


def resolve_item_id(query: str) -> str | None:
    """The item class id a name or id names, or None."""
    return resolve_item(game(), query)


def recipe_names() -> dict[str, str]:
    """Recipe class id -> display name; empty when the game data will not load."""
    try:
        return {cls: r.name for cls, r in game().recipes.items()}
    except Exception:
        return {}


def retired(*renamed: tuple[str, object, str]) -> str | None:
    """The refusal a retired parameter answers with, or ``None`` when none was passed.

    Each triple is ``(old spelling, what the caller passed, new spelling)``; the caller's own
    value is echoed so the answer is the line to write instead.
    """
    for old, value, new in renamed:
        if value is not None:
            return f"! {old}={value!r} is retired -- write {new}={value!r} instead"
    return None


@lru_cache(maxsize=1)
def stale_artifact_notes() -> tuple[str, ...]:
    """Whether the generated tables under ``data/`` still describe the build installed here.

    Cached like ``game``: neither the install nor a generated table changes under a running
    server, and this reads six sidecars.
    """
    try:
        return tuple(provenance.stale_artifacts(config.game_root(), config.data_dir()))
    except (FileNotFoundError, OSError):
        # No install to compare against is not drift.
        return ()


#: How many of a channel's warnings are quoted before the rest are counted.
INTEGRITY_NOTES_SHOWN = 4


def integrity_notes(projection: dict, data: GameData) -> list[str]:
    """What the two normalisation guards found, as notes, or nothing at all.

    Both channels collect drift instead of raising, which is only a good trade while somebody
    is told: unread, a game update reads as a quietly smaller world.
    """
    notes = []
    for channel, found in (
        ("this save", list(projection.get("warnings") or [])),
        ("the game's own data", list(data.warnings)),
    ):
        if not found:
            continue
        shown = render.capped(found, INTEGRITY_NOTES_SHOWN, sep="; ", more="; and {n} more")
        notes.append(
            f"{len(found)} problem(s) reading {channel}, so what follows may describe less "
            f"than is really there: {shown}"
        )
    return notes
