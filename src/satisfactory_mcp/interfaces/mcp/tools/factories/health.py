"""``factory_health``: measured uptime, and why each stopped machine is stopped."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

from pydantic import Field

from .....core.saveio import ports
from .....domain.factories.health import (
    FLOW_RATE,
    JOINED,
    NOTHING,
    OPEN,
    RUNGS,
    STATES,
    UNFED,
    Feed,
    HealthReport,
    MachineHealth,
    assess,
)
from .....domain.factories.select import resolve_factory
from .....domain.factories.sweep import sweep
from .....domain.world import headlift
from .....domain.world.plumbing import throttled_buffers, unwired_pumps
from .....domain.world.state import WorldState
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf, Limit

#: How many machines a note names before it counts the rest. The names are what
#: show_on_map and factory_query take back; a hundred of them is one unwired block.
UNWIRED_NAMED = 8


def _named(instances: list[str]) -> str:
    """The first ``UNWIRED_NAMED`` instance names, and a count of the rest."""
    return render.capped(instances, UNWIRED_NAMED, more=", and {n} more")


def _health_headline(report: HealthReport) -> str:
    """One line, states worst-first."""
    parts = [f"{report.by_state[s]} {s}" for s in STATES if report.by_state[s]]
    mean = report.mean_uptime
    head = f"{len(report.machines)} machines"
    if mean is not None:
        head += f", mean uptime {mean:.0%}"
    return head + (" -- " + ", ".join(parts) if parts else "")


def _feed_row(machine: MachineHealth, feed: Feed) -> tuple[str, ...]:
    """One starved input and the run that should be bringing it.

    The not-fed verdicts read differently on purpose: nothing arriving is a finding and so is
    a network no source reaches, while a run whose far end the save joins to no actor is not.
    """
    carrier = "conveyor" if feed.medium == ports.CONVEYOR else "pipe"
    if feed.verdict == NOTHING:
        arrives, far = f"NO {carrier} arrives", ""
    else:
        arrives = f"{feed.run or carrier} x{feed.pieces}"
        far = "far end joined to nothing" if feed.verdict == OPEN else f"{feed.far_name} {feed.far}"
        if feed.verdict == JOINED:
            far += " (which way is unresolved)"
        if feed.verdict == UNFED:
            far += " -- and NO source anywhere on this network"
        if feed.makes:
            far += " -- MAKES it"
    return (machine.instance, feed.item, arrives, far, feed.far_state)


def _plumbing_sections(st: WorldState, window: render.Page) -> tuple[list[str], list[str]]:
    """The world-wide plumbing faults: starved buffers, head-lift crests, dark pumps.

    World-wide here and nowhere else, because a buffer and a pump belong to no machine set.
    """
    buffer_chunks, buffer_notes = _buffer_section(st, window)
    head_chunks, head_notes = _head_lift_sections(st, window)
    return buffer_chunks + head_chunks, buffer_notes + head_notes + _pump_notes(st)


def _buffer_section(st: WorldState, window: render.Page) -> tuple[list[str], list[str]]:
    """Fluid buffers holding too little to output at the rate they take in."""
    chunks: list[str] = []
    notes: list[str] = []
    throttled = throttled_buffers(st.projection, st.game)
    if throttled:
        chunks.append(
            "## fluid buffers below the level they need\n"
            + render.paged_table(
                ("buffer", "fluid", "holding m3", "needs m3"),
                [
                    (
                        b.instance,
                        st.game.item_name(b.fluid) if b.fluid else "-",
                        f"{b.stored_m3:.0f}",
                        f"{b.balance_m3:.0f}",
                    )
                    for b in throttled
                ],
                window,
            )
        )
        notes.append(
            f"{len(throttled)} fluid buffer(s) world-wide are under the level they need: "
            "a buffer's head lift is the height of the fluid standing in it, so one "
            "holding less than 1.5 m of fluid outputs slower than it takes in, silently"
        )
    return chunks, notes


def _head_lift_sections(st: WorldState, window: render.Page) -> tuple[list[str], list[str]]:
    """Crests above the head lift behind them, lines riding a buffer's head, unfed ports."""
    chunks: list[str] = []
    notes: list[str] = []
    head = headlift.head_lift(st.projection, st.game, st.graph)
    if head.faults:
        chunks.append(
            "## fluid lines that climb higher than their supply can push\n"
            + render.paged_table(
                ("fluid", "crest m", "head m", "short by", "machines cut off", "state"),
                [
                    (
                        st.game.item_name(c.fluid) if c.fluid else "-",
                        f"{c.crest_m:.1f}",
                        f"{c.head_m:.1f}",
                        f"{c.short_m:.1f}",
                        len(c.consumers),
                        "marginal" if c.marginal else "cut off",
                    )
                    for c in head.faults
                ],
                window,
            )
        )
        notes.append(
            f"{len(head.faults)} point(s) on the plumbing stand above the head lift "
            "behind them, so nothing past them is supplied -- a pump placed BEFORE the "
            "crest is the fix, and a second pump after it would add nothing"
        )
    if head.buffer_lines:
        chunks.append(
            "## lines running on a part-full buffer's own head\n"
            + render.paged_table(
                ("fluid", "rises to m", "buffer delivers at m", "over by", "machines past it"),
                [
                    (
                        st.game.item_name(c.fluid) if c.fluid else "-",
                        f"{c.crest_m:.1f}",
                        f"{c.head_m:.1f}",
                        f"{c.short_m:.1f}",
                        len(c.consumers),
                    )
                    for c in head.buffer_lines
                ],
                window,
            )
        )
        notes.append(
            "a buffer passes incoming head on only when it is nearly full, so the line "
            "above one gets the buffer's own fluid level and no more. These are NOT "
            "called faults: the same shape runs at full uptime on this world, so the "
            "reading is 'this line has no margin above its buffer', not 'it is broken'"
        )
        if head.undecided_buffers:
            notes.append(
                f"{head.undecided_buffers} buffer(s) sit in the band between the fill "
                "measured not to pass head on and the one measured to, so a constant "
                "settled them rather than a measurement"
            )
        if any(c.assumed for c in head.crests):
            notes.append(
                "some of those rest on the 10 m of head lift a normal machine is assumed "
                "to give, which is the plumbing manual's figure and is in no game data"
            )
    if head.unfed_ports:
        named = sorted(set(head.unfed_ports))
        notes.append(
            f"{len(named)} machine(s) draw a fluid from a pipe network that reaches NO "
            "source at all -- rung (1) of the plumbing manual's order, a line to finish "
            "rather than a shortage, and not a head-lift fault: " + _named(named)
        )
    return chunks, notes


def _pump_notes(st: WorldState) -> list[str]:
    """Pipeline pumps on no wire, and those coupled to no pipe that went unchecked."""
    notes: list[str] = []
    dark, unseen = unwired_pumps(st.projection, st.graph)
    if dark:
        notes.append(
            f"{len(dark)} pipeline pump(s) have no electrical connection: an unpowered "
            "pump still passes fluid but lifts nothing, so a line that climbs past it "
            "stops climbing and no machine on it looks broken -- " + _named(dark)
        )
    if unseen:
        notes.append(
            f"{unseen} pipeline pump(s) are coupled to no pipe at all and were not "
            "checked for a wire"
        )
    return notes


def _sweep_report(st: WorldState, window: render.Page) -> str:
    """Every named factory's uptime and state counts, then the world-wide plumbing."""
    if not st.labels.labels:
        return "! nothing named yet -- run propose_factories, then name_factory"
    rows: list[tuple[object, ...]] = []
    notes: list[str] = []
    blocked_total = 0
    dark_total = 0
    for swept in sweep(st):
        label, report, view = swept.label, swept.report, swept.view
        mean = report.mean_uptime
        blocked_total += report.by_state["blocked"]
        dark_total += len(report.unwired) + len(report.no_generator)
        rows.append(
            (
                label.name,
                len(report.machines),
                "-" if mean is None else f"{mean:.0%}",
                f"{view.measured_draw_mw:.0f}",
                report.by_state["blocked"] or "",
                report.by_state["starved"] or "",
                report.by_state["stalled"] or "",
                report.by_state["no recipe"] or "",
                report.by_state["dead node"] or "",
                report.by_state["paused"] or "",
                swept.actionable or "",
            )
        )
    notes.append(
        "measured MW is each machine's rated draw weighted by its own 300s productivity "
        "window -- what the factory is actually taking off the grid, not what it could"
    )
    if blocked_total:
        notes.append(
            f"{blocked_total} machine(s) are blocked -- their output stack is full, so "
            "nothing is taking what they make. They count in todo; factory_health on "
            "one factory names the items backing up"
        )
    if dark_total:
        # A note rather than a column: a count that is zero on a finished factory does
        # not earn a column in every row.
        notes.append(
            f"{dark_total} machine(s) across these factories are on no circuit a "
            "generator stands on -- no wire at all, or a wire to a circuit with no "
            "source. factory_health on the one factory names them and says which"
        )
    chunks = [
        render.paged_table(
            (
                "factory",
                "n",
                "uptime",
                "measured MW",
                "blocked",
                "starved",
                "stalled",
                "no recipe",
                "dead node",
                "paused",
                "todo",
            ),
            rows,
            window,
        )
    ]
    plumbing, plumbing_notes = _plumbing_sections(st, window)
    return render.envelope(
        f"# {st.age_note}\n# uptime measured over a 300s window per machine",
        "\n\n".join(chunks + plumbing),
        notes + plumbing_notes,
    )


def _feed_notes(supply: list[tuple[MachineHealth, Feed]]) -> list[str]:
    """What the feed table can and cannot say, and which rung each missing fluid reached."""
    notes = [
        (
            "the far end is ONE hop: trace_upstream walks the rest of the chain. A run "
            "carries whatever is put on it, so where several arrive the save does not say "
            "which was meant to bring the item -- only the one marked MAKES it provably could"
        )
    ]
    nothing = sum(1 for _m, f in supply if f.verdict == NOTHING)
    if nothing:
        notes.append(
            f"{nothing} of these inputs have NO conduit of that medium arriving at all -- "
            "the item cannot reach the machine, which is a build to finish, not a shortage"
        )
    sourceless = sum(1 for _m, f in supply if f.verdict == UNFED)
    if sourceless:
        notes.append(
            f"{sourceless} of these inputs arrive by a pipe from a real fitting whose "
            "network reaches NO source at all -- the conduit is fine and nothing "
            "anywhere puts that fluid into it, so the fix is a source rather than a "
            "pump or a reroute"
        )
    loose = sum(1 for _m, f in supply if f.verdict == OPEN)
    if loose:
        notes.append(
            f"{loose} run(s) do arrive and the save joins their far end to nothing, so "
            "the feeder is UNKNOWN there rather than absent -- a torn line or a build in "
            "progress. Not the same finding as the row above"
        )
    # Per (machine, ingredient), not per row: three runs reaching one input are one diagnosis.
    rungs = Counter(
        rung for _m, _item, rung in {(m.instance, f.item, f.rung) for m, f in supply if f.rung}
    )
    if rungs:
        reached = [
            f"{rungs[rung]} at ({i}) {rung}" for i, rung in enumerate(RUNGS, 1) if rungs[rung]
        ]
        notes.append(
            f"{sum(rungs.values())} missing fluid(s) walked the plumbing manual's order -- "
            "(1) connection, (2) head lift, (3) flow rate -- and each stops at the first "
            "rung that fires, which is what the bracket in its cause names: "
            + ", ".join(reached)
            + (
                ". Reaching (3) means the head-lift model checked the climb and ruled its "
                "own rung out"
                if rungs[FLOW_RATE]
                else ""
            )
        )
    return notes


def _state_notes(report: HealthReport) -> list[str]:
    """One note per state that needs explaining, and per unpowered group of machines."""
    notes: list[str] = []
    if report.by_state["blocked"]:
        notes.append(
            f"{report.by_state['blocked']} blocked: output stack full, so its consumer "
            "is the bottleneck -- or nothing is drawing from it at all"
        )
    if report.by_state["dead node"]:
        notes.append(
            f"{report.by_state['dead node']} extractor(s) sit on NO resource node -- "
            "the node was removed, so they can never produce and must be rebuilt elsewhere"
        )
    if report.by_state["stalled"]:
        dark = sum(1 for m in report.machines if m.state == "stalled" and m.cause)
        notes.append(
            f"{report.by_state['stalled']} stalled: has input, output has room, still not "
            + (
                f"producing, and {dark} of them no generator can reach over the wires -- "
                "their cause says whether that is no wire at all or a circuit with no source"
                if dark
                else "producing -- and every one of them is on a circuit some generator "
                "stands on, so power delivery, a switch or a monitor that has not caught up, "
                "not a connection left unbuilt"
            )
        )
    if report.unwired:
        notes.append(
            f"{len(report.unwired)} machine(s) have no electrical connection at all -- no wire "
            "reaches them, whatever else they are doing: " + _named(report.unwired)
        )
    if report.no_generator:
        notes.append(
            f"{len(report.no_generator)} machine(s) are wired to a circuit NO GENERATOR stands "
            "on, so the wire reaches them and no power does -- a different build to finish "
            "from the row above: " + _named(report.no_generator)
        )
    if report.by_state["unmonitored"]:
        notes.append(
            f"{report.by_state['unmonitored']} machine(s) keep no productivity monitor, "
            "so their uptime is unknown rather than zero"
        )
    return notes


def _factory_report(st: WorldState, name: str, machines: list[str], window: render.Page) -> str:
    """One factory: what needs attention, what backs up or never arrives, and why."""
    heads = headlift.head_lift(st.projection, st.game, st.graph)
    report = assess(name, machines, st.game, st.projection, st.graph, st.physical, heads)
    chunks = [_health_headline(report)]

    worst = report.worst(window.end)[window.start :]
    if worst:
        chunks.append(
            "## needs attention\n"
            + render.table(
                ("instance", "state", "uptime", "recipe", "cause"),
                [
                    (
                        m.instance,
                        m.state,
                        "-" if m.uptime is None else f"{m.uptime:.0%}",
                        m.recipe or st.game.building_name(m.building) or m.building,
                        ", ".join(m.cause),
                    )
                    for m in worst
                ],
                total=sum(1 for m in report.machines if m.needs_attention),
                offset=window.start,
                limit=window.size,
            )
        )
    if report.blocked_on:
        chunks.append(
            "## output backing up\n"
            + render.paged_table(
                ("item", "machines blocked"), report.blocked_on.most_common(), window
            )
        )
    if report.starved_of:
        chunks.append(
            "## inputs not arriving\n"
            + render.paged_table(
                ("ingredient", "machines starved"), report.starved_of.most_common(), window
            )
        )
    supply = [(m, f) for m in report.machines for f in m.feeds]
    if supply:
        chunks.append(
            "## what feeds the missing input\n"
            + render.table(
                ("machine", "missing", "arrives by", "at the far end", "its state"),
                [_feed_row(m, f) for m, f in window.of(supply)],
                total=len(supply),
                offset=window.start,
                limit=window.size,
            )
        )
    # Only the crests that cut off a machine here: naming one says where the pump goes.
    standing = {m.instance for m in report.machines}
    crests = [c for c in heads.crests if standing.intersection(c.consumers)]
    if crests:
        chunks.append(
            "## where the fluid stops climbing\n"
            + render.paged_table(
                ("fluid", "crest m", "head m", "short by", "machines here"),
                [
                    (
                        st.game.item_name(c.fluid) if c.fluid else "-",
                        f"{c.crest_m:.1f}",
                        f"{c.head_m:.1f}",
                        f"{c.short_m:.1f}",
                        len(standing.intersection(c.consumers)),
                    )
                    for c in crests
                ],
                window,
            )
        )

    notes: list[str] = []
    if crests:
        notes.append(
            "a pump placed BEFORE the crest is the fix and a second one after it would add "
            "nothing -- head lift does not stack pump to pump, only with the height a pump "
            "already stands at"
        )
    if supply:
        notes += _feed_notes(supply)
    notes += _state_notes(report)
    return render.envelope(f"# {st.age_note}\n# {name}", "\n\n".join(chunks), notes)


@app.tool()
def factory_health(
    factory: Annotated[
        str, Field(description="a label name, any selector, or 'all' for every named factory")
    ] = "all",
    limit: Limit = 15,
    offset: int = 0,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Measured uptime per machine, and WHY each stopped one is stopped.

    Every manufacturing building keeps a 300-second productivity window; uptime is the
    share of it spent producing.

    States, worst first: `paused`, `dead node` (extractor bound to no resource), `no
    recipe`, `blocked` (output stack full), `starved` (input empty), `stalled` (has input,
    output has room, still not running), `intermittent`, `saturated`, `unmonitored`. The
    sweep's `todo` counts dead node, no recipe, blocked, starved and stalled.

    A machine no generator can reach over the wires says so -- "no wire at all" and "wired
    to a circuit no generator stands on" separately. The save has no "has power" flag, so a
    machine a generator CAN reach is never called unpowered.

    For a STARVED machine it names what feeds the missing input ONE hop back: the run that
    arrives, what stands at its far end, and that feeder's state; `trace_upstream` walks
    further. No conduit arriving and one whose far end joins nothing are never merged. A
    missing FLUID is diagnosed on the plumbing ladder -- (1) connection, (2) head lift, (3)
    flow rate -- and the cause names the first rung that fires, so a line that cannot climb
    to the machine is never answered with its supply rates.

    factory='all' sweeps every named factory and adds the world-wide plumbing faults: fluid
    buffers too low to output at their intake rate, unwired pipeline pumps, and lines that
    climb above their head lift. `offset` pages every table at once, worst first.
    """
    st = app.load_world(save, world, as_of)
    window = render.page(limit, offset)
    if factory.strip().casefold() in ("all", "*"):
        return _sweep_report(st, window)
    name, machines = resolve_factory(st, factory)
    if not machines:
        return f"! {factory!r} resolved to no machines that still exist in this save"
    return _factory_report(st, name, machines, window)
