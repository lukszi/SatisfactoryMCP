"""A diff against the save as TSV: what to change, and what the save cannot prove.

Two shapes share one report. The default is the delta -- rows of actions, ordered
free-first -- and the stage views are the same plan cut into startup stages and matched
against what stands. Both carry the same caveat, because it is the one thing a reader
will otherwise get wrong: the save separates built from energised in one direction only.
"""

from __future__ import annotations

from ...domain.planning.progress.diff import NEIGHBOUR_RADIUS_M as DIFF_NEIGHBOUR_M
from ...domain.planning.progress.diff_service import DiffVsSaveReport
from ...domain.planning.progress.stages import ENERGISED_CAVEAT, RANGE_CAVEAT, Tracking
from ...domain.power.report import biomass_note
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["built_at_lines", "render_diff"]

#: Cost rows shown. Deliberately below ``limit``: the bill is ranked by shortfall and the
#: gate on a build is at its head, so this is a headline and not the whole bill.
COST_ROWS = 5

#: Machine ids named per actionable row: enough to walk to the first few, not a work order.
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
        # The free action belongs to the whole build job, not to this slice of it, so it
        # is not rendered as this stage's verb.
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


def built_at_lines(found, plan_name: str = "") -> list[str]:
    """Where a stored plan's built machines were found, as header lines."""
    if found is None or not hasattr(found, "tool_text"):
        return []
    lines = [f"# {found.tool_text()}"]
    lines += [f"#   {extra}" for extra in (found.fallback, found.hint, *found.details()) if extra]
    plan = f"plan={plan_name!r}" if plan_name else "plan=<name>"
    top = found.top
    if found.confidence == "unsure":
        name = next((c.name for c in found.candidates if c.kind == "factory"), "<name>")
        lines.append(
            f"#   pass factory={name!r} for this call, or plan_factory {plan} "
            f"for_factory={name!r} to keep it"
        )
    elif found.confidence == "no site":
        lines.append(
            f"#   site_plan {plan} at=<where> places it; plan_factory {plan} "
            "for_factory=<factory> or 'whole world' counts without a site"
        )
    elif found.mode == "auto" and top is not None and top.kind == "cluster":
        lines.append(
            f"#   found automatically; name_factory select=['proposal:{top.proposal}'] names "
            "that cluster, then for_factory= keeps it"
        )
    return lines


def _stage_answer(
    st: WorldState, report: DiffVsSaveReport, stage: int, limit: int, objective: str, header_lines
) -> tuple[str, str, list[str]]:
    """``stage=<n>``: the header, that one stage's body, and its notes (body '' when absent)."""
    req, diff_report = report.prepared.request, report.rep
    body, stage_notes = _stage_detail(report.tracking, stage, limit)
    if not body:
        return (
            f"# no stage {stage} [plan {req.plan_id}/save {diff_report.save_id}]",
            "",
            stage_notes,
        )
    header = "\n".join(
        [
            (
                f"# stage {stage} of plan {objective}|{req.selection.description} "
                f"[plan {req.plan_id}/save {diff_report.save_id}]"
            ),
            f"# {st.age_note}",
            *header_lines,
        ]
    )
    return header, body, stage_notes


def _delta_rows(diff_report, limit: int) -> tuple[list[tuple], list[str], list[str]]:
    """The action rows, the free nodes they target, and the machines each action names."""
    rows = []
    targets: list[str] = []
    acts: list[str] = []
    for r in diff_report.rows[: render.clamp(limit, default=20)]:
        if r.act_instances:
            acts.append(
                f"#   {r.verb} {r.process[:30]}: "
                + render.capped(r.act_instances, ACT_IDS, sep=" ")
            )
        count = "" if r.verb == "OK" else render.num(r.count)
        if r.verb == "BUILD" and r.build_max is not None and r.build_max != r.build:
            count = f"{r.build}..{r.build_max}"
        note = r.note
        if r.targets:
            # Ids go in one footer: a node instance name would crowd every other column.
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
    return rows, targets, acts


def _delta_summary(st: WorldState, report: DiffVsSaveReport, objective: str, header_lines) -> str:
    """The header: the plan and save ids, what is left to place, and the grid now."""
    req, sol = report.prepared.request, report.prepared.solution
    diff_report, power = report.rep, report.power
    to_place = (
        render.num(diff_report.to_build)
        if diff_report.to_build_max == diff_report.to_build
        else f"{diff_report.to_build}..{diff_report.to_build_max}"
    )
    return "\n".join(
        [
            (
                f"# diff vs plan {objective}|{req.selection.description} "
                f"[plan {req.plan_id}/save {diff_report.save_id}]"
            ),
            f"# {st.age_note}",
            *header_lines,
            render.kv(
                [
                    ("target_MW", render.num(sol.net_mw)),
                    ("plan_buildings", render.num(sol.machines_total)),
                    ("to_place", to_place),
                    ("actionable", sum(1 for r in diff_report.rows if r.actionable)),
                ]
            ),
            render.kv(
                [
                    ("now_gen_MW", render.num(power["generation_mw"])),
                    ("draw_MW", render.num(power["draw_mw"])),
                    ("headroom_MW", render.num(power["headroom_mw"])),
                ]
            ),
        ]
    )


def _delta_notes(report: DiffVsSaveReport) -> list[str]:
    """What the delta rows cannot say for themselves: ranges, spread, off-100% clocks."""
    diff_report = report.rep
    notes = [*diff_report.notes]
    if biomass_note(report.power):
        notes.append(biomass_note(report.power))
    ranged = [r for r in diff_report.rows if r.build_max is not None and r.build_max != r.build]
    for r in ranged[:2]:
        notes.append(
            f"{r.building}s cannot be matched to a job, so {r.need} needed vs {r.have} "
            f"built is a RANGE: build {r.build}..{r.build_max}"
        )
    spread = [t[1] for r in diff_report.rows for t in r.targets]
    if spread and max(spread) - min(spread) > 1000:
        notes.append(
            f"the plan's build targets span {min(spread) / 1000:.2g}-"
            f"{max(spread) / 1000:.2g}km from your plant -- this is one plan, not one site"
        )
    if any("plan budgets 100%" in r.note for r in diff_report.rows):
        notes.append(
            "matched machines running off 100% are noted, not actioned: the plan "
            "budgets 100%, so it understates what you already produce"
        )
    return notes


def _site_survey_block(report: DiffVsSaveReport, limit: int) -> tuple[str, list[str]]:
    """What stands inside the sited footprint against what the plan wants there."""
    if report.site is None or report.site_survey is None:
        return "", []
    survey = report.site_survey
    site_rows = [
        (r.name[:24], r.planned, r.standing, f"{r.standing - r.planned:+d}")
        for r in survey.rows[: render.clamp(limit, default=20)]
    ]
    block = (
        f"# ON SITE (approximate): {report.site.describe()}\n"
        f"# {survey.standing_total} machine(s) stand inside that footprint; "
        f"the plan wants {survey.planned_total}\n"
        + render.table(
            ("building", "planned", "on_site", "delta"),
            site_rows,
            total=len(survey.rows),
            limit=limit,
        )
    )
    note = (
        "ON SITE counts by BUILDING CLASS inside the sited footprint only -- it checks "
        "neither recipes nor clocks, so it says whether the pad holds the right SHAPE "
        "of plant; the rows above are the identity-matched truth"
    )
    return block, [note]


def _cost_block(diff_report) -> str:
    """The head of the bill for what is left to build, against spendable stock."""
    return (
        "# cost of the build counts. stock is spendable only, never machine buffers."
        "\n"
        + render.table(
            ("item", "need", "stock", "your_lines"),
            [
                (c.name[:24], render.num(c.need), render.num(c.stock), c.lines)
                for c in diff_report.cost[:COST_ROWS]
            ],
            total=len(diff_report.cost),
        )
    )


def _order_block(diff_report) -> str:
    """The proportional-slices order, for a plan the startup stages do not cover."""
    return (
        "# ORDER: an LP solution is a ray, so any fraction of the plan is itself "
        f"feasible and self-powered.\n# The build dips {render.num(diff_report.deficit_mw)} MW "
        f"against {render.num(diff_report.headroom_mw)} MW of headroom, so place it in "
        f">={diff_report.slices} proportional slices."
    )


def render_diff(
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
        # An empty diff would read as "you already have it", the opposite of infeasible.
        suffix = " -- no plan to diff against" if "INFEASIBLE" in prepared.failure.headline else ""
        return render.envelope(
            f"# {prepared.failure.headline}{suffix}",
            "",
            [*prepared.failure.notes, "see plan_factory for why; there is nothing to change yet"],
        )
    sol = prepared.solution
    if report.empty:
        # Feasible but empty: the objective walked away from the resource, which an empty
        # table would read as "nothing to do".
        return render.envelope(
            f"# EMPTY PLAN ({objective} over {prepared.request.selection.description}) "
            "-- nothing to change",
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
    diff_report, tracking = report.rep, report.tracking
    header_lines = built_at_lines(diff_report.built_at, plan_name)
    if report.drift_note:
        plan_notes.append(report.drift_note)

    if stage:
        header, body, stage_notes = _stage_answer(st, report, stage, limit, objective, header_lines)
        return render.envelope(header, body, [*plan_notes, *stage_notes])

    rows, targets, acts = _delta_rows(diff_report, limit)
    notes = _delta_notes(report)
    parts = [
        render.table(
            ("st", "act", "n", "process", "building", "have", "where(km)", "note"),
            rows,
            total=len(diff_report.rows),
            limit=limit,
        )
    ]
    if targets:
        parts.append(
            "# build targets, reusable as node: selectors -- " + render.capped(targets, 4, sep=" ")
        )
    if acts:
        # Per row, not pooled: which machines an action applies to is the whole point.
        parts.append("# machines to act on, reusable as machine: selectors\n" + "\n".join(acts))
    if diff_report.neighbours:
        near = ", ".join(f"{n}x {label}" for label, n in diff_report.neighbours[:3])
        parts.append(
            f"# within {int(DIFF_NEIGHBOUR_M)}m and competing for the plan's own "
            f"materials, but NOT in it: {near}"
            "\n#   yours to keep or reclaim; no action proposed"
        )
    survey, survey_notes = _site_survey_block(report, limit)
    if survey:
        parts.append(survey)
        notes += survey_notes
    if show_cost and diff_report.cost:
        parts.append(_cost_block(diff_report))
    # Not beside the stage table: the slices advice predates the startup order and would
    # tell the player to partition a build that is not partitioned.
    if diff_report.deficit_mw > 0 and diff_report.slices > 1 and tracking is None:
        parts.append(_order_block(diff_report))
    if tracking is not None:
        block, stage_notes = _stage_overview(tracking)
        if block:
            parts.append(block)
        notes += stage_notes
        if report.run is not None and report.run.headroom_source:
            notes.append(
                f"stages use {render.num(report.run.headroom_mw)} MW of headroom, "
                f"{report.run.headroom_source}"
            )

    if plan_name:
        plan_notes = [f"recalled saved plan {plan_name!r}", *plan_notes]
    return render.envelope(
        _delta_summary(st, report, objective, header_lines),
        "\n".join(parts),
        [*plan_notes, *notes],
    )
