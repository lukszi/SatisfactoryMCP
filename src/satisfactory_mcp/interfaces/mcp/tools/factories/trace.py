"""``trace_upstream``: what feeds a machine, or what it feeds, on the save's connections."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .....core.gamedata.model import GameData
from .....core.saveio import ports
from .....domain.factories.trace import Trace, power_at_risk, resolve_seeds, trace
from .....domain.world.logistics import Link
from .....domain.world.state import WorldState
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: Run ids named in a trace's route note: a sample to follow, beside counts that are whole.
VIA_NAMED = 6


def _via(crossed: list[Link]) -> str:
    """The route a trace took, as runs rather than as the hundreds of nodes they contract.

    Named in the order the walk met them, so the sample is the near end of the chain rather
    than six consecutive pipes of whichever network sorts first.
    """
    if not crossed:
        return ""
    belts = sum(1 for link in crossed if link.medium == ports.CONVEYOR)
    idents = [link.ident for link in crossed if link.ident]
    counted = " and ".join(
        f"{n} {word} run(s)" for n, word in ((belts, "belt"), (len(crossed) - belts, "pipe")) if n
    )
    out = f"the route ran through {counted}, {sum(link.pieces for link in crossed)} pieces in all"
    if idents:
        out += (
            "; it crossed "
            + render.capped(idents, VIA_NAMED, more=" and {n} more")
            + ", which search_conduits and show_on_map both take"
        )
    return out


def _trace_notes(
    st: WorldState, g: GameData, result: Trace, seeds: list[str], via: str
) -> list[str]:
    """How far the walk went, how direction was decided, and what power hangs off it."""
    notes = [
        (
            f"walked {result.visited} node(s) to depth {result.deepest}; the belt and pipe "
            "nodes are traversed and never listed one by one, because a path through them "
            "is unreadable -- "
            + (
                "the route note below names the RUNS they contract to instead"
                if via
                else "search_conduits lists the runs themselves, with endpoints and lengths"
            )
        ),
        (
            "direction comes from each edge's connector role, and from the machine's own "
            "nature where the role does not say. "
            + (
                f"{result.ambiguous} edge(s) have neither -- belt-to-belt and pipe-to-pipe "
                "segments, which are walked BOTH ways, so this list can over-report a "
                "feeder but never miss one"
                if result.ambiguous
                else "Every edge here states its direction"
            )
        ),
    ]
    if via:
        notes.append(via)
    if result.truncated:
        notes.append(
            "the walk stopped at its hop limit, so this is a FLOOR: machines further along "
            "the chain exist and are not listed"
        )
    mw, gens, running = power_at_risk(st, g, seeds)
    if gens:
        notes.append(
            f"downstream of this sits {gens} generator(s), {running} of them PROVEN running "
            f"in the last 300s window, worth {mw:,.0f} MW. Cutting this feed stops that "
            "power -- idle generators are not counted, since they are already not producing"
        )
    return notes


@app.tool()
def trace_upstream(
    seed: Annotated[
        str, Field(description="a machine instance, a factory label, or a building name")
    ],
    direction: Annotated[
        str, Field(description="up (what feeds it) | down (what it feeds)")
    ] = "up",
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
) -> str:
    """What feeds a machine, or what it feeds -- walked on the save's own connections.

    `factory_query` answers this between LABELLED sets; this answers it for one machine, a
    building type or a label, which is the question a cutover asks: which feed can be
    repiped without dropping the generators behind it.

    Direction is READ from each edge's connector role, or from the machine's own nature
    where the role is silent; an edge with neither is walked BOTH ways, so a feeder may be
    over-reported but never missed. Belts and pipes are walked THROUGH and left out of the
    table; a note names the runs the route crossed, with the ids `search_conduits` takes.
    """
    g = app.game()
    st = app.load_world(save, world, as_of)
    seeds, subject = resolve_seeds(st, g, seed)
    if not seeds:
        return f"! nothing matches {seed!r} -- give a machine instance, a building name, or a factory label"

    way = (direction or "up").strip().casefold()
    if way not in ("up", "down"):
        return f"! unknown direction {direction!r}. Choose from: up, down"
    result = trace(st, g, seeds, way)

    rows: list[tuple[object, ...]] = []
    for name, group in sorted(result.by_class().items(), key=lambda kv: -len(kv[1])):
        hops = [r.hops for r in group]
        rows.append(
            (
                name[:28],
                group[0].kind,
                len(group),
                f"{min(hops)}..{max(hops)}" if min(hops) != max(hops) else str(min(hops)),
                ", ".join(r.instance for r in group[:3]),
            )
        )
    via = _via(result.crossed)
    notes = _trace_notes(st, g, result, seeds, via)
    # An empty table is an answer and says which one: a self-contained factory owns its chain.
    if not rows:
        body = (
            "nothing outside this selection "
            + ("feeds it" if way == "up" else "is fed by it")
            + f" -- the walk crossed {result.visited} belt/pipe node(s) and reached no other "
            "machine. Narrow the seed (a product: or a single instance) to see the chain "
            "INSIDE it."
        )
    else:
        body = render.table(
            ("building", "kind", "count", "hops", "examples"),
            rows,
            total=len(rows),
            limit=render.clamp(limit, default=20),
            hint="raise limit -- one row per building class, biggest first, and no offset",
        )
    return render.envelope(
        f"# {st.age_note}\n# {'what feeds' if way == 'up' else 'what is fed by'} {subject}",
        body,
        notes,
    )
