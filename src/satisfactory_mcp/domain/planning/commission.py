"""Bringing a plant online without blowing the fuse.

**This is a startup order, not a build order.** Building costs materials and never power --
a machine draws only when it runs -- so the whole plant may be constructed at leisure and
then energised block by block, under one constraint: at every step, the energised consumer
draw must stay within the headroom plus the generation from generators already receiving
fuel. Overshooting does not degrade gracefully in Satisfactory; the fuse blows and the
whole grid stops until it is reset by hand, so every step here is checked rather than
merely reported. ``track`` then matches the partition back against the save.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ...core.text import plural
from ..factories.health import assess
from ..world.state import WorldState
from .diff import DiffReport, group_key, rate_units

__all__ = [
    "DARK_STATES",
    "ENERGISED_CAVEAT",
    "MONITORED_STATES",
    "NO_MONITOR",
    "RANGE_CAVEAT",
    "RUNNING_STATES",
    "Commissioning",
    "Energised",
    "Stage",
    "StageRow",
    "Tracking",
    "Wave",
    "commission",
    "feeder_records",
    "live_feeders",
    "machine_states",
    "partition_id",
    "track",
]

#: ``graph.health`` states that PROVE a machine was energised: both mean it produced inside
#: the last complete window, and a machine with no power produces nothing. Every other
#: state is silence, and silence has several causes.
RUNNING_STATES = frozenset({"saturated", "intermittent"})

#: The states where "no power" is still a live explanation. ``blocked`` and ``starved`` name
#: a supply cause instead; ``stalled`` is where an unpowered block lands -- and also where a
#: monitor that has not caught up lands, so it is never conclusive.
DARK_STATES = frozenset({"stalled", "unmonitored"})

#: States reachable only by reading the productivity monitor. If none of a plan's machines
#: land in one, the save carries no uptime evidence at all and the report has to say so
#: instead of reading silence as "nothing is running".
MONITORED_STATES = frozenset({"saturated", "intermittent", "blocked", "starved", "stalled"})

ENERGISED_CAVEAT = (
    "built and ENERGISED are different states and the save separates them only one way: "
    "a machine that produced inside the last 300s window certainly had power, while a "
    "machine that did not may be unpowered, starved, blocked or simply idle. mHasPower "
    "and the circuit id are not SaveGame properties and the circuit subsystem stores "
    "nothing, so grid membership is rebuilt at load and is NOT in the file. A fully "
    "built, wholly dark block is a valid state here, not an anomaly"
)

RANGE_CAVEAT = (
    "a built count is a RANGE wherever a machine cannot be attributed to this plan "
    "(Water Extractors, OQ5): the low bound counts only the ones standing among the "
    "plan's own. 'running' is measured over every MATCHED machine, so it can sit above "
    "the low bound without contradicting it"
)

NO_MONITOR = (
    "this save carries no productivity monitor for any matched machine, so "
    "there is NO evidence either way about what is energised -- only what is built"
)

#: How many waves to attempt before giving up. A plant whose generation exceeds its draw
#: converges geometrically, so a run that reaches this many is not converging.
MAX_WAVES = 24


@dataclass
class Energised:
    """One process, and how much of it comes on in this wave."""

    label: str
    kind: str
    building: str
    #: Machines switched on in THIS wave, and the running total against the plan.
    machines: int
    cumulative: int
    total: int
    draw_mw: float
    generation_mw: float
    #: Distance from raw extraction along the item chain. Decides switch-on order within a
    #: wave: upstream first, so the fluid is already moving when the next block lights.
    depth: int = 0
    #: Solution process id, so a wave row joins back to the build job the diff matched
    #: against the save without re-deriving it from labels, which are display strings.
    pid: str = ""
    #: One cycle of this process at its own clock, in seconds. 0 for a generator, which
    #: burns continuously and has no cycle to wait through.
    cycle_s: float = 0.0


@dataclass
class Wave:
    index: int
    rows: list[Energised] = field(default_factory=list)
    available_before: float = 0.0

    def fill_s(self) -> float:
        """Lower bound, in seconds, on the wait before this wave's generators produce.

        The CYCLE chain: every stage must finish one full cycle before the next stage sees
        anything, so the sum of the slowest cycle at each chain depth is a hard floor.

        It is a floor and not an estimate because **pipe transit is not in it**. A pipe's
        fluid volume is not in Docs.json -- the only dimension there is ``mRadius``, which
        is collision geometry -- and route lengths are unknown, so on a long run the
        transit dominates this number. Machine input buffers are out for a related reason:
        their capacity is per-BUILT-machine and these machines do not exist yet.
        """
        deepest: dict[int, float] = {}
        for row in self.rows:
            if row.cycle_s <= 0:
                continue
            deepest[row.depth] = max(deepest.get(row.depth, 0.0), row.cycle_s)
        return sum(deepest.values())

    @property
    def draw_mw(self) -> float:
        return sum(r.draw_mw for r in self.rows)

    @property
    def generation_mw(self) -> float:
        return sum(r.generation_mw for r in self.rows)

    @property
    def available_after(self) -> float:
        return self.available_before - self.draw_mw + self.generation_mw

    @property
    def machines(self) -> int:
        return sum(r.machines for r in self.rows)

    @property
    def waits_for_fill(self) -> bool:
        """True when this wave energises both consumers and the generators they feed."""
        return any(r.generation_mw > 0 for r in self.rows) and any(r.draw_mw > 0 for r in self.rows)


@dataclass
class Commissioning:
    headroom_mw: float = 0.0
    #: Where the headroom figure came from, printed as a labelled input so a sequence
    #: computed against a stale save is visibly stale.
    headroom_source: str = ""
    waves: list[Wave] = field(default_factory=list)
    plant_draw_mw: float = 0.0
    plant_generation_mw: float = 0.0
    #: Cheapest slice that keeps every stage of the chain fed: one machine of every
    #: process. If this does not fit the headroom, no startup order exists at this scope.
    minimum_slice_mw: float = 0.0
    ok: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def machines(self) -> int:
        return sum(w.machines for w in self.waves)


def _cycle_s(proc: dict, game: GameData) -> float:
    """How long one cycle of this process takes at the clock the plan runs it at.

    Clock divides: a machine at 250% finishes its cycle in 40% of the base time.
    """
    clock = proc.get("clock") or 1.0
    recipe = game.recipes.get(proc.get("recipe") or "")
    if recipe is not None and recipe.duration_s:
        return recipe.duration_s / clock
    building = game.buildings.get(proc.get("building_id") or "")
    if building is not None and building.extract_cycle_s:
        return building.extract_cycle_s / clock
    # Generators burn continuously; there is no cycle to wait through.
    return 0.0


def _depths(processes: list[dict]) -> dict[str, int]:
    """Chain depth per process id, from ``layout.chain_depth``.

    That function and not a local one: ``diff`` computes build order with it and ``track``
    joins a wave against a diff row, so two implementations would let the two halves of
    "which stage am I in" order the same plant differently. It condenses cycles rather
    than relaxing depths, which matters because item flow is genuinely cyclic -- Recycled
    Plastic and Recycled Rubber consume each other's output -- and cycle members have to
    go up on the same stage or the stage is unbuildable.

    ``MW`` is filtered out here rather than inside ``chain_depth``: power is modelled as an
    item so the balance is just another row, but as a DEPENDENCY it would make every
    consumer depend on every generator and every generator on its fuel, leaving one
    component and no order at all.
    """
    from .layout import chain_depth
    from .optimize import MW

    depths = chain_depth(
        [
            (
                [i for i, rate in p["rates"].items() if rate < 0 and i != MW],
                [i for i, rate in p["rates"].items() if rate > 0 and i != MW],
            )
            for p in processes
        ]
    )
    return {p["pid"]: d for p, d in zip(processes, depths, strict=True)}


def commission(
    prepared, game: GameData, headroom_mw: float, headroom_source: str = ""
) -> Commissioning:
    """Order the plan's machines into waves that can each be switched on safely."""
    out = Commissioning(headroom_mw=headroom_mw, headroom_source=headroom_source)
    if prepared.solution is None:
        out.ok = False
        return out

    procs = [p for p in prepared.solution.processes if p["machines"] > 0]
    if not procs:
        out.ok = False
        out.warnings.append("plan has no machines to energise")
        return out

    depth = _depths(procs)
    # Per MACHINE, because a wave energises whole machines. p["mw"] is exact for the whole
    # row at its derived clock, so dividing is right and rounding is not.
    per: dict[str, tuple[float, float]] = {}
    for p in procs:
        each = p["mw"] / p["machines"]
        per[p["pid"]] = (max(0.0, -each), max(0.0, each))
        out.plant_draw_mw += max(0.0, -p["mw"])
        out.plant_generation_mw += max(0.0, p["mw"])

    # One machine of every consuming process: the cheapest slice that still feeds the whole
    # chain. Generators draw nothing, so they are not part of the floor.
    out.minimum_slice_mw = sum(draw for draw, _ in per.values())
    if out.minimum_slice_mw > headroom_mw + 1e-6:
        out.ok = False
        out.warnings.append(
            f"no startup order exists at this scope: one machine of every process draws "
            f"{out.minimum_slice_mw:,.0f} MW and only {headroom_mw:,.0f} MW is free. "
            "Plan a smaller sub-plant (fewer nodes) and commission that first, or add "
            "generation before starting"
        )
        return out

    totals = {p["pid"]: p["machines"] for p in procs}
    done = {p["pid"]: 0 for p in procs}
    by_pid = {p["pid"]: p for p in procs}
    available = headroom_mw

    while sum(done.values()) < sum(totals.values()):
        if len(out.waves) >= MAX_WAVES:
            out.ok = False
            left = sum(totals.values()) - sum(done.values())
            out.warnings.append(
                f"gave up after {MAX_WAVES} waves with "
                f"{left} {plural('machine', left)} unstarted -- "
                "the sequence is not converging, which means generation is not "
                "outrunning draw"
            )
            return out

        wave = Wave(index=len(out.waves) + 1, available_before=available)
        # Largest fraction of the remaining plant this wave's power can carry, then shrunk
        # until the WHOLE-MACHINE bill fits. Ceil keeps the chain fed: flooring 0.64 of an
        # extractor to zero would light six refineries with nothing to refine. Exact ratios
        # are unreachable at the bottom of the ramp, so early waves run starved -- which
        # errs safe, since an idle machine draws well under its modelled figure.
        fraction = 1.0 if out.plant_draw_mw <= 0 else min(1.0, available / out.plant_draw_mw)
        for _ in range(60):
            take = {
                pid: min(
                    totals[pid] - done[pid],
                    max(1, math.ceil(fraction * totals[pid])) if totals[pid] > done[pid] else 0,
                )
                for pid in totals
            }
            cost = sum(per[pid][0] * n for pid, n in take.items())
            if cost <= available + 1e-6:
                break
            fraction *= 0.75
        else:
            out.ok = False
            out.warnings.append("could not fit a whole-machine wave inside the headroom")
            return out

        for pid, n in sorted(take.items(), key=lambda kv: (depth[kv[0]], kv[0])):
            if n <= 0:
                continue
            done[pid] += n
            p = by_pid[pid]
            wave.rows.append(
                Energised(
                    pid=pid,
                    label=p["label"],
                    kind=p["kind"],
                    cycle_s=_cycle_s(p, game),
                    building=p["building"],
                    machines=n,
                    cumulative=done[pid],
                    total=totals[pid],
                    draw_mw=per[pid][0] * n,
                    generation_mw=per[pid][1] * n,
                    depth=depth[pid],
                )
            )
        if not wave.rows:
            out.ok = False
            out.warnings.append("a wave came out empty; nothing further can be energised")
            return out
        out.waves.append(wave)
        # Only NOW does this wave's generation count. It is not available DURING the wave:
        # the pipes are still filling and the generators are not burning yet, so a sequence
        # that spends it early looks fine on paper and trips halfway through.
        available = wave.available_after

    return out


# ------------------------------------------------- which stage am I actually in


@dataclass
class StageRow:
    """One build job's share of one stage, and what the save says about it."""

    stage: int
    label: str
    kind: str
    building: str
    #: Machines this stage energises, and the plan's total for the same job.
    machines: int
    total: int
    #: Machines in the save allotted to this stage. An interval only where identity is
    #: unavailable, such as Water Extractors, and a single number would be a lie in
    #: whichever direction it fell.
    built: int = 0
    built_max: int = 0
    #: graph.health state -> how many of this stage's built machines are in it.
    by_state: Counter = field(default_factory=Counter)
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: ``verb``/``free`` are the WHOLE plan's free action for this build job -- unpausing
    #: three pumps is one job however the waves split them -- so a stage renders them as an
    #: aside and never as its own instruction.
    verb: str = "OK"
    free: int = 0
    note: str = ""
    key: tuple = ()
    instances: list[str] = field(default_factory=list)

    @property
    def running(self) -> int:
        """Machines PROVEN to have had power: they produced inside the last window. At
        most ``machines``: spread-out machines at a lower clock stand in for fewer."""
        return min(self.machines, sum(n for s, n in self.by_state.items() if s in RUNNING_STATES))

    @property
    def states(self) -> list[tuple[str, int]]:
        return _counted(self.by_state)

    @property
    def to_build(self) -> int:
        return max(0, self.machines - self.built)


@dataclass
class Stage:
    """One startup wave, matched against the save."""

    index: int
    rows: list[StageRow] = field(default_factory=list)
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    available_before: float = 0.0
    available_after: float = 0.0
    fill_s: float = 0.0
    waits_for_fill: bool = False

    @property
    def machines(self) -> int:
        return sum(r.machines for r in self.rows)

    @property
    def built(self) -> int:
        return sum(r.built for r in self.rows)

    def describe(self) -> str:
        """One phrase per stage, saying only what the save supports."""
        if self.built_max <= 0:
            return "not built"
        if not self.complete:
            span = f"{self.fraction_built:.0%}"
            if self.built_max != self.built and self.machines:
                span = f"{span}..{self.built_max / self.machines:.0%}"
            return f"{span} built"
        if not self.monitored:
            return "built"
        if self.running >= self.machines:
            return "built, all running"
        if self.running:
            return f"built, {self.running} running"
        return "built, none running"

    @property
    def built_max(self) -> int:
        return sum(r.built_max for r in self.rows)

    @property
    def running(self) -> int:
        return sum(r.running for r in self.rows)

    @property
    def monitored(self) -> int:
        return sum(n for s, n in self.by_state.items() if s in MONITORED_STATES)

    @property
    def by_state(self) -> Counter:
        total: Counter = Counter()
        for r in self.rows:
            total.update(r.by_state)
        return total

    @property
    def complete(self) -> bool:
        return self.built >= self.machines

    @property
    def fraction_built(self) -> float:
        return self.built / self.machines if self.machines else 1.0

    @property
    def dark(self) -> int:
        """Built machines with no proof of power, and no supply cause either.

        NOT the same as "unpowered": it is the residue after the save's own explanations
        have been taken out, and a monitor that has not caught up lands here too.
        """
        return sum(n for s, n in self.by_state.items() if s in DARK_STATES)


@dataclass
class Tracking:
    stages: list[Stage] = field(default_factory=list)
    ok: bool = True
    #: The stage the player is in: the first one not fully built. 0 when the whole plan
    #: stands, because the save cannot say which block of a built plant is energised.
    current: int = 0
    #: Name of the stored plan this partition came from. Empty means the numbering was
    #: derived from arguments given on the call and will renumber when they change.
    plan_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def machines(self) -> int:
        return sum(s.machines for s in self.stages)

    @property
    def built(self) -> int:
        return sum(s.built for s in self.stages)

    @property
    def running(self) -> int:
        return sum(s.running for s in self.stages)

    @property
    def monitored(self) -> int:
        """Built machines whose state was decided by reading the productivity monitor.

        Zero means the save yields NO evidence about energisation either way, which must
        never be printed as "nothing is running".
        """
        return sum(n for s in self.stages for st, n in s.by_state.items() if st in MONITORED_STATES)

    def headline(self, brief: bool = False) -> str:
        """Which stage the player is in; ``brief`` is the page's shorter wording."""
        if not self.ok or not self.stages:
            return ""
        count = len(self.stages)
        if self.current:
            here = next(s for s in self.stages if s.index == self.current)
            if brief:
                return (
                    f"you are in stage {self.current} of {count}: {here.fraction_built:.0%} "
                    f"built ({here.built}/{here.machines}), {here.running} proven running"
                )
            done = self.current - 1
            return (
                f"you are in STAGE {self.current} of {count}: "
                + (
                    f"stages 1-{done} complete, "
                    if done > 1
                    else "stage 1 complete, "
                    if done
                    else ""
                )
                + f"stage {self.current} is {here.fraction_built:.0%} built "
                f"({here.built}/{here.machines}) and {here.running} machine(s) in it are "
                "proven running"
            )
        if brief:
            return (
                f"every stage is built ({self.built}/{self.machines}), "
                f"{self.running} proven running"
            )
        return (
            f"every stage is built ({self.built}/{self.machines} machines). "
            f"{self.running} are proven running; the rest may be built-and-unpowered, "
            "which is what this plan expects until you energise them"
        )


def _counted(counter: Counter) -> list[tuple[str, int]]:
    return sorted(((s, n) for s, n in counter.items() if n), key=lambda sn: (-sn[1], sn[0]))


def partition_id(tracking: Tracking) -> str:
    """A short hash of which build jobs each stage energises, and how many of each."""
    if not tracking.ok or not tracking.stages:
        return ""
    shape = [
        [stage.index, [[repr(row.key), row.machines] for row in stage.rows]]
        for stage in tracking.stages
    ]
    return hashlib.sha1(json.dumps(shape).encode("utf-8")).hexdigest()[:10]


def machine_states(report: DiffReport, game: GameData, state: WorldState) -> dict[str, str]:
    """One health pass over every machine the diff matched: instance -> graph.health state."""
    matched = [name for r in report.rows for name in r.have_instances]
    if not matched:
        return {}
    return {m.instance: m.state for m in assess("plan", matched, game, state.projection).machines}


def _states_for(row, health: dict[str, str]) -> list[tuple[str, str, float]]:
    """This build job's matched machines with their clocks, running ones first.

    Identical machines are indistinguishable in the save -- nothing records which Refinery
    was meant for wave 2 -- so built machines are allotted to the EARLIEST wave that wants
    them and, within that, running ones first. Both halves assume progress was made in the
    order the startup sequence prescribes, which is the only rule the file supports.
    """
    clocks = list(row.have_clocks) + [1.0] * (len(row.have_instances) - len(row.have_clocks))
    triples = [
        (name, health.get(name, "unmonitored"), clock)
        for name, clock in zip(row.have_instances, clocks, strict=False)
    ]
    return sorted(triples, key=lambda p: (p[1] not in RUNNING_STATES, p[1]))


def track(
    prepared,
    run: Commissioning,
    report: DiffReport,
    game: GameData,
    state: WorldState,
    plan_name: str = "",
    health: dict[str, str] | None = None,
) -> Tracking:
    """Group a diff by startup wave: which stage is built, and which is proven running.

    ``commission`` owns the partition and ``build_diff`` owns the matching; this only joins
    them on ``diff.group_key``, so the two can never disagree about what one build job is.
    """
    out = Tracking(plan_name=plan_name)
    if not run.ok or not run.waves:
        out.ok = False
        out.warnings.append(
            "no startup order exists at this headroom, so the plan has no stages to "
            "match the save against"
        )
        return out

    by_key = {r.key: r for r in report.rows if r.key}
    key_of_pid = {p["pid"]: group_key(p) for p in prepared.solution.processes}

    # One health pass over every machine the diff matched, anywhere in the plan; split per
    # row it would rescan the whole projection once per build job.
    if health is None:
        health = machine_states(report, game, state)

    # Remaining pool per build job, consumed wave by wave BY RATE, so machines spread at a
    # lower clock fill a stage as the fewer machines they stand in for. `low` is the
    # pessimistic rate: where machines cannot be attributed, only those standing among the
    # plan's own are certainly its own and the rest may belong to any plant.
    pool: dict[tuple, list[tuple[str, str, float]]] = {}
    left: dict[tuple, float] = {}
    low: dict[tuple, float] = {}
    clock_of: dict[tuple, float] = {}
    for key, row in by_key.items():
        pool[key] = _states_for(row, health)
        clock_of[key] = row.plan_clock or 1.0
        left[key] = row.have_rate if row.have_instances else 0.0
        low[key] = left[key] if row.have_min is None else row.have_min * clock_of[key]

    for wave in run.waves:
        stage = Stage(
            index=wave.index,
            draw_mw=wave.draw_mw,
            generation_mw=wave.generation_mw,
            available_before=wave.available_before,
            available_after=wave.available_after,
            fill_s=wave.fill_s(),
            waits_for_fill=wave.waits_for_fill,
        )
        for energised in wave.rows:
            key = key_of_pid.get(energised.pid, ())
            diff_row = by_key.get(key)
            clock = clock_of.get(key, 1.0)
            wanted = energised.machines * clock
            got = min(wanted, left.get(key, 0.0))
            sure = min(wanted, low.get(key, 0.0))
            if key in left:
                left[key] -= got
                low[key] = max(0.0, low[key] - wanted)
            take, taken = [], 0.0
            rest = pool.get(key, [])
            while rest and taken < got - 1e-6:
                take.append(rest[0])
                taken += rest[0][2]
                rest = rest[1:]
            if key in pool:
                pool[key] = rest
            reached = min(energised.machines, rate_units(got, clock))
            certain = min(reached, rate_units(sure, clock))
            stage.rows.append(
                StageRow(
                    stage=wave.index,
                    label=energised.label,
                    kind=energised.kind,
                    building=energised.building,
                    machines=energised.machines,
                    total=energised.total,
                    built=certain,
                    built_max=reached,
                    by_state=Counter(s for _, s, _ in take),
                    key=key,
                    instances=[name for name, _, _ in take],
                    draw_mw=energised.draw_mw,
                    generation_mw=energised.generation_mw,
                    verb=diff_row.verb if diff_row else "OK",
                    free=diff_row.count if diff_row and diff_row.verb not in ("OK", "BUILD") else 0,
                    note=diff_row.note if diff_row else "",
                )
            )
        out.stages.append(stage)

    incomplete = [s.index for s in out.stages if not s.complete]
    out.current = incomplete[0] if incomplete else 0
    if not out.monitored:
        out.warnings.append(NO_MONITOR)
    return out


def live_feeders(g, st, floor_mw: float = 1.0) -> list[tuple[str, float]]:
    """Built extractors whose output currently reaches a running generator.

    Which of the machines already on the ground are load-bearing right now, which a startup
    order cannot answer on its own. The distribution is lopsided in practice -- one of
    sixteen Oil Extractors carrying all the running fuel generation -- so "repipe the
    extractors" is usually many safe moves and one that browns out the base.
    """
    return [
        (f"{name} {record['instance'].rsplit('.', 1)[-1][-10:]}", mw)
        for record, name, mw in feeder_records(g, st, floor_mw)
    ]


def feeder_records(g, st, floor_mw: float = 1.0) -> list[tuple[dict, str, float]]:
    """``live_feeders`` as (extractor record, building name, MW), largest first."""
    from ..factories.trace import power_at_risk

    out: list[tuple[dict, str, float]] = []
    for record in st.projection.get("extractors", ()):
        instance = record["instance"].rsplit(".", 1)[-1]
        mw, _, running = power_at_risk(st, g, [instance])
        if running and mw >= floor_mw:
            building = g.buildings.get(record.get("cls", ""))
            out.append((record, building.name if building else str(record.get("cls")), mw))
    out.sort(key=lambda row: -row[2])
    return out
