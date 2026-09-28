"""A diff against the save as TSV: what to change, and what the save cannot prove.

Two shapes share one report. The default is the delta -- rows of actions, ordered
free-first -- and the stage views are the same plan cut into startup stages and matched
against what stands. Both carry the same caveat, because it is the one thing a reader
will otherwise get wrong: the save separates built from energised in one direction only.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.commission import ENERGISED_CAVEAT, RANGE_CAVEAT, Tracking
from ...domain.planning.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from ...domain.planning.diff_service import DiffVsSaveReport
from ...domain.power.report import biomass_note
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["ENERGISED_CAVEAT", "RANGE_CAVEAT", "render_diff"]

#: Cost rows shown. Deliberately below ``limit``: the bill is ranked by shortfall and the
#: gate on a build is at its head, so this is a headline and not the whole bill.
COST_ROWS = 5

#: Machine ids named per actionable row. Enough to walk to the first few and no more: a
#: row can name 23 machines, and the footer is a starting point, not a work order.
ACT_IDS = 3


def _stage_overview(tracking: Tracking) -> tuple[str, list[str]]:
    """The whole partition against the save: which stage the player is in."""
    if not tracking.ok:
        return "", tracking.warnings
    rows = [
        (
            f"S{s.index}",
            s.machines,
            f"{s.built}..{s.built_max}" if s.built_max != s.built else s.built,
            s.running,
            f"{render.num(-s.draw_mw)}/+{render.num(s.generation_mw)}",
            f"{s.available_after:,.0f}",
            s.describe(),
        )
        for s in tracking.stages
    ]
    headline = "# " + tracking.headline()
    body = (
        "# STAGES: the commission_plan startup order, matched against the save\n"
        + render.table(("stage", "on", "built", "running", "MW", "free after", "state"), rows)
        + "\n"
        + headline
    )
    notes = [*tracking.warnings, ENERGISED_CAVEAT]
    if any(s.built_max != s.built for s in tracking.stages):
        notes.append(RANGE_CAVEAT)
    if tracking.monitored:
        notes.append(
            f"{tracking.monitored} built machine(s) carry a productivity monitor, so "
            "'running' is measured for those and unknown for the rest. Pass stage=<n> "
            "for one stage's rows, or factory_health for why a machine is stopped"
        )
    if not tracking.plan_name:
        notes.append(
            "these stage numbers came from THIS CALL's arguments, not a stored plan, so "
            "they renumber whenever the arguments or the world move. Save the plan "
            "(plan_factory save_as=...) before treating a stage number as a milestone"
        )
    return body, notes


def _stage_detail(tracking: Tracking, index: int, limit: int) -> tuple[str, list[str]]:
    """One stage's own rows: what it energises, what stands, what is proven running."""
    stage = next((s for s in tracking.stages if s.index == index), None)
    if stage is None:
        available = ", ".join(f"{s.index}" for s in tracking.stages) or "(none)"
        return "", [f"no stage {index} in this plan; it has stages {available}"]
    rows = []
    for r in stage.rows:
        # The free action belongs to the whole build job, not to this slice of it: three
        # paused pumps are three dropdowns however the waves cut them. Rendering it as
        # this stage's verb would tell the player to unpause them twice.
        note = f"{r.verb} {r.free} first, plan-wide" if r.free else ""
        note = f"{note}; {r.note}" if note and r.note else note or r.note
        rows.append(
            (
                "BUILD" if r.to_build else "OK",
                r.machines,
                f"{r.built}..{r.built_max}" if r.built_max != r.built else r.built,
                r.running,
                r.label[:34],
                r.building[:18],
                render.num(-r.draw_mw) if r.draw_mw else f"+{render.num(r.generation_mw)}",
                note[:44],
            )
        )
    body = (
        f"# STAGE {index} of {len(tracking.stages)}: {stage.machines} machine(s), "
        f"{stage.describe()}\n"
        + render.kv(
            [
                ("draw_MW", render.num(stage.draw_mw)),
                ("generation_MW", render.num(stage.generation_mw)),
                ("free_before_MW", render.num(stage.available_before)),
                ("free_after_MW", render.num(stage.available_after)),
            ]
        )
        + "\n"
        + render.table(
            ("act", "on", "built", "running", "process", "building", "MW", "note"),
            rows[: render.clamp(limit, default=20)],
            total=len(rows),
            limit=limit,
        )
    )
    notes = [
        *tracking.warnings,
        ENERGISED_CAVEAT,
        (
            "materials are NOT split by stage, and the cost table is left out here for "
            "that reason: a stage is a switch-on, not a build step, so the whole plant "
            "is built first and the bill belongs to the plan as a whole"
        ),
    ]
    if any(r.built_max != r.built for r in stage.rows):
        notes.append(RANGE_CAVEAT)
    if stage.dark:
        notes.append(
            f"{stage.dark} machine(s) in this stage are dark with no supply cause the "
            "save can name -- consistent with not being energised yet, but the file "
            "cannot confirm it"
        )
    return body, notes


def render_diff(
    g: GameData,
    st: WorldState,
    report: DiffVsSaveReport,
    *,
    objective: str,
    limit: int,
    show_cost: bool = True,
    stage: int | None = None,
    plan_name: str = "",
    plan_notes: list[str] | None = None,
) -> str:
    prepared = report.prepared
    if prepared.failure:
        # Hand back the plan's own reason. An empty diff table would read as "you
        # already have it", which is the opposite of what infeasible means.
        suffix = " -- no plan to diff against" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why; there is nothing to change yet"],
        )
    req, sol = prepared.request, prepared.solution
    sel = req.selection

    if report.empty:
        # Feasible but empty. Rendering an empty table would read as "nothing to do",
        # when what happened is that the objective walked away from the resource --
        # every crude route here emits Polymer Resin, and with MW as the only export
        # the LP abandons oil entirely.
        return render.envelope(
            f"# EMPTY PLAN ({objective} over {sel.description}) -- nothing to change",
            "",
            [
                *sol.warnings,
                (
                    "the solve chose to build nothing, which usually means a byproduct "
                    "has no outlet -- widen exports and re-run plan_factory first"
                ),
            ],
        )

    plan_notes = [*(plan_notes or [])]
    if report.scope_note:
        plan_notes.append(report.scope_note)
    rep, pw, tracking = report.rep, report.power, report.tracking
    if report.drift_note:
        plan_notes.append(report.drift_note)

    if stage:
        body, stage_notes = _stage_detail(tracking, stage, limit)
        if not body:
            return render.envelope(
                f"# no stage {stage} [plan {req.plan_id}/save {rep.save_id}]",
                "",
                [*plan_notes, *stage_notes],
            )
        return render.envelope(
            "\n".join(
                [
                    (
                        f"# stage {stage} of plan {objective}|{sel.description} "
                        f"[plan {req.plan_id}/save {rep.save_id}]"
                    ),
                    f"# {st.age_note}",
                ]
            ),
            body,
            [*plan_notes, *stage_notes],
        )

    rows = []
    targets: list[str] = []
    acts: list[str] = []
    for r in rep.rows[: render.clamp(limit, default=20)]:
        if r.act_instances:
            named = r.act_instances[:ACT_IDS]
            more = len(r.act_instances) - len(named)
            acts.append(
                f"#   {r.verb} {r.process[:30]}: "
                + " ".join(named)
                + (f" (+{more} more)" if more > 0 else "")
            )
        count = "" if r.verb == "OK" else render.num(r.count)
        if r.verb == "BUILD" and r.build_max is not None and r.build_max != r.build:
            count = f"{r.build}..{r.build_max}"
        note = r.note
        if r.targets:
            # Ids go in one footer, per the house rule -- a node instance name runs to
            # 51 characters and would crowd every other column off the row.
            spans = [t[1] / 1000 for t in r.targets]
            reach = (
                f"{min(spans):.2g}km"
                if max(spans) - min(spans) < 0.1
                else f"{min(spans):.2g}-{max(spans):.2g}km"
            )
            head = f"on {len(r.targets)} free node(s) @{reach}"
            note = f"{head}; {note}" if note else head
            targets += [t[0] for t in r.targets]
        rows.append(
            (
                r.stage,
                r.verb,
                count,
                r.process[:30],
                r.building[:20],
                r.have,
                render.where_bands(r.have_distances),
                note[:56],
            )
        )

    to_place = (
        render.num(rep.to_build)
        if rep.to_build_max == rep.to_build
        else f"{rep.to_build}..{rep.to_build_max}"
    )
    summary = "\n".join(
        [
            f"# diff vs plan {objective}|{sel.description} [plan {req.plan_id}/save {rep.save_id}]",
            f"# {st.age_note}",
            render.kv(
                [
                    ("target_MW", render.num(sol.net_mw)),
                    ("plan_buildings", render.num(sol.machines_total)),
                    ("to_place", to_place),
                    ("actionable", sum(1 for r in rep.rows if r.actionable)),
                ]
            ),
            render.kv(
                [
                    ("now_gen_MW", render.num(pw["generation_mw"])),
                    ("draw_MW", render.num(pw["draw_mw"])),
                    ("headroom_MW", render.num(pw["headroom_mw"])),
                ]
            ),
        ]
    )

    notes = [*rep.notes]
    if biomass_note(pw):
        notes.append(biomass_note(pw))
    for r in [r for r in rep.rows if r.build_max is not None and r.build_max != r.build][:2]:
        notes.append(
            f"{r.building}s cannot be matched to a job, so {r.need} needed vs {r.have} "
            f"built is a RANGE: build {r.build}..{r.build_max}"
        )
    spread = [t[1] for r in rep.rows for t in r.targets]
    if spread and max(spread) - min(spread) > 1000:
        notes.append(
            f"the plan's build targets span {min(spread) / 1000:.2g}-"
            f"{max(spread) / 1000:.2g}km from your plant -- this is one plan, not one site"
        )
    if any("plan budgets 100%" in r.note for r in rep.rows):
        notes.append(
            "matched machines running off 100% are noted, not actioned: the plan "
            "budgets 100%, so it understates what you already produce"
        )

    parts = [
        render.table(
            ("st", "act", "n", "process", "building", "have", "where(km)", "note"),
            rows,
            total=len(rep.rows),
            limit=limit,
        )
    ]
    if targets:
        parts.append(
            "# build targets, reusable as node: selectors -- "
            + " ".join(targets[:4])
            + (f" (+{len(targets) - 4} more)" if len(targets) > 4 else "")
        )
    if acts:
        # Per row, not pooled like the build targets: which machines an action applies to
        # is the whole point, and "unpause 3" over 23 pumps names three of them or nothing.
        parts.append("# machines to act on, reusable as machine: selectors\n" + "\n".join(acts))
    if rep.neighbours:
        near = ", ".join(f"{n}x {label}" for label, n in rep.neighbours[:3])
        parts.append(
            f"# within {int(DIFF_NEIGHBOUR_M)}m and competing for the plan's own "
            f"materials, but NOT in it: {near}"
            "\n#   yours to keep or reclaim; no action proposed"
        )
    if report.site is not None and report.site_survey is not None:
        sv = report.site_survey
        site_rows = [
            (r.name[:24], r.planned, r.standing, f"{r.standing - r.planned:+d}")
            for r in sv.rows[: render.clamp(limit, default=20)]
        ]
        parts.append(
            f"# ON SITE (approximate): {report.site.describe()}\n"
            f"# {sv.standing_total} machine(s) stand inside that footprint; "
            f"the plan wants {sv.planned_total}\n"
            + render.table(
                ("building", "planned", "on_site", "delta"),
                site_rows,
                total=len(sv.rows),
                limit=limit,
            )
        )
        notes.append(
            "ON SITE counts by BUILDING CLASS inside the sited footprint only -- it checks "
            "neither recipes nor clocks, so it says whether the pad holds the right SHAPE "
            "of plant; the rows above are the identity-matched truth"
        )
    if show_cost and rep.cost:
        parts.append(
            "# cost of the build counts. stock is spendable only, never machine buffers."
            "\n"
            + render.table(
                ("item", "need", "stock", "your_lines"),
                [
                    (c.name[:24], render.num(c.need), render.num(c.stock), c.lines)
                    for c in rep.cost[:COST_ROWS]
                ],
                total=len(rep.cost),
            )
        )
    # Suppressed when the stage table is present: "place it in >=18 proportional slices"
    # is the answer from BEFORE the startup-order re-frame, and printing it beside the
    # startup order would tell the player to partition a build that is not partitioned.
    if rep.deficit_mw > 0 and rep.slices > 1 and tracking is None:
        parts.append(
            "# ORDER: an LP solution is a ray, so any fraction of the plan is itself "
            f"feasible and self-powered.\n# The build dips {render.num(rep.deficit_mw)} MW "
            f"against {render.num(rep.headroom_mw)} MW of headroom, so place it in "
            f">={rep.slices} proportional slices."
        )
    if tracking is not None:
        block, stage_notes = _stage_overview(tracking)
        if block:
            parts.append(block)
        notes += stage_notes

    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]

    return render.envelope(summary, "\n".join(parts), [*plan_notes, *notes])
