"""A layout report as TSV: the stack, and whichever table ``show`` asked for.

Six tables share one header and one pile of caveats, because they are six views of the SAME
schematic -- change ``show`` and the plan underneath does not move. The caveats are what
stop a schematic being read as a blueprint: there is no terrain data here, so routing,
lengths and world coordinates are absent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from ...core.gamedata.model import GameData
from ...domain.planning.layout.head import HeadRow
from ...domain.planning.layout.materials import MaterialsBill
from ...domain.planning.layout.model import Layout
from ...domain.planning.layout.service import LayoutReport
from ...domain.planning.layout.site_partition import SitePlan
from ...domain.planning.layout.trunks import TrunkPlan
from ...domain.planning.readout.slice import slice_of
from ...domain.planning.solver.prepare import PreparedPlan
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["LAYOUT_VIEWS", "render_layout"]

#: Every ``show`` a layout answers; ``floors`` is the default stack.
LAYOUT_VIEWS = ("floors", "blocks", "buses", "trunks", "materials", "sites")

#: What ``show='sites'`` needs, said when it was asked for without ``sites=``.
_SITES_USAGE = (
    (
        'sites maps a name to patterns, e.g. {"rig": ["Heavy Oil '
        'Residue", "Diluted Fuel", "Water Extractor"], "hall": ["MW"]}'
    ),
    (
        "patterns match the same way exclude_recipes does: process "
        "label, building name, or recipe -- plus 'MW'/'power', which "
        "claims every generator, so a generator hall is a site like "
        "any other"
    ),
)


P = TypeVar("P", SitePlan, MaterialsBill, TrunkPlan)


def _prepared(report: LayoutReport) -> PreparedPlan:
    """The plan of a report whose carrier tiers resolved."""
    assert report.prepared is not None, "the carrier tiers did not resolve"
    return report.prepared


def _layout(report: LayoutReport) -> Layout:
    """The schematic of a report whose plan solved."""
    assert report.layout is not None, "the plan did not solve"
    return report.layout


def _payload(report: LayoutReport, kind: type[P]) -> P:
    """The answer to the ``show`` that asked for a ``kind``."""
    payload = report.show_payload
    assert isinstance(payload, kind), f"show answers no {kind.__name__}"
    return payload


def _riser_pumps(report: LayoutReport) -> int:
    """The pipeline pumps the fluid risers need, at least."""
    return sum(row["pumps"] for row in report.climbing)


def _blocks_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    layout = _layout(report)
    rows = [
        (
            b.name[:36],
            f"F{b.stage}",
            b.machines,
            f"{b.clock * 100:.4g}%",
            f"{b.width_m:g}x{b.depth_m:g}",
            b.foundations,
            ", ".join(
                f"{render.num(v)} {g.item_name(k)}"
                for k, v in sorted(b.inputs.items(), key=lambda kv: -kv[1])[:2]
            )
            or "-",
            ", ".join(
                f"{render.num(v)} {g.item_name(k)}"
                for k, v in sorted(b.outputs.items(), key=lambda kv: -kv[1])[:2]
            )
            or "-",
        )
        for b in sorted(layout.blocks, key=lambda b: (b.stage, -b.machines))[
            : render.clamp(limit, default=20)
        ]
    ]
    body = render.table(
        ("block", "floor", "n", "clock", "each(m)", "found", "in/min", "out/min"),
        rows,
        total=len(layout.blocks),
        limit=limit,
    )
    return body, []


def _sites_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    site_plan = _payload(report, SitePlan)
    rows = [
        (
            i.source[:14],
            "->",
            i.target[:14],
            i.name[:20],
            render.num(i.rate),
            f"{i.lines}x {i.carrier}",
        )
        for i in site_plan.interfaces[: render.clamp(limit, default=20)]
    ]
    interfaces = render.table(
        ("from", "", "to", "item", "rate", "carrier"),
        rows,
        total=len(site_plan.interfaces),
        limit=limit,
    )
    body = (
        render.table(
            ("site", "machines", "net_MW"),
            [(x.name, x.machines, render.num(x.net_mw)) for x in site_plan.sites],
        )
        + "\n\n"
        + interfaces
    )
    notes = list(site_plan.notes)
    if site_plan.ok:
        notes.append(
            "every process is assigned to exactly one site, so this interface table "
            "is complete: each flow's destination is stated rather than assumed"
        )
    else:
        notes.append(
            "the partition is INCOMPLETE, so the interface table is missing flows. "
            "This is the error a hand reconciliation makes -- a rig's whole fuel "
            "output looks like it reaches the generators until you notice something "
            "else was drinking it"
        )
    notes.append(
        "a shared flow is split between consumers by SHARE. The LP gives net balances "
        "and never who fed whom, so any exact producer-consumer pairing would be "
        "invented -- the same reason a layout models a bus rather than pairs"
    )
    notes.append(
        "site net_MW excludes the AWESOME Sink charge, which belongs to the plan as a "
        "whole and cannot be attributed to one site"
    )
    return body, notes


def _materials_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    bill = _payload(report, MaterialsBill)
    rows = [
        (
            line.name[:26],
            render.num(line.needed),
            render.num(line.held),
            render.num(line.short) if line.short else "",
            ", ".join(line.wanted_by)[:38],
        )
        for line in bill.lines[: render.clamp(limit, default=20)]
    ]
    table = render.table(
        ("item", "need", "have", "short", "for"),
        rows,
        total=len(bill.lines),
        limit=limit,
    )
    biggest = sorted(bill.buildings, key=lambda b: -b.items)[:3]
    body = (
        f"machines={bill.machines}  foundations={bill.foundations}  "
        f"distinct_parts={len(bill.lines)}\n"
        + "costliest: "
        + ", ".join(f"{b.count}x {b.name} = {b.items:,} parts" for b in biggest)
        + "\n\n"
        + table
    )
    notes = list(bill.notes)
    short = bill.shortfall
    notes.append(
        "you can afford every part of this from stock"
        if not short
        else "short of "
        + render.capped(
            [f"{render.num(x.short)} {x.name}" for x in short], 4, more=", and {n} more"
        )
    )
    notes.append(
        "construction cost only, and NOT the same question as diff_vs_save's cost "
        "table: this prices the WHOLE plan, that one prices what is left to place "
        "and lists only what you are short of"
    )
    notes.append(
        "stock is spendable only -- carried, crates and the Dimensional Depot -- "
        "never machine buffers, which are not carryable"
    )
    riser_pumps = _riser_pumps(report)
    if riser_pumps:
        notes.append(f"includes {riser_pumps} {report.pump_name}(s) for the fluid risers")
    notes.append(
        "belts and pipes are NOT costed: their cost is per metre and there is no "
        "route, so a length here would be invented. Use show='buses' for line "
        "counts and show='trunks' for a straight-line lower bound on the runs"
    )
    notes.append(
        "these are build-gun components, not ore. Call bom on any row to expand it "
        "-- flattening here would have to guess a depth through the Recycled loop"
    )
    return body, notes


def _trunks_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    trunk_plan = _payload(report, TrunkPlan)
    pump_head, pump_name = report.pump_head_m, report.pump_name
    rows: list[tuple[object, ...]] = []
    for i, t in enumerate(trunk_plan.trunks, 1):
        # Head is a FLUID concern only: a belt's climb invites a pump that cannot exist.
        climb = ""
        if t.carrier == "pipe" and abs(t.lift_m) >= 1.0:
            climb = f"{'down' if t.lift_m > 0 else 'UP'} {abs(t.lift_m):.0f}m"
            need = t.pumps(pump_head)
            if need:
                climb += f" ({need}x {pump_name})"
        rows.append(
            (
                f"T{i}",
                t.name[:16],
                len(t.members),
                f"{render.num(t.rate)}/{render.num(t.capacity)}",
                f"{t.used:.0%}",
                f"{t.run_m:.0f}m",
                climb,
                ", ".join(f"{m.short}:{m.purity[:3]}" for m in t.members[:4]),
            )
        )
    body = render.table(
        ("trunk", "item", "nodes", "rate", "full", "run", "head", "nodes tapped"),
        rows,
        total=len(trunk_plan.trunks),
        limit=limit,
    )
    notes = list(trunk_plan.notes)
    notes.append(
        f"trunks converge on {trunk_plan.destination_label}. `run` is the straight-line chain "
        "node to node, so it is a LOWER BOUND on pipe -- no terrain data exists here. "
        "`head` is the climb from the far end inward: UP needs pumping, down does not. "
        f"Pump counts assume {pump_name} at {pump_head:.0f}m head (mDesignPressure) and "
        "are a LOWER bound: pipe friction and the head a full pipe holds on its own are "
        "not modelled"
    )
    for name, rate, count in trunk_plan.placeless:
        notes.append(
            f"{count}x {name} extractor(s) carrying {render.num(rate)}/min sit on no "
            "node, so they get no trunk -- water comes from water volumes, which "
            "carry no geometry here. Site them at the shore and pipe inward"
        )
    return body, notes


def _buses_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    layout = _layout(report)
    rows = [
        (
            b.name[:24],
            f"{render.num(b.rate)}{b.unit}",
            b.carrier,
            b.lines,
            f"F{b.from_stage}->F{b.to_stage}",
            len(b.producers),
            len(b.consumers),
            "leaves site" if b.external else "",
        )
        for b in layout.buses[: render.clamp(limit, default=20)]
    ]
    body = render.table(
        ("item", "rate", "carrier", "lines", "flow", "from", "to", "note"),
        rows,
        total=len(layout.buses),
        limit=limit,
    )
    return body, []


def _floors_view(g: GameData, report: LayoutReport, limit: int) -> tuple[str, list[str]]:
    layout = _layout(report)
    # A site column only when a partition exists: three buildings, each read from its own F0.
    with_site = any(f.site for f in layout.floors)
    rows: list[tuple[object, ...]] = []
    for f in layout.floors:
        row: tuple[object, ...]
        if f.kind == "production":
            contents = ", ".join(
                f"{b.machines}x {b.label[:22]}"
                for b in sorted(f.blocks, key=lambda b: -b.machines)[:2]
            )
            row = (
                f"F{f.index}",
                f"stage {f.stage}",
                len(f.blocks),
                f.machines,
                f"{f.height_m:g}m",
                f.foundations,
                contents,
            )
        else:
            row = (
                f"L{f.index}",
                "logistics",
                len(f.buses),
                "",
                f"{f.height_m:g}m",
                "",
                ", ".join(f"{b.name} {b.lines}x{b.carrier}" for b in f.buses[:4]),
            )
        rows.append((f.site[:14], *row) if with_site else row)
    headers = ("floor", "kind", "n", "machines", "height", "found", "contents")
    body = render.table(
        ("site", *headers) if with_site else headers,
        rows,
        total=len(layout.floors),
        limit=limit,
    )
    notes = [
        (
            'show="blocks" for every module, show="buses" for item flows, '
            'show="trunks" for which nodes share a pipe, show="materials" for '
            "what it costs to build"
        )
    ]
    return body, notes


#: The table each ``show`` other than ``floors`` asks for.
_VIEWS: dict[str, Callable[[GameData, LayoutReport, int], tuple[str, list[str]]]] = {
    "blocks": _blocks_view,
    "sites": _sites_view,
    "materials": _materials_view,
    "trunks": _trunks_view,
    "buses": _buses_view,
}


def _layout_summary(g: GameData, st: WorldState, report: LayoutReport, objective: str) -> str:
    """The header: the plan's size, its floors and stacks, and the carriers it assumed."""
    tiers, layout, prepared = report.tiers, _layout(report), _prepared(report)
    production = [f for f in layout.floors if f.kind == "production"]
    logistics = [f for f in layout.floors if f.kind == "logistics"]
    # Under a site partition each building is named with its own stack height.
    stack = (
        (
            "stacks",
            " + ".join(f"{name} {sub.height_m:g}m" for name, sub in report.site_layouts),
        )
        if report.site_layouts
        else ("stack_height", f"{layout.height_m:g}m")
    )
    return "\n".join(
        [
            f"# layout for {objective} over {prepared.request.selection.description}",
            f"# {st.age_note}",
            render.kv(
                [
                    ("net_MW", render.num(slice_of(prepared, g).net_mw)),
                    ("machines", layout.machines),
                    ("blocks", len(layout.blocks)),
                    ("floors", f"{len(production)} production + {len(logistics)} logistics"),
                    stack,
                ]
            ),
            render.kv(
                [
                    ("peak_floor_foundations", layout.foundations),
                    ("site", f"~{layout.site_side_m():g}x{layout.site_side_m():g}m"),
                    (
                        "carriers",
                        (
                            f"{tiers.belt_tier} belt {render.num(tiers.belt_ipm)}/min, "
                            f"{tiers.pipe_tier} pipe {render.num(tiers.pipe_m3min)}m3/min"
                        ),
                    ),
                ]
            ),
        ]
    )


def _schematic_notes(g: GameData, st: WorldState, report: LayoutReport) -> list[str]:
    """What every view shares: the tiers assumed, what a schematic is not, what to build."""
    tiers, layout = report.tiers, _layout(report)
    notes = [
        *layout.warnings,
        (
            f"carriers are the fastest you have UNLOCKED: {tiers.belt_tier} at "
            f"{render.num(tiers.belt_ipm)}/min and {tiers.pipe_tier} at "
            f"{render.num(tiers.pipe_m3min)} m3/min. "
            "Pass belt_tier/pipe_tier to plan against a different one -- an unlocked tier "
            "assumed rather than checked changes every line count in this schematic"
        ),
    ]
    if report.site_layouts:
        notes.append(
            "sites are SEPARATE buildings: floors are stacked and ordered within each "
            "site independently, so heights and riser pump counts are per site -- "
            "nothing here prices the ground between them"
        )
    notes.append(
        "schematic only: no world coordinates or belt routing -- there is no terrain "
        "data available, so those would be invented"
    )
    needed = {
        b.building_id for b in layout.blocks if b.building_id and st.built(b.building_id) == 0
    }
    if needed:
        notes.append(
            "must build first: "
            + ", ".join(g.buildings[c].name for c in needed if c in g.buildings)
        )
    return notes


def _fit_header(report: LayoutReport, body: str) -> tuple[str, list[str]]:
    """``body`` under the fit against the scoped factory's platform, and the fit's notes."""
    fit = report.fit
    if fit is None:
        return body, []
    head = [
        f"## fit against {report.scope_name}",
        fit.headline(),
        (
            f"blocks: {len(fit.standing)} standing ({fit.machines_standing} machines), "
            f"{len(fit.to_build)} to build ({fit.machines_to_build} machines)"
        ),
    ]
    still = ", ".join(fit.to_build[:8]) if fit.to_build else ""
    if still:
        head.append(f"still to build: {still}")
    return "\n".join(head) + "\n\n" + body, list(fit.notes)


def _riser_notes(report: LayoutReport) -> list[str]:
    """The pumps the risers need, and why fluid head does not order the floors."""
    notes: list[str] = []

    def label(row: HeadRow) -> str:
        return f"{row['site']}: {row['item']}" if row.get("site") else row["item"]

    pumps_total = _riser_pumps(report)
    if pumps_total:
        notes.append(
            f"risers need at least {pumps_total} {report.pump_name}(s): "
            + ", ".join(
                f"{label(d)} {d['lines']}x pipe up {d['metres']:.0f}m = {d['pumps']}"
                for d in report.climbing
                if d["pumps"]
            )
            + ". A LOWER bound -- pipe friction and the head a full pipe holds are not "
            "modelled"
        )
    if report.climbing:
        notes.append(
            "floors follow chain depth, not fluid head: "
            + ", ".join(
                f"{label(d)} climbs {d['floors']} floor(s) at {render.num(d['rate'])}{d['unit']}"
                for d in report.climbing[:4]
            )
            + ". Water can only be drawn at sea level, so putting its extractors at the "
            "bottom with consumers above lets the rest of the stack fall instead"
        )
    return notes


def render_layout(
    g: GameData,
    st: WorldState,
    report: LayoutReport,
    *,
    objective: str,
    show: str,
    limit: int,
    plan_name: str = "",
    plan_notes: list[str] | None = None,
) -> str:
    tiers = report.tiers
    if tiers.errors:
        return render.envelope("# unknown carrier tier", "", tiers.errors)
    prepared = _prepared(report)
    if prepared.failure:
        suffix = " -- nothing to lay out" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )
    if show == "sites" and report.show_payload is None:
        return render.envelope("# show='sites' needs sites=", "", list(_SITES_USAGE))

    summary = _layout_summary(g, st, report, objective)
    notes = _schematic_notes(g, st, report)
    body, view_notes = _VIEWS.get(show, _floors_view)(g, report, limit)
    notes += view_notes

    plan_notes = [*(plan_notes or [])]
    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]
    body, fit_notes = _fit_header(report, body)
    plan_notes += fit_notes
    notes += _riser_notes(report)
    return render.envelope(summary, body, [*plan_notes, *notes])
