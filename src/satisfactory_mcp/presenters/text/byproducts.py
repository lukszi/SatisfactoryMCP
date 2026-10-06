"""The byproduct diagnostic as compact TSV.

``analyse`` does the solving and hands back a ``ByproductReport``; everything below only
decides how it reads. The report already carries the objective, its unit, the scope
description and the age note, so this module never re-derives a fact.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.analysis.byproducts import Blocker, ByproductReport
from . import primitives as render

__all__ = ["render_byproducts"]


def _value_or_infeasible(value: float | None, unit: str) -> str:
    return f"{render.num(value)} {unit}" if value is not None else "infeasible"


def _form_of(b: Blocker) -> str:
    if b.is_fluid:
        return "FLUID -- cannot be sunk, must be consumed exactly or packaged"
    return f"solid, {b.sink_points} sink pts" if b.sinkable else "solid, NOT sinkable"


def _also_stuck_note(report: ByproductReport) -> str:
    return "surplus with no outlet but no effect here: " + ", ".join(
        f"{n} {render.num(r)}/min" for n, r in report.also_stuck[:4]
    )


def _no_blocker(report: ByproductReport, now: str) -> str:
    """Nothing is stuck: say so, and what the Sink alone is absorbing."""
    unit = report.unit
    notes = list(report.notes)
    head = [f"# no dead-end byproduct: everything this scope makes has an outlet. now {now}."]
    if report.sink_only:
        head.append(
            "# only the AWESOME Sink absorbs "
            + render.kv([(k, render.num(v) + "/min") for k, v in report.sink_only.items()])
            + " -- without sinking this scope is worth "
            + _value_or_infeasible(report.no_sink_value, unit)
        )
        notes.append(
            "sinking is a real belt and 30 MW per Sink: give those items a consumer or "
            "an export if you would rather keep them"
        )
    if report.also_stuck:
        notes.append(_also_stuck_note(report))
    if report.base_value is None:
        # "No byproduct is stuck" about an infeasible plan is half an answer.
        notes.append(
            "the plan is infeasible for some other reason -- check export_minimums "
            "against what this scope can actually supply, and that the recipes you "
            "need have their building unlocked"
        )
    return render.envelope(
        "\n".join([*head, f"# {report.scope} [plan {report.plan_id}]", f"# {report.age_note}"]),
        "",
        notes,
    )


def _lead_line(report: ByproductReport, top: Blocker, now: str) -> str:
    """The finding: stuck, surplus but harmless, made but used up, or not made at all."""
    best = next((f for f in top.fixes if f.gain), None)
    verdict = (
        f"{_value_or_infeasible(best.value, report.unit)} once fixed"
        if best
        else "nothing here rescues it"
    )
    if top.confirmed:
        return (
            f"# STUCK: {top.name} {render.num(top.rate)}/min has no outlet -- "
            f"{report.objective} is {now}, {verdict}."
        )
    if top.rate > 0:
        return (
            f"# {top.name} {render.num(top.rate)}/min is surplus but is NOT what blocks this "
            f"plan ({report.objective} is {now})."
        )
    if top.producers:
        # "Not produced in this scope" would contradict the next line, which names the
        # recipe it comes from.
        return f"# {top.name} is made in this scope but none is left over. What consumes it:"
    return f"# {top.name} is not produced in this scope. Here is what would consume it."


def _blocker_notes(game: GameData, report: ByproductReport, top: Blocker) -> list[str]:
    notes = list(report.notes)
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
    if len(report.blockers) > 1:
        notes.append(
            "also stuck: "
            + ", ".join(f"{b.name} {render.num(b.rate)}/min" for b in report.blockers[1:4])
        )
    if report.also_stuck:
        notes.append(_also_stuck_note(report))
    return notes


def render_byproducts(game: GameData, report: ByproductReport, *, limit: int) -> str:
    """Compact answer to 'which byproduct stalls this plan, and what would eat it'."""
    now = _value_or_infeasible(report.base_value, report.unit)
    if not report.blockers:
        return _no_blocker(report, now)

    top = report.blockers[0]
    summary = [
        _lead_line(report, top, now),
        (
            f"# {_form_of(top)}. from {top.producers[0] if top.producers else 'this scope'}. "
            f"outlets: {len(top.unlocked_outlets)} unlocked / {len(top.locked_outlets)} locked"
            # Only worth the characters when nothing the save owns touches the item.
            + ("; nothing you own consumes it" if not top.allowed_consumers else "")
            + "."
        ),
        f"# {report.scope} [plan {report.plan_id}]",
        # Never omitted: the save is a live autosave, so a rate needs its provenance.
        f"# {report.age_note}",
    ]

    rows = render.clamp(limit, default=6)
    if top.confirmed:
        body = render.table(
            ("fix", report.objective, "how"),
            [
                (
                    f.label,
                    f"{now} -> {_value_or_infeasible(f.value, report.unit)}"
                    if f.gain
                    else "no gain",
                    f.detail,
                )
                for f in top.fixes[:rows]
            ],
            total=len(top.fixes),
            limit=limit,
        )
    else:
        # Nothing to price, so answer who eats it and which of those the save owns.
        body = render.table(
            ("outlet", "building", "eats/min", "status"),
            [
                (o.name, o.building, render.num(-o.net_rate), o.source or "unlocked")
                for o in top.outlets[:rows]
            ],
            total=len(top.outlets),
            limit=limit,
        )

    footer = render.ids_footer([(b.name, b.item) for b in report.blockers[:3]])
    return render.envelope(
        "\n".join(summary),
        body + ("\n" + footer if footer else ""),
        _blocker_notes(game, report, top),
    )
