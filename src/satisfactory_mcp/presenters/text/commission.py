"""A startup sequence as TSV: the waves, and the warnings that make them safe.

The table is two interleaved row shapes -- a summary line per wave and the processes inside
it -- because the numbers that decide whether the sequence is safe, what it costs and what
it hands back, belong to the wave rather than to any row in it. The rest is notes, because
the arithmetic is four columns and the ways a player can come to grief treating it as a
build order are not.
"""

from __future__ import annotations

from ...core.gamedata.model import GameData
from ...domain.planning.commission_service import CommissionReport
from ...domain.power.report import biomass_note
from ...domain.world.state import WorldState
from . import primitives as render
from .diff import built_lines

__all__ = ["render_commission"]


def render_commission(
    g: GameData,
    st: WorldState,
    report: CommissionReport,
    *,
    objective: str,
    limit: int,
    offset: int = 0,
    plan_name: str = "",
    plan_notes: list[str] | None = None,
) -> str:
    prepared = report.prepared
    if prepared.failure:
        return render.envelope(
            f"# {prepared.failure.headline} -- nothing to commission",
            "",
            [*prepared.failure.notes, "see plan_factory for why"],
        )
    plan_notes = [*(plan_notes or [])]
    plan_run, power = report.plan_run, report.power
    head, source = report.head_mw, report.head_source

    rows = []
    for w in plan_run.waves:
        rows.append(
            (
                f"W{w.index}",
                "",
                w.machines,
                "",
                f"-- switch on {w.machines}, wait >={w.fill_s():.0f}s, then next wave --",
                f"{render.num(-w.draw_mw)} then +{render.num(w.generation_mw)}",
                f"{w.available_after:,.0f}",
            )
        )
        for r in w.rows:
            rows.append(
                (
                    "",
                    f"d{r.depth}",
                    r.machines,
                    f"{r.cumulative}/{r.total}",
                    r.label[:34],
                    render.num(-r.draw_mw) if r.draw_mw else f"+{render.num(r.generation_mw)}",
                    "",
                )
            )
    # Truncation applies to the WHOLE sequence, never per wave: a wave's generator rows sort
    # last by chain depth, and they are the only rows that pay for the next wave. `offset`
    # continues that one sequence, so a page can start mid-wave and show no wave summary line.
    offset = max(0, offset)
    body = render.table(
        ("wave", "chain", "on", "cum", "process", "MW", "free after"),
        rows[offset : offset + render.clamp(limit, default=25)],
        total=len(rows),
        offset=offset,
        limit=limit,
    )

    summary = "\n".join(
        [
            f"# startup order for {objective}"
            + (f" ({plan_name})" if plan_name else "")
            + f", {len(plan_run.waves)} wave(s)",
            f"# {st.age_note}",
            *built_lines(report.built_at, plan_name),
            (
                f"headroom_MW={head:,.0f} (source: {source})  "
                f"plant_draw_MW={plan_run.plant_draw_mw:,.0f}  "
                f"plant_generation_MW={plan_run.plant_generation_mw:,.0f}"
            ),
            (
                f"minimum_slice_MW={plan_run.minimum_slice_mw:,.0f} "
                "(one machine of every process -- the floor no order can go under)"
            ),
        ]
    )

    notes = [*plan_notes, *plan_run.warnings]
    if source == "power_report, nameplate" and biomass_note(power):
        notes.append(biomass_note(power))
    if source == "power_report, nameplate" and power["measured_headroom_mw"] > head * 1.2:
        notes.append(
            f"your grid is only {power['utilisation']:.0%} utilised, so measured headroom "
            f"is {power['measured_headroom_mw']:,.0f} MW against the {head:,.0f} MW "
            "nameplate used here. Nameplate is the safe bound -- energising a block can "
            "un-starve idle machines and the fuse blows on demand, not on averages -- but "
            "if you know your base is quiet, pass headroom_mw= to plan against the real "
            "figure and get far fewer waves"
        )
    if plan_run.ok:
        notes.append(
            "build EVERYTHING first, unpowered: a machine draws only when it runs, so "
            "construction is never the constraint. These waves are switch-ons"
        )
        # Read from the save's own connections, and only PROVEN-running generators count.
        if report.live:
            notes.append(
                "CUTOVER RISK -- these are already feeding running generators, so "
                "repiping one mid-startup takes that power out at the worst moment: "
                + "; ".join(f"{name} ({mw:,.0f} MW)" for name, mw in report.live[:4])
                + ". trace_upstream on any of them shows what hangs off it"
            )
        notes.append(
            "wire one Power Switch per block before starting. Energising is then a "
            "switch flip, and a block that misbehaves can be isolated -- without one, "
            "an overload blows the fuse on the WHOLE grid and stops the plant feeding it"
        )
        waits = [w.index for w in plan_run.waves if w.waits_for_fill]
        if waits:
            notes.append(
                "wave(s) "
                + ", ".join(f"W{i}" for i in waits[:6])
                + " energise consumers and the generators they feed: let the pipes fill "
                "and the generators come up to speed BEFORE starting the next wave. A "
                "wave's own generation is not counted until it completes, so the free-MW "
                "column is what you have during the wait, not after it"
            )
        slowest = max((w.fill_s() for w in plan_run.waves), default=0.0)
        notes.append(
            f"the wait is a LOWER bound (>={slowest:.0f}s on the longest wave): it sums "
            "one full cycle at each chain depth, which every stage must finish before the "
            "next sees anything. It does NOT include pipe transit -- a pipe's fluid volume "
            "is not in the dump (only mRadius, which is collision geometry) and route "
            "lengths are unknown -- so on a long run the real wait is longer, and the "
            "deficit is carried for all of it"
        )
        notes.append(
            "waves are power-ordered, not ratio-balanced -- whole machines cannot hit "
            "the plan's ratios at the bottom of the ramp, so early waves run starved. "
            "That is safe: a starved machine idles and draws less than modelled"
        )
    return render.envelope(summary, body, notes)
