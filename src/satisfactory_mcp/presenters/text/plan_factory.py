"""A solved factory plan as TSV: the build table, the flows, and the caveats.

Almost all of this file is caveat, because an LP will happily hand back a plan that needs
research the player has not bought, shards they do not hold, or clocks that quietly cost
hundreds of MW, and none of that is visible in the numbers themselves.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.readout import payback
from ...domain.planning.readout.report import PlanFactoryReport
from ...domain.planning.readout.slice import grid_import_mw, linear_gap_note
from ...domain.planning.readout.summary import power_view
from ...domain.planning.solver.model import MW
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["render_plan_factory"]


def _clock(row: dict) -> str:
    if row.get("last_clock") is None:
        return f"{row['clock'] * 100:.4g}%"
    return f"100%, last {row['last_clock'] * 100:.4g}%"


def _water_bound(report: PlanFactoryReport) -> str:
    """How the extractor ceiling was arrived at, and whether it is holding the plan down."""
    cap = report.water_cap
    if report.water_cap_given:
        return f"capped at {cap}, the number you measured and passed"
    if report.water_binding:
        return (
            f"capped at {cap} by the WATER_EXTRACTOR_CAP_ASSUMED default, and that cap is "
            f"BINDING: the solve took every extractor it was allowed, so this answer is "
            f"held down by an assumption rather than by your world"
        )
    return (
        f"ASSUMED, not measured -- bounded at {cap} by the WATER_EXTRACTOR_CAP_ASSUMED "
        f"default, which is not what binds here"
    )


def _water_evidence(report: PlanFactoryReport, site) -> str:
    """What was actually measured about water at this plan's site, if it has one.

    Every branch reports a distance, a level or a share of ground; none reports a capacity.
    Turning submerged area into an extractor count would need shoreline geometry, clearance
    and overlap rules, and none of the three is in this field.
    """
    if site is None:
        return (
            " This plan is NOT SITED, so its water was assumed and not measured: pass "
            "site_at=<where it will stand> to have the terrain there read instead."
        )
    coords = f"{site.x_m:g},{site.y_m:g}"
    named = "" if site.origin_label in ("", coords) else f"{site.origin_label} "
    where = f" Sited at {named}({coords} m)"
    sw = report.site_water
    if sw is None:
        return f"{where}, but this machine carries no terrain field, so nothing was measured."
    pad = f"{site.width_m:g}x{site.depth_m:g} m pad"
    if sw.pad.submerged_pct > 0:
        body = f"{sw.pad.submerged_pct:g}% of the {pad} stands under water at {sw.level_m:.1f} m"
    elif sw.distance_m is not None:
        body = (
            f"the {pad} is dry and the nearest standing water is {sw.distance_m:g} m away, "
            f"surface {sw.level_m:.1f} m"
        )
    else:
        radius = sw.near.radius_m if sw.near else 0.0
        return (
            f"{where}, and MEASURED: no standing water within {radius:g} m of the {pad}, so "
            "every m3 this plan drinks has to be piped in from further out."
        )
    drop = (
        ", and the pad keeps no dry ground to measure a drop against"
        if sw.below_ground_m is None
        else f", {sw.below_ground_m:.1f} m below its dry ground"
    )
    body += " (this world's sea level)" if sw.at_sea_level else ""
    coarse = ""
    if sw.pad.nodata_pct >= 10.0 or sw.pad.coarse_pct >= 50.0:
        coarse = (
            f" ({sw.pad.nodata_pct:g}% of the pad has no terrain data and "
            f"{sw.pad.coarse_pct:g}% is the 3.9 m fill layer, so read those to the metre "
            "at best)"
        )
    return f"{where}, and MEASURED: {body}{drop}.{coarse}"


def _build_rows(report: PlanFactoryReport, limit: int) -> list[tuple]:
    """The build table: one row per process, BUILD where its building is not built yet."""
    return [
        (
            p["machines"],
            _clock(p),
            p["label"][:42],
            p["building"][:20],
            render.num(p["mw"]),
            "BUILD" if p["building_id"] in report.needed_buildings else "",
        )
        for p in report.prepared.solution.processes[: render.clamp(limit, default=15)]
    ]


def _zero_export_notes(report: PlanFactoryReport, objective: str) -> list[str]:
    """A named export coming out at zero, said first: "766 Plastic" otherwise reads as success."""
    notes = []
    for z in report.zero_exports:
        name = z["name"]
        if z["produced"] > 1e-6:
            fate = "consumed inside the plan as an intermediate"
            if z["sunk"] > 1e-6:
                fate += f" or sunk ({render.num(z['sunk'])}/min)"
            why = f"all {render.num(z['produced'])}/min made is {fate}"
        elif not z["makeable"]:
            why = "nothing in scope can make it -- the note naming what is missing says why"
        else:
            why = (
                "nothing in the plan makes it: an export is a whitelist, not a demand, "
                f"and objective {objective!r} earns nothing from it, so zero is the optimum"
            )
        notes.append(
            f"EXPORT AT ZERO: {name} is named in exports but 0/min leaves this plan -- "
            f"{why}. Pass export_minimums={{{name!r}: <rate>}} to require it"
        )
    return notes


def _water_note(report: PlanFactoryReport) -> str:
    """How many Water Extractors, what bounds the count, and where they would stand."""
    water = report.water
    if water is None:
        return ""
    n_water = report.water_pumps
    size, block, pier = water["size"], water["block"], water["pier"]
    space = ""
    if size:
        space = (
            f" Each is {size}, so {n_water} of them pack into {block} "
            f"({block.foundations:,} foundations, {block.foundations * 5:,} Concrete), "
            f"or a single pier {pier.width_m:,.0f}x{pier.depth_m:,.0f} m "
            f"({pier.foundations:,} foundations). Counting each machine's "
            f"{size.foundations} foundations separately would say "
            f"{n_water * size.foundations:,} -- that ignores shared edges."
        )
    existing = ""
    if water["pumps"]:
        bodies = ", ".join(f"{k} ({n})" for k, n in list(water["volumes"].items())[:3])
        existing = (
            f" You already run {water['pumps']} pump(s) across "
            f"{len(water['volumes'])} distinct water bod(ies): {bodies}."
        )
        if water["sea_level_m"] is not None:
            existing += (
                f" They all sit at {water['sea_level_m']:.1f}m"
                f" (spread {water['sea_level_span_m']:.2f}m), which is this world's"
                " sea level and the height any new pump has to be drawn at."
            )
    return (
        f"{n_water} Water Extractor(s): the COUNT is {_water_bound(report)} -- a water "
        "volume's shape is level geometry and is not in the save, so nothing here "
        "knows how many a body of water holds. Shoreline is NOT the limit: pumps sit "
        "on platforms built out over open water, so only area and concrete cost "
        "anything. What does bind is vertical -- water is the only fluid that must be "
        f"drawn at sea level and cannot be gravity-fed, so it sets deck order."
        f"{_water_evidence(report, report.prepared.request.site)}{space}"
        f"{existing} Pass water_extractors=<what your site holds> for a measured limit"
    )


def _shard_note(report: PlanFactoryReport) -> str:
    """The power shards the clocks need, against those held and craftable."""
    bill = report.bill
    if not bill.shard_rows:
        return ""
    shard_budget = report.shard_budget
    detail = ", ".join(
        f"{r.machines}x {r.label.split(' on ')[0]} @{r.clock:.0%} = {r.total}"
        for r in bill.shard_rows[:4]
    )
    verdict = (
        "already free"
        if bill.shards <= shard_budget["free"]
        else "affordable after crafting slugs"
        if bill.shards <= shard_budget["potential"]
        else f"SHORT by {bill.shards - shard_budget['potential']:.0f}"
    )
    return (
        f"power shards: {bill.shards} needed ({detail}); you hold "
        f"{shard_budget['free']:.0f} free + {shard_budget['craftable']:.0f} craftable "
        f"= {shard_budget['potential']:.0f} -- {verdict}"
    )


def _sloop_notes(report: PlanFactoryReport) -> list[str]:
    """Whether somersloops can be slotted at all, and what the plan spent or left empty."""
    bill = report.bill
    sloops_asked = report.sloops_asked
    notes = []
    aside = (
        f" A further {bill.unboostable_slots} slot(s) sit in generators and extractors, "
        "which this model cannot production-boost, so they are not counted as capacity."
        if bill.unboostable_slots
        else ""
    )
    gate = report.sloop_gate
    if gate is not None:
        bill_line = ", ".join(f"{r['need']:g} {r['name']}" for r in gate["cost"])
        verdict = (
            "you can afford that now"
            if gate["affordable"]
            else "short of "
            + ", ".join(f"{r['need'] - r['have']:.0f} {r['name']}" for r in gate["short"])
        )
        notes.append(
            f"sloops={sloops_asked} but PRODUCTION AMPLIFIER IS NOT RESEARCHED, so no somersloop "
            f"can go in a machine yet and this plan is not buildable as printed. Research "
            f"{gate['schematic_name']} in the MAM ({bill_line}) -- {verdict}. "
            "The save carries no flag for this; it is read from your purchased schematics"
        )
    if bill.sloop_used_rows:
        spent = ", ".join(
            f"{r.machines}x{r.slots_each} in {r.label[:26]} = {r.total} ({r.boost:g}x)"
            for r in bill.sloop_used_rows[:4]
        )
        held = report.sloop_budget
        # The LP spends against machine-equivalents and the table rounds up to whole
        # machines, so an honest bill can exceed the budget it was solved under.
        over = (
            f" -- ROUNDING UP to whole machines needs {bill.sloops_used - sloops_asked} more "
            f"than the budget of {sloops_asked}; drop a machine or raise it"
            if bill.sloops_used > sloops_asked
            else ""
        )
        short = (
            f" You hold {held['free']:.0f} free, so this is SHORT by "
            f"{bill.sloops_used - held['free']:.0f}."
            if bill.sloops_used > held["free"]
            else f" You hold {held['free']:.0f} free."
        )
        # Only FREE sloops pay for a plan; the committed ones are shown, never added in.
        unmeasured = (
            f" A further {held['committed']:.0f} sit in {len(held['holders'])} machine(s) "
            "and would have to be pulled out first."
            if held["committed_measured"] and held["committed"]
            else ""
            if held["committed_measured"]
            else " Slotted sloops are unreadable on this projection (pre-schema-10), so "
            "'held' may undercount what you own."
        )
        spare = (
            f" {bill.sloop_slots} boostable slot(s) are still empty, so a bigger budget "
            "has somewhere to go."
            if bill.sloop_slots
            else ""
        )
        notes.append(
            f"somersloops: {bill.sloops_used} spent ({spent}){over}.{short}{unmeasured}"
            f"{spare}{aside}"
        )
    elif bill.sloop_rows:
        top = bill.sloop_rows[0]
        why = (
            " The budget bought nothing here: every boost costs 4x power for 2x output, "
            "and this plan is power-limited."
            if sloops_asked
            else " Reported, not spent -- pass sloops=<how many you will commit> to "
            "let the solver use them"
        )
        notes.append(
            f"somersloops: {bill.sloop_slots} boostable slot(s), none used. Filling "
            f"{top.label[:28]} ({top.machines}x{top.slots_each}={top.total}) would run "
            f"it at {top.boost:g}x output for 4x power, halving that block."
            f"{aside}{why}"
        )
    return notes


def _caveat_notes(g: GameData, report: PlanFactoryReport, objective: str) -> list[str]:
    """What the numbers cannot show: free inputs, overclocked clocks, the binding limits."""
    prepared = report.prepared
    req, sol, bill = prepared.request, prepared.solution, report.bill
    notes = []
    # Supplied items are free here: right for a module, wrong for a whole-plant comparison.
    if req.scenario.raw_caps:
        given = ", ".join(
            f"{v:g} {g.item_name(k)}/min" for k, v in sorted(req.scenario.raw_caps.items())
        )
        notes.append(
            f"MODULE PLAN -- {given} arrive free, so this net_MW is NOT comparable with a "
            "whole-plant plan: the cost of making them is charged wherever they are made. "
            "Whatever produces them must export at least these rates, or the chain does "
            "not balance and the surplus you think you have is not there"
        )
    if (
        objective in ("max_item", "min_raw", "min_machines")
        and report.overclocked
        and sol.net_mw < 0
    ):
        worst = max(report.overclocked, key=lambda p: p["clock"])
        notes.append(
            f"objective {objective!r} does not price POWER, and phase 2 breaks ties by "
            f"minimising machines -- so clocks are pushed up ({worst['machines']}x "
            f"{worst['label'][:28]} at {worst['clock']:.0%}) and power rises as clock^1.32. "
            "If this plan feeds a power plant, solve it as min_power with "
            "export_minimums instead; the same output on more machines can cost "
            "hundreds of MW less"
        )
    if req.excluded:
        notes.append("excluded by request: " + ", ".join(req.excluded))
    if gap := linear_gap_note(sol, bill):
        notes.append(gap)
    if not prepared.audit_ok:
        notes.append(f"GUARD FAILED: free-lunch audit returned {prepared.audit_value} MW, not 0")
    for b in sol.binding[:6]:
        notes.append(f"binding: {b}")
    if report.needed_buildings:
        notes.append(
            "must build first: "
            + ", ".join(g.buildings[c].name for c in report.needed_buildings if c in g.buildings)
        )
    return notes


def _logistics_block(report: PlanFactoryReport, limit: int) -> tuple[str, list[str]]:
    """The belt and pipe table, pinned items first, and what it had to leave out.

    Pins are ADDITIVE to the limit: naming two small items must not drop two big ones.
    """
    flows, pinned = report.flows, report.logistics_item_ids
    notes = list(report.logistics_item_errors)
    rest = [e for e in flows if e["item"] not in pinned]
    top_flows = [e for e in flows if e["item"] in pinned] + rest[: render.clamp(limit, default=15)]
    if len(top_flows) < len(flows):
        notes.append(
            f"logistics: showing {len(top_flows)} of {len(flows)} flows by volume "
            "-- raise limit, or name items in logistics_items to pin them"
        )
    if not top_flows:
        return "", notes
    block = "\n# logistics (lines at Mk5 belt / Mk2 pipe)\n" + render.table(
        ("item", "rate", "carrier", "lines"),
        [
            (e["name"], f"{render.num(e['rate'])}{e['unit']}", e["carrier"], e["lines"])
            for e in top_flows
        ],
    )
    return block, notes


def _plan_summary(
    g: GameData,
    st: WorldState,
    report: PlanFactoryReport,
    objective: str,
    only_free_nodes: bool,
    plan_name: str,
) -> str:
    """The header: scope and plan id, the headline numbers, exports, raw and sunk."""
    req, sol, bill = report.prepared.request, report.prepared.solution, report.bill
    summary = "\n".join(
        [
            (
                f"# {objective} over {req.selection.description} "
                f"({'free nodes only' if only_free_nodes else 'all nodes'}) "
                # Spelled exactly as diff_vs_save spells it, so the two can be compared.
                f"[plan {req.plan_id}]"
            ),
            f"# {st.age_note}",
            render.kv(
                [
                    ("net_MW", render.num(bill.net_mw)),
                    ("buildings", render.num(sol.machines_total)),
                    ("water_extractors", report.water_pumps or None),
                    ("grid_import_MW", render.num(grid_import_mw(sol, bill))),
                ]
            ),
            # A named export solved to zero prints 0 rather than vanishing from the line.
            "exports: "
            + render.kv(
                [
                    ("MW" if k == MW else g.item_name(k), render.num(v))
                    for k, v in sol.exports.items()
                ]
                + [(z["name"], "0") for z in report.zero_exports if z["item"] not in sol.exports]
            ),
            "raw: " + render.kv([(g.item_name(k), render.num(v)) for k, v in sol.raw_used.items()]),
            "sunk: "
            + (
                render.kv([(g.item_name(k), render.num(v)) for k, v in sol.sunk.items()])
                or "nothing"
            ),
        ]
    )
    if plan_name:
        summary = f"# recalled plan {plan_name!r}\n" + summary
    return summary


def render_plan_factory(
    g: GameData,
    st: WorldState,
    report: PlanFactoryReport,
    *,
    objective: str,
    only_free_nodes: bool,
    limit: int,
    plan_name: str = "",
    plan_notes: list[str] | None = None,
    save_as_note: str = "",
) -> str:
    prepared = report.prepared
    if prepared.failure:
        hint = (
            "with equality balances, infeasible usually means a byproduct has no "
            "consumer and no legal sink -- try adding it to exports"
        )
        notes = (
            [*prepared.failure.notes, hint]
            if "INFEASIBLE" in prepared.failure.headline
            else prepared.failure.notes
        )
        return render.envelope(f"# {prepared.failure.headline}", "", notes)
    req, sol, bill = prepared.request, prepared.solution, report.bill

    notes = [
        *_zero_export_notes(report, objective),
        *req.selection.errors,
        *req.site_errors,
        *req.recipe_errors,
        *prepared.notes,
    ]
    notes += [note for note in (_water_note(report), _shard_note(report)) if note]
    notes += _sloop_notes(report)
    notes += _caveat_notes(g, report, objective)
    logistics_block, logistics_notes = _logistics_block(report, limit)
    notes += logistics_notes

    notes = [*(plan_notes or []), *notes]
    notes += payback.trade_text(
        power_view(g, st, req, sol, round(sol.machines_total), bill.draw_mw + bill.sink_mw)
    )
    if save_as_note:
        notes.append(save_as_note)

    return render.envelope(
        _plan_summary(g, st, report, objective, only_free_nodes, plan_name),
        render.table(
            ("build", "clock", "process", "building", "MW", "note"),
            _build_rows(report, limit),
            total=len(sol.processes),
            limit=limit,
        )
        + logistics_block,
        notes,
    )
