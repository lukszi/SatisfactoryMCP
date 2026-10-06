"""A layout report as TSV: the stack, and whichever table ``show`` asked for.

Six tables share one header and one pile of caveats, because they are six views of the SAME
schematic -- change ``show`` and the plan underneath does not move. The caveats are what
stop a schematic being read as a blueprint: there is no terrain data here, so routing,
lengths and world coordinates are absent.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.layout.service import LayoutReport
from ...domain.planning.readout.slice import slice_of
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["render_layout"]


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
    prepared = report.prepared
    if prepared.failure:
        suffix = " -- nothing to lay out" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )
    req = prepared.request
    sel = req.selection
    lay = report.lay
    plan_notes = [*(plan_notes or [])]

    production = [f for f in lay.floors if f.kind == "production"]
    logistics = [f for f in lay.floors if f.kind == "logistics"]

    tier_note = (
        f"carriers are the fastest you have UNLOCKED: {tiers.belt_tier} at "
        f"{render.num(tiers.belt_ipm)}/min and {tiers.pipe_tier} at "
        f"{render.num(tiers.pipe_m3min)} m3/min. "
        "Pass belt_tier/pipe_tier to plan against a different one -- an unlocked tier "
        "assumed rather than checked changes every line count in this schematic"
    )

    # Under a site partition the floors are per-site stacks, so one summed height would
    # describe a tower nobody is building -- each building is named with its own.
    stack = (
        (
            "stacks",
            " + ".join(f"{name} {sub.height_m:g}m" for name, sub in report.site_layouts),
        )
        if report.site_layouts
        else ("stack_height", f"{lay.height_m:g}m")
    )
    summary = "\n".join(
        [
            f"# layout for {objective} over {sel.description}",
            f"# {st.age_note}",
            render.kv(
                [
                    ("net_MW", render.num(slice_of(prepared, g).net_mw)),
                    ("machines", lay.machines),
                    ("blocks", len(lay.blocks)),
                    ("floors", f"{len(production)} production + {len(logistics)} logistics"),
                    stack,
                ]
            ),
            render.kv(
                [
                    ("peak_floor_foundations", lay.foundations),
                    ("site", f"~{lay.site_side_m():g}x{lay.site_side_m():g}m"),
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

    notes = [*lay.warnings, tier_note]
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
    needed = {b.building_id for b in lay.blocks if b.building_id and st.built(b.building_id) == 0}
    if needed:
        notes.append(
            "must build first: "
            + ", ".join(g.buildings[c].name for c in needed if c in g.buildings)
        )

    if show == "blocks":
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
            for b in sorted(lay.blocks, key=lambda b: (b.stage, -b.machines))[
                : render.clamp(limit, default=20)
            ]
        ]
        body = render.table(
            ("block", "floor", "n", "clock", "each(m)", "found", "in/min", "out/min"),
            rows,
            total=len(lay.blocks),
            limit=limit,
        )
    elif show == "sites":
        sp = report.show_payload
        if sp is None:
            return render.envelope(
                "# show='sites' needs sites=",
                "",
                [
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
                ],
            )
        rows = [
            (
                i.source[:14],
                "->",
                i.target[:14],
                i.name[:20],
                render.num(i.rate),
                f"{i.lines}x {i.carrier}",
            )
            for i in sp.interfaces[: render.clamp(limit, default=20)]
        ]
        body = render.table(
            ("from", "", "to", "item", "rate", "carrier"),
            rows,
            total=len(sp.interfaces),
            limit=limit,
        )
        body = (
            render.table(
                ("site", "machines", "net_MW"),
                [(x.name, x.machines, render.num(x.net_mw)) for x in sp.sites],
            )
            + "\n\n"
            + body
        )
        notes.extend(sp.notes)
        if sp.ok:
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
    elif show == "materials":
        bill = report.show_payload
        riser_pumps = sum(row["pumps"] for row in report.climbing)
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
        body = render.table(
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
            + body
        )
        notes.extend(bill.notes)
        short = bill.shortfall
        notes.append(
            "you can afford every part of this from stock"
            if not short
            else "short of "
            + ", ".join(f"{render.num(x.short)} {x.name}" for x in short[:4])
            + (f", and {len(short) - 4} more" if len(short) > 4 else "")
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
        if riser_pumps:
            notes.append(
                f"includes {riser_pumps} {report.pump_name}(s) for the fluid risers -- these "
                "were missing entirely, so a fluid-heavy plan used to understate its own bill"
            )
        notes.append(
            "belts and pipes are NOT costed: their cost is per metre and there is no "
            "route, so a length here would be invented. Use show='buses' for line "
            "counts and show='trunks' for a straight-line lower bound on the runs"
        )
        notes.append(
            "these are build-gun components, not ore. Call bom on any row to expand it "
            "-- flattening here would have to guess a depth through the Recycled loop"
        )
    elif show == "trunks":
        tp = report.show_payload
        pump_head, pump_name = report.pump_head_m, report.pump_name
        rows = []
        for i, t in enumerate(tp.trunks, 1):
            # Head is a FLUID concern only. A belt does not care that its coal climbs
            # 218 m, and printing a number there invites a pump that cannot exist.
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
            total=len(tp.trunks),
            limit=limit,
        )
        notes.extend(tp.notes)
        notes.append(
            f"trunks converge on {tp.destination_label}. `run` is the straight-line chain "
            "node to node, so it is a LOWER BOUND on pipe -- no terrain data exists here. "
            "`head` is the climb from the far end inward: UP needs pumping, down does not. "
            f"Pump counts assume {pump_name} at {pump_head:.0f}m head (mDesignPressure) and "
            "are a LOWER bound: pipe friction and the head a full pipe holds on its own are "
            "not modelled"
        )
        for name, rate, count in tp.placeless:
            notes.append(
                f"{count}x {name} extractor(s) carrying {render.num(rate)}/min sit on no "
                "node, so they get no trunk -- water comes from water volumes, which "
                "carry no geometry here. Site them at the shore and pipe inward"
            )
    elif show == "buses":
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
            for b in lay.buses[: render.clamp(limit, default=20)]
        ]
        body = render.table(
            ("item", "rate", "carrier", "lines", "flow", "from", "to", "note"),
            rows,
            total=len(lay.buses),
            limit=limit,
        )
    else:
        # A site column only when a partition exists: it separates "three buildings, read
        # each stack from its own F0" from one fused tower.
        with_site = any(f.site for f in lay.floors)
        rows = []
        for f in lay.floors:
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
            total=len(lay.floors),
            limit=limit,
        )
        notes.append(
            'show="blocks" for every module, show="buses" for item flows, '
            'show="trunks" for which nodes share a pipe, show="materials" for '
            "what it costs to build"
        )

    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]

    if report.fit is not None:
        fit = report.fit
        still = ", ".join(fit.to_build[:8]) if fit.to_build else ""
        head = [
            f"## fit against {report.scope_name}",
            fit.headline(),
            (
                f"blocks: {len(fit.standing)} standing ({fit.machines_standing} machines), "
                f"{len(fit.to_build)} to build ({fit.machines_to_build} machines)"
            ),
        ]
        if still:
            head.append(f"still to build: {still}")
        body = "\n".join(head) + "\n\n" + body
        plan_notes = [*plan_notes, *fit.notes]

    def _riser_label(d: dict) -> str:
        return f"{d['site']}: {d['item']}" if d.get("site") else d["item"]

    pumps_total = sum(row["pumps"] for row in report.climbing)
    if pumps_total:
        notes.append(
            f"risers need at least {pumps_total} {report.pump_name}(s): "
            + ", ".join(
                f"{_riser_label(d)} {d['lines']}x pipe up {d['metres']:.0f}m = {d['pumps']}"
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
                f"{_riser_label(d)} climbs {d['floors']} floor(s) at "
                f"{render.num(d['rate'])}{d['unit']}"
                for d in report.climbing[:4]
            )
            + ". Water can only be drawn at sea level, so putting its extractors at the "
            "bottom with consumers above lets the rest of the stack fall instead"
        )

    return render.envelope(summary, body, [*plan_notes, *notes])
