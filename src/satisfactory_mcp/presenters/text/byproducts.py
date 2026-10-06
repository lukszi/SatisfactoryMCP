"""The byproduct diagnostic as compact TSV.

``analyse`` does the solving and hands back a ``ByproductReport``; everything below only
decides how it reads. The report already carries the objective, its unit, the scope
description and the age note, so this module never re-derives a fact.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.analysis.byproducts import Blocker, ByproductReport, analyse
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["explain"]


def _fmt(value: float | None, unit: str) -> str:
    return f"{render.num(value)} {unit}" if value is not None else "infeasible"


def _form_of(b: Blocker) -> str:
    if b.is_fluid:
        return "FLUID -- cannot be sunk, must be consumed exactly or packaged"
    return f"solid, {b.sink_points} sink pts" if b.sinkable else "solid, NOT sinkable"


def explain(
    game: GameData,
    state: WorldState,
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    item: str | None = None,
    exclude_recipes: list[str] | None = None,
    limit: int = 6,
) -> str:
    """Compact answer to 'which byproduct stalls this plan, and what would eat it'."""
    rep = analyse(
        game,
        state,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        item=item,
        exclude_recipes=exclude_recipes,
    )
    unit = rep.unit
    now = _fmt(rep.base_value, unit)
    notes = list(rep.notes)

    if not rep.blockers:
        head = [f"# no dead-end byproduct: everything this scope makes has an outlet. now {now}."]
        if rep.sink_only:
            head.append(
                "# only the AWESOME Sink absorbs "
                + render.kv([(k, render.num(v) + "/min") for k, v in rep.sink_only.items()])
                + f" -- without sinking this scope is worth {_fmt(rep.no_sink_value, unit)}"
            )
            notes.append(
                "sinking is a real belt and 30 MW per Sink: give those items a consumer or "
                "an export if you would rather keep them"
            )
        if rep.also_stuck:
            notes.append(_also_stuck_note(rep))
        if rep.base_value is None:
            # Saying "no byproduct is stuck" about an infeasible plan is only half an
            # answer; the other half is where to look instead.
            notes.append(
                "the plan is infeasible for some other reason -- check export_minimums "
                "against what this scope can actually supply, and that the recipes you "
                "need have their building unlocked"
            )
        return render.envelope(
            "\n".join([*head, f"# {rep.scope} [plan {rep.plan_id}]", f"# {rep.age_note}"]),
            "",
            notes,
        )

    top = rep.blockers[0]
    best = next((f for f in top.fixes if f.gain), None)
    verdict = f"{_fmt(best.value, unit)} once fixed" if best else "nothing here rescues it"
    if top.confirmed:
        lead = (
            f"# STUCK: {top.name} {render.num(top.rate)}/min has no outlet -- {objective} "
            f"is {now}, {verdict}."
        )
    elif top.rate > 0:
        lead = (
            f"# {top.name} {render.num(top.rate)}/min is surplus but is NOT what blocks this "
            f"plan ({objective} is {now})."
        )
    elif top.producers:
        # "not produced in this scope" would contradict the very next line, which
        # names the recipe it comes from. What is actually true is that none of it is
        # left over, which is a different -- and reassuring -- answer.
        lead = f"# {top.name} is made in this scope but none is left over. What consumes it:"
    else:
        lead = f"# {top.name} is not produced in this scope. Here is what would consume it."
    summary = [
        lead,
        (
            f"# {_form_of(top)}. from {top.producers[0] if top.producers else 'this scope'}. "
            f"outlets: {len(top.unlocked_outlets)} unlocked / {len(top.locked_outlets)} locked"
            # Only worth the characters when it is zero, which is the blunt case:
            # nothing the save owns touches this item at all.
            + ("; nothing you own consumes it" if not top.allowed_consumers else "")
            + "."
        ),
        f"# {rep.scope} [plan {rep.plan_id}]",
        # Never omitted: the save is a live autosave that rotates every ~5 minutes,
        # so a rate quoted without its provenance is a number with no shelf life.
        f"# {rep.age_note}",
    ]

    rows = render.clamp(limit, default=6)
    if top.confirmed:
        body = render.table(
            ("fix", objective, "how"),
            [
                (f.label, f"{now} -> {_fmt(f.value, unit)}" if f.gain else "no gain", f.detail)
                for f in top.fixes[:rows]
            ],
            total=len(top.fixes),
            limit=limit,
        )
    else:
        # Nothing to price, so answer the question that was actually asked: who eats
        # it, and which of those you already own.
        body = render.table(
            ("outlet", "building", "eats/min", "status"),
            [
                (o.name, o.building, render.num(-o.net_rate), o.source or "unlocked")
                for o in top.outlets[:rows]
            ],
            total=len(top.outlets),
            limit=limit,
        )

    if top.loop is not None and top.loop.absorbs_nothing:
        notes.append(
            f"LOOP: {' + '.join(top.loop.items)} only feed each other "
            f"({', '.join(top.loop.recipes)}) and that pair "
            f"{'net-CREATES' if top.loop.net_creates else 'cannot reduce'} them -- it can "
            f"never absorb {top.name}"
        )
    if top.is_fluid and top.packaging is not None:
        packed = game.items[top.packaging.products[0]]
        notes.append(
            f"only solid disposal: Packager -> {packed.name} ({packed.sink_points} pts, sinkable)"
            + ("" if top.packaging.unlocked else f" -- LOCKED, {top.packaging.source}")
        )
    if len(rep.blockers) > 1:
        notes.append(
            "also stuck: "
            + ", ".join(f"{b.name} {render.num(b.rate)}/min" for b in rep.blockers[1:4])
        )
    if rep.also_stuck:
        notes.append(_also_stuck_note(rep))

    footer = render.ids_footer([(b.name, b.item) for b in rep.blockers[:3]])
    return render.envelope("\n".join(summary), body + ("\n" + footer if footer else ""), notes)


def _also_stuck_note(rep: ByproductReport) -> str:
    return "surplus with no outlet but no effect here: " + ", ".join(
        f"{n} {render.num(r)}/min" for n, r in rep.also_stuck[:4]
    )
