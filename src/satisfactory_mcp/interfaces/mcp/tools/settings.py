"""The settings the page and chat share (docs/shared-settings.md)."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from ....core.filelock import LockTimeout
from ....core.schema import NewerSchema
from ....core.text import ago
from ....domain import settings as store
from ....domain.maps import registry as maps
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


def _maps_line() -> str:
    """One line naming the base map types the page can show, the shared default marked."""
    try:
        view = maps.view()
    except Exception as exc:
        return f"# base maps: unreadable ({exc})"
    shown = []
    for row in view["types"]:
        if row["status"] != "ready":
            continue
        word = row["id"] + (" (default)" if row["id"] == view["default"] else "")
        if row["freshness"]["stale"]:
            word += " -- stale: " + "; ".join(s["text"] for s in row["freshness"]["stale"])
        shown.append(word)
    if view["default"] == maps.PLAIN:
        shown.append("plain (default)")
    if not shown:
        return "# base maps: none generated; the page's Settings > maps tab makes them"
    return "# base maps (show_on_map mode=): " + ", ".join(shown)


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
    if view["by"]:
        who = Actor.from_dict(view["by"]).display()
        when = ago(int(float(view["updated"] or 0) * 1e9)) or "at an unknown time"
        head += f"\n# last changed by {who} {when}"
    head += "\n" + _maps_line()
    return render.envelope(head, render.table(("setting", "value", "takes", "means"), rows))


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
    stored headroom against; ``biomass`` is the default of every ``biomass=`` parameter.
    ``payback_hours`` and ``overclock_last`` are what a plan that sets neither follows. The
    page's Settings tab reads and writes the same file, and an open page follows a change.
    The base map types are listed read-only; generating one is the page's job.
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
