"""The MCP app object, and the state accessors every tool group needs.

Split out so tool modules can register against one ``mcp`` without importing each other.
``server`` imports the tool packages purely for their decorator side effects.
"""

from __future__ import annotations

import difflib
import functools
import os
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import TYPE_CHECKING, ParamSpec, TypeAlias

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.types import ContentBlock, TextContent

from ... import config
from ...core.gameassets import provenance
from ...core.gamedata.loader import load_docs
from ...core.gamedata.model import GameData
from ...core.gamedata.normalize import normalize
from ...core.gamedata.search import resolve_item
from ...core.jsontypes import JsonObject
from ...core.saveio.schema import Projection
from ...core.schema import NewerSchema
from ...domain import settings
from ...domain.factories.select import SelectorError
from ...domain.planning.stored.plan_args import PLAN_DEFAULTS
from ...domain.planning.stored.planlog import Actor
from ...domain.session import journal
from ...domain.world import pin
from ...domain.world.state import WorldState, load_state
from ...presenters.text import primitives as render

# The context a tool asks for; bare at runtime, where FastMCP reads it (docs/mcp-surface.md).
if TYPE_CHECKING:
    ToolContext: TypeAlias = Context[ServerSession, object, object]
else:
    ToolContext = Context

P = ParamSpec("P")

INSTRUCTIONS = (
    "Plans are versioned: read one (list_plans name=) and pass its version as base_rev when "
    "you change it. When the user says 'this', 'here' or 'what I have open', or quotes an "
    "ask: or pin: id, call ui_context first."
)


def undeclared_refusal(tool: str, arguments: Mapping[str, object], declared: frozenset[str]) -> str:
    """The refusal for the arguments ``tool`` does not declare, or '' when it declares them all."""
    unknown = sorted(set(arguments) - declared)
    if not unknown:
        return ""
    parts = [f"! {tool} does not take {', '.join(f'{arg}=' for arg in unknown)}; nothing ran."]
    near = [
        f"{close[0]}= for {arg}="
        for arg in unknown
        if (close := difflib.get_close_matches(arg, declared, n=1))
    ]
    if near:
        parts.append(f"Did you mean {', '.join(near)}?")
    stored = [f"{arg}=" for arg in unknown if arg in PLAN_DEFAULTS]
    if stored and "plan" in declared:
        parts.append(
            f"A saved plan carries {', '.join(stored)}: save it with "
            "plan_factory(..., save_as=<name>), then pass plan=<name> here."
        )
    return " ".join(parts)


class StrictFastMCP(FastMCP):
    """``FastMCP`` refusing an argument a tool does not declare; FastMCP itself drops it.

    docs/mcp-surface.md §10 says why; ``tests/mcp/test_undeclared_arguments.py`` drives a real
    client session, so an mcp upgrade that stops routing calls through here fails it.
    """

    async def call_tool(
        self, name: str, arguments: JsonObject
    ) -> Sequence[ContentBlock] | JsonObject:
        declared = await self.declared_arguments(name)
        refusal = undeclared_refusal(name, arguments, declared) if declared is not None else ""
        if refusal:
            return [TextContent(type="text", text=refusal)]
        return await super().call_tool(name, arguments)

    async def declared_arguments(self, name: str) -> frozenset[str] | None:
        """The argument names tool ``name`` publishes, or None for no such tool."""
        for tool in await self.list_tools():
            if tool.name == name:
                return frozenset(tool.inputSchema.get("properties") or ())
        return None


mcp = StrictFastMCP("satisfactory", instructions=INSTRUCTIONS)


class Refusal(Exception):
    """A tool's answer that ends the call early; ``tool`` returns its text verbatim."""


def tool() -> Callable[[Callable[P, str]], Callable[P, str]]:
    """Register a text tool on ``mcp``, answering a ``Refusal`` or ``SelectorError`` as text.

    ``structured_output=False`` because a ``-> str`` tool otherwise echoes its whole payload
    into ``structuredContent`` (docs/mcp-surface.md §10).
    """

    def register(fn: Callable[P, str]) -> Callable[P, str]:
        @functools.wraps(fn)
        def answer(*args: P.args, **kw: P.kwargs) -> str:
            try:
                return fn(*args, **kw)
            except Refusal as exc:
                return str(exc)
            except SelectorError as exc:
                return f"! {exc}"

        mcp.tool(structured_output=False)(answer)
        return answer

    return register


def shared_setting(key: str) -> tuple[settings.SettingValue, str]:
    """A shared setting's value, and a note when the file could not be read (the default)."""
    try:
        return settings.value(key), ""
    except (NewerSchema, OSError) as exc:
        return settings.SPECS[key].default, f"shared settings unreadable, {key} defaulted: {exc}"


def biomass_setting(biomass: bool | None) -> tuple[bool, str]:
    """``biomass`` as the caller gave it, else the shared setting and its unread note."""
    if biomass is not None:
        return biomass, ""
    stored, note = shared_setting("biomass")
    return bool(stored), note


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


def save_token(st: WorldState) -> str:
    """The token naming ``st``'s world state, or '' when it carries none."""
    try:
        return st.token
    except Exception:
        return ""


def actor(ctx: ToolContext | None) -> Actor:
    """Who is writing: this process, as the client named itself at ``initialize``."""
    try:
        # ``session`` raises ValueError outside a request.
        params = ctx.session.client_params if ctx is not None else None
        client = (params.clientInfo.name or "") if params is not None else ""
    except (AttributeError, ValueError):
        client = ""
    return Actor("chat", client, os.getpid())


def journal_world_find(
    st: WorldState,
    ctx: ToolContext | None,
    tool: str,
    view: str,
    params: Mapping[str, object],
    text: str,
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


def integrity_notes(projection: Projection | None, data: GameData) -> list[str]:
    """What the two normalisation guards found, as notes, or nothing at all.

    Both channels collect drift instead of raising, which is only a good trade while somebody
    is told: unread, a game update reads as a quietly smaller world. ``None`` is no save.
    """
    notes: list[str] = []
    for channel, found in (
        ("this save", list(projection.get("warnings") or []) if projection else []),
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
