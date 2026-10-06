"""``show_on_map``: map links centred on a place, and pinning it for the page."""

from __future__ import annotations

from typing import Annotated, NamedTuple

from mcp.server.fastmcp import Context
from pydantic import Field

from .....domain.maps import registry as maps
from .....domain.session import journal, pins
from .....domain.spatial import maplink
from .....domain.spatial import nodes as nodes_mod
from .....domain.spatial.places import NODE_PREFIX, PLAN_PREFIX, RUN_PREFIXES, resolve_place
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf


class MapTarget(NamedTuple):
    """What ``at`` resolved to: a point, its words, and the node or resources it names."""

    origin: tuple[float, float]
    where: str
    node: dict | None
    resources: list[str]
    notes: list[str]


def _base_map(mode: str) -> str:
    """``mode`` as the local link spells it; a ``Refusal`` for a map that is not ready."""
    mode = "map" if mode.strip() == "artwork" else mode.strip()
    entry, _where = maps.lookup(mode)
    if mode != maps.PLAIN and (entry is None or entry.get("status") != "ready"):
        known = ", ".join([*maps.known_ids(), maps.PLAIN])
        raise app.Refusal(f"! no base map {mode!r}; known: {known}")
    return mode


def _resource_target(g, table, value: str) -> MapTarget:
    """``resource:<name>``: the centroid of every node of it, with its overlays on."""
    item = app.resolve_item_id(value.strip())
    if not item or item not in maplink.LAYERS:
        raise app.Refusal(f"! no map layer for resource {value.strip()!r}")
    rows = table.by_resource(item)
    if not rows:
        raise app.Refusal(f"! no {g.item_name(item)} nodes on the map")
    origin = (
        sum(r["x"] for r in rows) / len(rows),
        sum(r["y"] for r in rows) / len(rows),
    )
    return MapTarget(
        origin,
        f"all {len(rows)} {g.item_name(item)} node(s)",
        None,
        [item],
        [
            (
                "centred on the centroid of every node of that resource, which may be open "
                "water if they are spread across the map -- pass node:<id> or x,y to pin it"
            )
        ],
    )


def _place_target(g, st, table, text: str, kind: str, value: str) -> MapTarget:
    """Any other place, and the node's own resource when the place is a node."""
    try:
        origin, where = resolve_place(st, text)
    except ValueError as exc:
        raise app.Refusal(f"! {exc}") from None
    # The resolver answers with a point; the overlay wants the node's own resource.
    node = None
    if kind.casefold() == NODE_PREFIX:
        short = {k.rsplit(".", 1)[-1]: v for k, v in table.by_instance().items()}
        node = short.get(value.strip())
    if node is None:
        return MapTarget(origin, where, None, [], [])
    return MapTarget(
        origin, f"{where} -- {g.item_name(node['resource'])}", node, [node["resource"]], []
    )


@app.tool()
def show_on_map(
    at: Annotated[
        str,
        Field(
            description="any place -- 'x,y' in metres, 'me', a factory label, "
            "'node:<id>', 'machine:<id>', 'slab:<n>', 'chain:<n>'/'pipe:<n>', 'plan:<name>' -- or "
            "'resource:Crude Oil' for every node of one resource"
        ),
    ],
    layers: Annotated[
        list[str] | None,
        Field(description="explicit sublayer tokens, overriding the guess"),
    ] = None,
    zoom: float = 4.75,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    pin: Annotated[bool, Field(description="also pin it on the page (pin:N)")] = False,
    mode: Annotated[
        str | None,
        Field(description="base map type for the local link, an id settings() lists, or plain"),
    ] = None,
    ctx: Context | None = None,
) -> str:
    """Map links centred on something: this project's own map, and the public one.

    Two links for every place. The LOCAL one opens this project's web map, which draws
    the reader's own save -- their machines, their belts, their siting. The
    satisfactory-calculator.com one opens a third-party map of the vanilla world, which
    knows the terrain and the nodes and nothing the player built.

    `at` is the same place vocabulary every other tool takes (see docs/selectors.md),
    plus one kind of its own: `resource:<name>` centres on the centroid of EVERY node of
    that resource and switches its overlays on, which is a viewport rather than a place
    and is why no other tool accepts it.

    Only the Crude Oil layer tokens are confirmed; the rest follow the same pattern and
    are flagged. A wrong token still opens the map in the right place, just without that
    overlay.

    ``pin=True`` also pins the place for the page (a node, factory, sited plan or point;
    pinning it twice returns the pin it already has), and the page shows it at once.
    ``mode`` opens the local link on that base map instead of the shared default.
    """
    if mode:
        mode = _base_map(mode)

    g = app.game()
    st, _reason = app.load_world_or_none(save, world, as_of)

    table = nodes_mod.load_nodes()
    text = at.strip()
    kind, _sep, value = text.partition(":")
    if kind.casefold() == "resource":
        target = _resource_target(g, table, value)
    else:
        target = _place_target(g, st, table, text, kind, value)
    origin, where, node, resources = target.origin, target.where, target.node, target.resources
    notes = list(target.notes)

    # Which variants a resource actually HAS, read from the node table: only oil, nitrogen
    # and water have wells, so a well token for coal would name nothing.
    kinds = sorted(
        {
            "well" if r["kind"].startswith("well") else "node"
            for res in resources
            for r in table.by_resource(res)
        }
    )
    tokens = layers or maplink.layers_for(resources, kinds or None)

    # Identity only: a metre of drift is below a pixel at any zoom this emits.
    if node is not None:
        notes += nodes_mod.identity_notes(
            nodes_mod.skew_for_save(st.header if st else None, table), [node["instance"]]
        )

    # The local map leads for every target: it is the only one of the two that draws
    # this world.
    label = st.labels.find(text) if st and node is None and not resources else None
    local = maplink.local_map_url(
        origin[0] / 100.0,
        origin[1] / 100.0,
        world=st.world_id if st else "",
        show=maplink.show_ref(
            node=node["instance"] if node is not None else None,
            run=text if kind.casefold() in RUN_PREFIXES else None,
            label=label.name if label is not None and label.name == where else None,
        ),
        mode=mode or "",
    )
    body = f"local map: {local}\npublic map: {maplink.map_url(*origin, tokens, zoom=zoom)}"
    if tokens:
        body += "\n# layers: " + ", ".join(tokens)
    notes.append(
        "the local map is this project's own web map and draws YOUR save, with the server "
        "running; the public one is satisfactory-calculator.com and knows the vanilla "
        "world only -- nothing you built is on it"
    )
    if pin:
        body += "\n" + _pin_place(st, text, node, label, origin, resources, ctx)
    return render.envelope(
        f"# {where} at {round(origin[0] / 100, 1):g},{round(origin[1] / 100, 1):g} (metres)",
        body,
        notes,
    )


def _pin_place(st, text: str, node, label, origin, resources, ctx) -> str:
    """Pin what ``show_on_map`` showed; the ``pin:`` line, or why nothing was pinned."""
    if st is None:
        return "! not pinned: the save could not be read"
    head = text.partition(":")[0].casefold()
    if resources and node is None:
        return "! not pinned: a whole resource is no place; pin one node:<id> or x,y instead"
    if head == "pin":
        n = pins.parse(text)
        return f"pin: already pin:{n}" if n is not None else "! not pinned"
    if node is not None:
        kind, ref = "node", {"node": node["instance"]}
    elif head == PLAN_PREFIX:
        stored = st.plans.find(text.partition(":")[2].strip())
        kind, ref = "plan", {"plan": stored.key if stored is not None else ""}
    elif label is not None:
        kind, ref = "factory", {"factory": label.name}
    else:
        kind = "point"
        ref = {"x_m": round(origin[0] / 100.0, 1), "y_m": round(origin[1] / 100.0, 1)}
    try:
        row, existing = pins.create(st, kind, ref)
    except pins.PinError as exc:
        return f"! not pinned: {exc}"
    except Exception as exc:
        return f"! not pinned: the pins are busy ({type(exc).__name__})"
    if existing:
        return f"pin: already {row['id']} {row['text']}"
    plan = ref.get("plan") if kind == "plan" else None
    journal.append(
        st.world_id,
        "pin.add",
        actor=app.actor(ctx),
        plan=plan,
        args={"n": row["n"], "kind": kind},
        text=f"pinned {row['id']} {row['text']}",
    )
    return f"pin: pinned as {row['id']} {row['text']}"
