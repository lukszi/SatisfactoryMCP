"""Progression: elevator phases, milestones, MAM research, power shards and somersloops."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from pydantic import Field

from ....core.gamedata.constants import CAPABILITY_SCHEMATICS, max_clock, shards_for_clock
from ....core.gamedata.model import GameData
from ....domain.progression.ladder import Rung, SchematicLadder
from ....domain.progression.views import PhaseRequirements, PhaseRow, ShardBudget
from ....presenters.text import primitives as render
from .. import app
from ..params import AsOf, Limit

#: The three views onto a schematic ladder, spelled the same way by both tools that walk
#: one. Adding a fourth here without teaching ``_select`` about it silently shows everything.
LADDER_VIEWS = ("all", "todo", "affordable")

SPENDABLE_NOTE = (
    "cost is checked against spendable stock only: carried, storage containers "
    "and the Dimensional Depot. Machine buffers do not count, and neither do the "
    "crates on the ground -- a crate deletes itself once emptied"
)


def _select(
    rungs: list[Rung],
    wanted: str,
    query: str | None,
    startable: Callable[[Rung], bool] = lambda r: True,
) -> list[Rung]:
    """One ladder view. ``affordable`` keeps BLOCKED rungs, because their bill IS covered
    and they are the rows whose answer is "go and clear the prerequisite first"; anything a
    ladder knows cannot be STARTED today belongs in ``startable`` instead."""
    out = []
    for r in rungs:
        if wanted != "all" and r.done:
            continue
        if query and query.strip().casefold() not in (r.schematic.name or "").casefold():
            continue
        if wanted == "affordable" and (r.missing or r.done or not startable(r)):
            continue
        out.append(r)
    return out


def _bill(g: GameData, rung: Rung) -> str:
    return ", ".join(f"{f.amount:g} {g.item_name(f.item)}" for f in rung.schematic.cost) or "-"


def _shortfall(g: GameData, rung: Rung) -> str:
    return ", ".join(f"{m.short_by:.0f} {g.item_name(m.item)}" for m in rung.missing)


@app.tool()
def phase_requirements(
    save: str | None = None, world: str | None = None, as_of: AsOf = None
) -> str:
    """What the Space Elevator still wants, live record and deprecated record apart.

    The per-phase item table in the save is DEPRECATED and frozen, so it is shown
    labelled rather than believed. Read the header line first.
    """
    st = app.load_world(save, world, as_of)
    g = st.game
    req = st.phase_requirements()
    rows, short_by_phase = _phase_rows(g, req, st.stock())

    target_row = next((r for r in req["phases"] if r["phase"] == req["target_phase"]), None)
    outstanding_total = sum(target_row["outstanding"].values()) if target_row else 0
    paid = req["paid_off_target"]
    deliverable = _deliverable(target_row, short_by_phase.get(req["target_phase"]) or {})

    return render.envelope(
        "\n".join(
            [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("current_phase", req["current_phase"]),
                        ("target_phase", req["target_phase"]),
                        ("outstanding_on_target", outstanding_total or "-"),
                        ("target_deliverable_now", deliverable),
                    ]
                ),
                "delivered to target: "
                + (
                    " + ".join(f"{render.num(a)} {g.item_name(i)}" for i, a in sorted(paid.items()))
                    or "nothing"
                ),
            ]
        ),
        render.table(
            ("phase", "legacy_key", "trust", "outstanding", "done", "items", "have", "short by"),
            rows,
        ),
        _phase_notes(req, paid),
    )


def _phase_rows(
    g: GameData, req: PhaseRequirements, stock: dict[str, float]
) -> tuple[list[tuple], dict]:
    """One row per phase against spendable stock, and each phase's shortfall by item."""
    rows = []
    short_by_phase = {}
    for row in req["phases"]:
        outstanding = row["outstanding"]
        ordered = sorted(outstanding.items(), key=lambda kv: -kv[1])
        short = {i: a - stock.get(i, 0.0) for i, a in ordered if stock.get(i, 0.0) < a}
        short_by_phase[row["phase"] or row["egp"]] = short
        rows.append(
            (
                row["phase"] or f"?({row['egp']})",
                row["egp"],
                row["stale"],
                len(outstanding),
                len(row["complete"]),
                " + ".join(f"{render.num(a)} {g.item_name(i)}" for i, a in ordered) or "-",
                " + ".join(
                    f"{render.num(stock[i])} {g.item_name(i)}" for i, _ in ordered if stock.get(i)
                )
                or "-",
                " + ".join(f"{render.num(a)} {g.item_name(i)}" for i, a in short.items()) or "-",
            )
        )
    return rows, short_by_phase


def _deliverable(target_row: PhaseRow | None, target_short: dict) -> str:
    """Whether the target phase could be delivered now, and which kind of row says so."""
    if target_row is None:
        return ""
    if target_short:
        deliverable = (
            f"no, short on {len(target_short)} of "
            f"{len(target_row['outstanding'])} item(s) -- see the short by column"
        )
    else:
        deliverable = "yes, every outstanding item is in stock"
    # Every row but an untouched one is a frozen snapshot of a cost that may be paid already.
    if target_row["stale"] != "usable":
        deliverable += f" (from a {target_row['stale']} row)"
    return deliverable


def _phase_notes(req: PhaseRequirements, paid: dict[str, float]) -> list[str]:
    """Which of the save's two phase records each number comes from, and how far to trust it."""
    notes = [
        (
            "the per-phase amounts come from mGamePhaseCosts, which FGGamePhaseManager.h "
            "marks 'DEPRECATED Only kept for save compatibility'. It is FROZEN: identical "
            "across all 29 parseable saves of the reference world (180h-316h), including "
            "the session that completed a whole phase. stale=stale rows are wrong."
        ),
        (
            "stale=usable means the phase has never been delivered into "
            "(mTargetGamePhasePaidOffCosts is empty), so its untouched snapshot is still "
            "its true full cost. The first delivery turns that row into stale=derived, "
            "not into stale=stale."
        ),
        (
            "[UNVERIFIED for MidGame/LateGame/FoodCourt] the EGP_* -> GP_Project_Assembly_"
            "Phase_N mapping is not in Docs.json (the UFGGamePhase assets do not ship) nor "
            "joinable in the save (the legacy mGamePhase scalar is absent = EGP_NA = "
            "migrated). EGP_EndGame -> Phase_3 IS measured: at 180-244h play the same world "
            "had target=Phase_3 with paid_off={Versatile Framework 2500}, matching that "
            "key's single settled item. The rest follow by enum order from that anchor."
        ),
    ]
    if any(r["stale"] == "derived" for r in req["phases"]):
        notes.append(
            "stale=derived is that row's frozen snapshot MINUS the live "
            "mTargetGamePhasePaidOffCosts. Only the TARGET row is ever subtracted -- it is "
            "the only phase deliveries can reach, so it is the only row with a live counter "
            "to take off -- and the result is a LOWER bound on what is owed: were the "
            "snapshot itself frozen after some earlier delivery the real bill would be "
            "bigger, so a derived 0 means 'nothing left that this can see'"
        )
    notes.append(
        "have and short by join each phase's outstanding items to spendable stock -- "
        "carried, storage containers and the Dimensional Depot, the same pool mam_research "
        "prices research against. What sits in machines and on belts is NOT counted, so a "
        "phase can be deliverable in practice while this says short"
    )
    if not paid:
        notes.append(
            "nothing has been delivered toward the target phase yet "
            "(mTargetGamePhasePaidOffCosts absent = empty)"
        )
    return notes


@app.tool()
def power_shards(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    plan_machines: int = 0,
    plan_clock: float = 2.5,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """Power Shards held, committed and free, plus what an overclock plan would cost.

    ``plan_machines`` machines at ``plan_clock`` costs shards per machine; the answer
    says whether the free pool covers it.
    """
    st = app.load_world(save, world, as_of)
    budget = st.shard_budget()
    per_shard = max(budget["shard_items"].values()) if budget["shard_items"] else 0.0
    notes = _shard_notes(budget, per_shard)
    if plan_machines:
        notes.append(_shard_plan_note(budget, per_shard, plan_machines, plan_clock))

    window = render.page(limit, offset)
    rows = [
        (h["cls"], render.num(h["clock"]), h["slotted"], h["needed"], h["idle"] or "")
        for h in window.of(budget["holders"])
    ]
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", render.num(budget["free"])),
                ("craftable_from_slugs", render.num(budget["craftable"])),
                ("potential", render.num(budget["potential"])),
                ("committed", budget["committed"]),
                ("owned", render.num(budget["owned"])),
                ("overclocked_buildings", len(budget["holders"])),
            ]
        ),
        render.table(
            ("building", "clock", "slotted", "needed", "idle"),
            rows,
            total=len(budget["holders"]),
            offset=window.start,
            limit=window.size,
        ),
        notes,
    )


def _shard_notes(budget: ShardBudget, per_shard: float) -> list[str]:
    """What the shard budget rests on: the measurement, the slot count, slugs, idle slots."""
    notes = []
    if not budget["measured"]:
        notes.append(
            "[UNVERIFIED] this projection predates schema 9 and carries no "
            "InventoryPotential data, so committed=0 means unknown, not zero"
        )
    notes.append(
        f"a shard adds {render.num(per_shard)} max clock (mExtraPotential, from "
        f"Docs.json) and a building takes {budget['slots_per_building']} of them, so "
        f"max clock is {render.num(max_clock(per_shard))}. The slot count is the only hardcoded "
        "number here -- mPotentialShardSlots is 0 on every building in the dump. [WIKI]"
    )
    if budget["slugs"]:
        held = ", ".join(
            f"{render.num(s['held'])} {s['name']} x{s['each']:g}" for s in budget["slugs"]
        )
        notes.append(
            f"craftable = uncrafted slugs carried, in storage containers or in the "
            f"Dimensional Depot: {held}. The 1/2/5 ratios come from the Power Shard (1)/(2)/(5) "
            "recipes, not from game knowledge. Craftable is POTENTIAL, not free -- "
            "crafting is a manual step"
        )

    if budget["by_place"]:
        where = "; ".join(
            f"{place}: " + ", ".join(f"{render.num(v)} {k}" for k, v in sorted(held.items()))
            for place, held in budget["by_place"].items()
        )
        notes.append(f"where they are -- {where}")

    idle = sum(h["idle"] for h in budget["holders"])
    if idle:
        notes.append(
            f"{idle} shard(s) sit in slots the current clock does not need. A shard "
            "raises the MAXIMUM clock; the slider is set separately, so committed is "
            "read from InventoryPotential and never derived from clock"
        )

    return notes


def _shard_plan_note(budget: ShardBudget, per_shard: float, machines: int, clock: float) -> str:
    """Whether ``machines`` at ``clock`` fit the free shards, the craftable ones, or neither."""
    need_each = shards_for_clock(clock, per_shard)
    need = need_each * machines
    short = need - budget["free"]
    line = (
        f"plan: {machines} machine(s) at clock {render.num(clock)} needs "
        f"{need_each} shard(s) each = {need}; free {render.num(budget['free'])}"
    )
    if short <= 0:
        return line + " -> affordable now"
    if short <= budget["craftable"]:
        # Craftable, not free: the slugs cover it but somebody has to press craft.
        return (
            line + f" -> SHORT by {render.num(short)}, but "
            f"{render.num(budget['craftable'])} more are craftable from slugs you "
            "already hold, so it is affordable after crafting"
        )
    return (
        line + f" -> SHORT by {render.num(short)}; even crafting every slug "
        f"({render.num(budget['craftable'])}) leaves you "
        f"{render.num(need - budget['potential'])} short"
    )


@app.tool()
def mam_research(
    show: Annotated[
        str, Field(description="all | todo | affordable -- todo hides finished research")
    ] = "todo",
    query: Annotated[str | None, Field(description="filter by name, case-insensitive")] = None,
    search: Annotated[str | None, Field(description="retired -- write query= instead")] = None,
    status: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """MAM research: what is left, what it costs, and what you can afford right now.

    The MAM is where CAPABILITIES live, as opposed to recipes -- the Dimensional Depot, the
    Power Augmenter, and the Production Amplifier, which lets a Somersloop go into a
    machine. The save has no flag for that last one, so it is derived from the purchased
    schematics. Rows marked LOCKS gate a tool argument rather than add a recipe.
    """
    if gone := app.retired(("status", status, "show"), ("search", search, "query")):
        return gone
    st = app.load_world(save, world, as_of)

    window = render.page(limit, offset, default=25)
    wanted = (show or "todo").strip().casefold()
    if wanted not in LADDER_VIEWS:
        return f"! unknown show {show!r}. Choose from: all, todo, affordable"

    ladder = SchematicLadder(game=st.game, unlocks=st.unlocks, inventory=st.inventory)
    rungs = ladder.rungs("EST_MAM")
    ongoing = st.research.ongoing
    shut = {r.schematic.cls: st.research.tree_locked(r.schematic.cls) for r in rungs}
    outstanding = [r for r in rungs if not r.done]
    n_running = sum(1 for r in outstanding if r.schematic.cls in ongoing)
    n_shut = sum(1 for r in outstanding if shut[r.schematic.cls])
    # Neither an in-flight node nor one in a shut tree can be started now, whatever the
    # bill says, so affordable does not offer them.
    picked = _select(
        rungs,
        wanted,
        query,
        startable=lambda r: r.schematic.cls not in ongoing and not shut[r.schematic.cls],
    )
    rows = [_research_row(st, rung) for rung in picked]

    return render.envelope(
        f"# {st.age_note}\n# {len(outstanding)} MAM research node(s) outstanding"
        + (f", {n_running} under way" if n_running else "")
        + (f", {n_shut} in an unopened tree" if n_shut else "")
        + f", showing {wanted}",
        render.paged_table(
            ("status", "research", "capability", "cost", "short by", "blocked by"), rows, window
        ),
        _research_notes(st, n_running, n_shut),
    )


def _research_row(st, rung: Rung) -> tuple:
    cls = rung.schematic.cls
    state = st.research.status(rung)
    if state == "RUNNING":
        state = f"RUNNING {st.research.ongoing[cls]:.0f}s"
    gates = {v: k for k, v in CAPABILITY_SCHEMATICS.items()}
    return (
        state,
        rung.schematic.name[:30],
        "LOCKS " + gates[cls] if cls in gates else "",
        _bill(st.game, rung)[:52],
        _shortfall(st.game, rung)[:34],
        ", ".join(rung.blocked_by)[:24],
    )


def _research_notes(st, n_running: int, n_shut: int) -> list[str]:
    """The reading notes, then one line per capability the MAM has not yet granted."""
    notes = [
        (
            "MAM research is where CAPABILITIES live, not just recipes -- 'LOCKS x' marks "
            "one that gates a feature of this MCP rather than adding a recipe"
        ),
        SPENDABLE_NOTE,
    ]
    if n_running:
        notes.append(
            "RUNNING is research already under way: it is paid for and cannot be started "
            "again. The seconds are what the save recorded and do not run down while the "
            "game is closed"
        )
    if n_shut:
        notes.append(
            "TREE SHUT means the MAM tree that node lives in has not been opened yet, so "
            "the node cannot be researched however affordable it is. Which tree a node "
            "belongs to is read off its class id: Docs.json does not ship the research "
            "trees, and the save names only which trees are open"
        )
    if not st.research.knows_trees:
        notes.append(
            "this projection predates the unlocked-tree list, so a node in an unopened "
            "tree is listed here as if it were available -- re-read the save"
        )
    for name in CAPABILITY_SCHEMATICS:
        gate = st.research_gate(name)
        if gate is None:
            continue
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        missing = ", ".join(f"{r['need'] - r['have']:.0f} {r['name']}" for r in gate["short"])
        verdict = "you can afford it now" if gate["affordable"] else f"short of {missing}"
        blocked = (
            " Blocked by " + ", ".join(gate["blocked_by"]) + " first." if gate["blocked_by"] else ""
        )
        notes.append(
            f"{name} is NOT researched: needs {gate['schematic_name']} in the MAM "
            f"({bill}) -- {verdict}.{blocked}"
        )
    return notes


@app.tool()
def milestones(
    show: Annotated[
        str, Field(description="all | todo | affordable -- todo hides finished milestones")
    ] = "todo",
    tier: Annotated[
        int | None, Field(description="one HUB tier, 1-9. Omit for all of them")
    ] = None,
    query: Annotated[str | None, Field(description="filter by name, case-insensitive")] = None,
    search: Annotated[str | None, Field(description="retired -- write query= instead")] = None,
    status: Annotated[str | None, Field(description="retired -- write show= instead")] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """HUB milestones: what is left, what each costs, and what you can afford right now.

    The questions `mam_research` answers about the MAM tree, asked of the other ladder and
    in the same words -- both walk one `SchematicLadder` priced against the same spendable
    stock, so a status here means what it means there.

    READY is about the BILL, not about access: tiers are opened by Space Elevator
    deliveries, that gate is in no shipped data, and `phase_requirements` is where the
    elevator stands.
    """
    if gone := app.retired(("status", status, "show"), ("search", search, "query")):
        return gone
    st = app.load_world(save, world, as_of)

    g = st.game
    window = render.page(limit, offset, default=25)
    wanted = (show or "todo").strip().casefold()
    if wanted not in LADDER_VIEWS:
        return f"! unknown show {show!r}. Choose from: all, todo, affordable"

    ladder = SchematicLadder(game=g, unlocks=st.unlocks, inventory=st.inventory)
    every = sorted(
        ladder.rungs("EST_Milestone"), key=lambda r: (r.schematic.tier, r.schematic.name)
    )
    rungs = every
    if tier is not None:
        rungs = [r for r in every if r.schematic.tier == tier]
        if not rungs:
            tiers = sorted({r.schematic.tier for r in every})
            return f"! no milestones in tier {tier}. Tiers are {tiers[0]}-{tiers[-1]}"
    picked = _select(rungs, wanted, query)
    outstanding = [r for r in rungs if not r.done]
    ready = [r for r in outstanding if r.status == "READY"]

    page = window.of(picked)
    show_blocked = any(r.blocked_by for r in page)
    headers = ["status", "tier", "milestone", "cost", "short by", "unlocks"]
    if show_blocked:
        headers.append("blocked by")
    rows = []
    for rung in page:
        row = [
            rung.status,
            rung.schematic.tier,
            rung.schematic.name,
            _bill(g, rung),
            _shortfall(g, rung),
            len(st.unlocks.schematic_recipes(rung.schematic)) or "",
        ]
        if show_blocked:
            row.append(", ".join(rung.blocked_by))
        rows.append(row)

    prog = st.progression()
    notes = [
        SPENDABLE_NOTE,
        (
            "READY is about the bill, not about access: a HUB tier is opened by delivering "
            "to the Space Elevator, and no milestone schematic in Docs.json carries that "
            "dependency -- so a READY row in a tier above the ones you have bought into may "
            "still be behind an elevator phase. phase_requirements has that half"
        ),
        "'unlocks' counts the recipes a milestone would newly grant; blank means none",
    ]
    if ready:
        notes.append(
            "affordable right now: "
            + ", ".join(f"T{r.schematic.tier} {r.schematic.name}" for r in ready[:5])
        )

    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(outstanding)} milestone(s) outstanding, showing {wanted}"
        + (f" tier={tier}" if tier is not None else "")
        + "\n"
        + render.kv(
            [
                ("highest_complete_tier", prog["highest_complete_tier"]),
                ("by_tier", " ".join(f"{t}:{v}" for t, v in prog["milestones_by_tier"].items())),
                ("affordable_now", len(ready)),
            ]
        ),
        render.table(headers, rows, total=len(picked), offset=window.start, limit=window.size),
        notes,
    )


@app.tool()
def somersloops(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 20,
    offset: int = 0,
) -> str:
    """Somersloops held, slotted and owned -- the sibling of power_shards.

    Free and committed are both exact: slotted ones are counted from `InventoryPotential`,
    the same component as Power Shards, not inverted from a boost multiplier.
    """
    st = app.load_world(save, world, as_of)

    budget = st.sloop_budget()
    gate = st.research_gate("production_boost")
    holders = budget["holders"]
    window = render.page(limit, offset, default=20)
    rows = [
        (
            h["name"],
            h["instance"],
            f"{h['sloops']:.0f}",
            f"{h['boost']:g}x" if h["boost"] else "",
            f"{h['boost_in_save']:g}x" if h["boost_in_save"] else "-",
        )
        for h in window.of(holders)
    ]
    disagree = [
        h
        for h in holders
        if h["boost"] and h["boost_in_save"] and abs(h["boost"] - h["boost_in_save"]) > 1e-6
    ]
    notes = []
    if gate is not None:
        bill = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        notes.append(
            f"PRODUCTION AMPLIFIER IS NOT RESEARCHED, so none of these can go in a machine "
            f"yet. Research {gate['schematic_name']} in the MAM ({bill})"
        )
    notes.append(
        "only FREE sloops can fund a plan; committed ones are counted so you know there "
        "is something to pull out, not added to what is spendable"
    )
    notes.append(
        "free pools carried, storage containers and the Dimensional Depot -- the same set "
        "as power_shards, and never machine buffers or the crates on the ground"
    )
    if not budget["committed_measured"]:
        notes.append(
            "this projection predates schema 10, so slotted sloops are unreadable and "
            "'committed' is unknown rather than zero"
        )
    notes.append(
        "Mercer Spheres share the WAT prefix and do nothing for production, so they are "
        "reported apart and never added in"
    )
    notes.append(
        "boost is what the plan model says those slots are worth; boost_in_save is "
        "mPendingProductionBoost, the multiplier the save itself carries. They are read "
        "from different places on purpose, so the two agreeing is the cross-check"
    )
    if disagree:
        notes.append(
            f"THEY DISAGREE on {len(disagree)} building(s), which is a finding: "
            + "; ".join(
                f"{h['name']} {h['instance']} computed {h['boost']:g}x, "
                f"save says {h['boost_in_save']:g}x"
                for h in disagree[:3]
            )
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("free", f"{budget['free']:.0f}"),
                ("committed", f"{budget['committed']:.0f}"),
                ("owned", f"{budget['owned']:.0f}"),
                ("where", ", ".join(f"{k} {v:.0f}" for k, v in budget["by_place"].items()) or "-"),
                ("mercer_spheres", f"{budget['mercer_spheres']:.0f}"),
            ]
        ),
        render.table(
            ("building", "instance", "sloops", "boost", "boost_in_save"),
            rows,
            total=len(holders),
            offset=window.start,
            limit=window.size,
        ),
        notes,
    )
