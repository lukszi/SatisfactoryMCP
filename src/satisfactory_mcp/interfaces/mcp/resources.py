"""MCP resources: client-pulled orientation data."""

from __future__ import annotations

import json

from ...core.text import ago, format_local_time
from ...domain.factories.labels import SCHEMA, LabelStore
from ...domain.spatial import regions as regions_mod
from ...presenters.text import primitives as render
from . import app

# Resources are CLIENT-PULLED, so they cost zero context until something asks for
# them. That makes them right for stable orientation data and wrong for anything
# parameterised, which stays a tool.


@app.mcp.resource("satisfactory://docs/summary", mime_type="text/plain")
def docs_summary() -> str:
    """One-line census of the normalized game data, plus its content hash."""
    g = app.game()
    kinds: dict[str, int] = {}
    for r in g.recipes.values():
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return render.envelope(
        render.kv(
            [
                ("items", len(g.items)),
                ("fluids", sum(1 for i in g.items.values() if i.is_fluid)),
                ("recipes", len(g.recipes)),
                ("automatable", kinds.get("part", 0)),
                ("alternates", len(g.alternates())),
                ("buildings", len(g.buildings)),
                ("schematics", len(g.schematics)),
                ("docs_sha256", g.docs_sha256[:16]),
                ("warnings", len(g.warnings)),
            ]
        ),
        notes=app.integrity_notes({}, g) + list(app.stale_artifact_notes()),
    )


@app.mcp.resource("satisfactory://save/current", mime_type="text/plain")
def current_save() -> str:
    """Which world and file the server would read right now, and its headline state."""
    st, reason = app.load_world_or_none()
    if st is None:
        return f"no readable save: {reason}"
    p = st.progression()
    mtime_ns = st.header.get("mtime_ns")
    written = f"{format_local_time(mtime_ns)} ({ago(mtime_ns)})" if mtime_ns else "?"
    if "autosave" in (st.header.get("filename") or "").lower():
        written += " -- an autosave; the game writes them periodically, so disk may lag the world"
    return render.envelope(
        render.kv(
            [
                # Leads, and is the one line here a tool takes back: this resource is where an
                # orienting client looks first, and `file` alone cannot name a world state
                # because the game rewrites `autosave_0` every rotation. See as_of= in
                # docs/mcp-surface.md 10.1i.
                ("save_token", st.token),
                ("file", st.header.get("filename")),
                ("written", written),
                ("world", st.header.get("session_name")),
                ("played_h", int((st.header.get("play_duration_s") or 0) / 3600)),
                ("save_version", st.header.get("save_version")),
                ("phase", p["game_phase"]),
                ("tier_complete", p["highest_complete_tier"]),
                ("recipes", p["available_recipes"]),
                ("alternates", len(st.unlocked_alternates)),
                ("hard_drives_pending", len(st.hard_drive_offers)),
            ]
        ),
        notes=app.integrity_notes(st.projection, st.game) + list(app.stale_artifact_notes()),
    )


@app.mcp.resource("satisfactory://factories/labels", mime_type="application/json")
def factory_labels() -> str:
    """Factory labels for the current world, as the JSON another tool can consume.

    Labels are the one thing in this server a player authored by hand, so they are the
    one thing worth publishing as a stable interface rather than a private cache. The
    file is per world, keyed by the save header's ``saveIdentifier``, and carries a
    ``schema`` integer so a reader can refuse a shape it does not know.

    ``anchors`` are machine instance names, which were verified stable across saves --
    365 of 365 kept id and position between two files. That is what makes a label
    portable: a consumer can join it against its own read of the same save without
    needing anything from this server.
    """
    st, reason = app.load_world_or_none()
    if st is None:
        return json.dumps({"error": f"no readable save: {reason}"}, indent=1)

    store = st.labels
    return json.dumps(
        {
            "schema": SCHEMA,
            "world_id": store.world_id,
            "session_name": store.session_name,
            "path": str(LabelStore.path_for(store.world_id)),
            "labels": [label.to_json() for label in store.labels],
        },
        indent=1,
    )


@app.mcp.resource("satisfactory://map/regions", mime_type="text/plain")
def map_regions() -> str:
    """Region names available as source selectors, with their accuracy caveat."""
    rm = regions_mod.load_regions()
    return (
        f"# {len(rm.names())} regions, advisory names, ~{rm.meta.get('accuracy_m', 256)}m "
        "boundary accuracy\n" + "\n".join(rm.names())
    )
