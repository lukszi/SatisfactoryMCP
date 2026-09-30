"""The settings the page and chat share (docs/shared-settings.md)."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....core.text import ago
from ....domain import settings as store
from ....domain.planning.planlog import Actor
from ....presenters.text import primitives as render
from ..app import actor, mcp


def _word(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return f"{value:g}" if isinstance(value, float) else str(value)


def _choices(spec: store.Spec) -> str:
    if spec.kind == "switch":
        return "true|false"
    if spec.kind == "choice":
        return "|".join(spec.options)
    return f"{spec.low:g}..{spec.high:g}"


def _render(view: dict, head: str) -> str:
    rows = [
        (
            key,
            _word(view["values"][key]) + ("" if key in view["stored"] else " (default)"),
            _choices(spec),
            spec.hint,
        )
        for key, spec in store.SPECS.items()
    ]
    notes = []
    if view["by"]:
        who = Actor.from_dict(view["by"]).display()
        when = ago(int(float(view["updated"] or 0) * 1e9)) or "at an unknown time"
        notes.append(f"last changed by {who} {when}")
    return render.envelope(head, render.table(("setting", "value", "takes", "means"), rows), notes)


@mcp.tool(structured_output=False)
def settings(
    change: Annotated[
        dict[str, str | bool | float | None] | None,
        Field(description='settings to change, e.g. {"stage_headroom": "nameplate"}; null resets'),
    ] = None,
    ctx: Context | None = None,
) -> str:
    """The settings the page and chat share. Change one only when the user asks you to.

    ``stage_headroom`` is what ``diff_vs_save`` and ``commission_plan`` stage a plan with no
    stored headroom against; ``biomass`` is the default of every ``biomass=`` parameter. The
    page's Settings tab reads and writes the same file, and an open page follows a change.
    """
    try:
        if change:
            before = store.read()["version"]
            view = store.write(dict(change), actor(ctx))
            moved = view["version"] != before
            head = f"# shared settings v{view['version']}: " + (
                "changed; every open page follows" if moved else "already so, nothing written"
            )
        else:
            view = store.read()
            head = f"# shared settings v{view['version']} (the page's Settings tab shows these)"
    except store.SettingsError as exc:
        return f"! {exc}"
    except (NewerSchema, LockTimeout, OSError) as exc:
        return f"! settings unreadable, nothing written: {exc}"
    return _render(view, head)
