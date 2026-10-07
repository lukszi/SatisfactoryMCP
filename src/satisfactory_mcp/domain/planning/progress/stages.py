"""Which startup stage a plant is in: the startup waves matched back against the save."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData
from ...factories.health import assess
from ...world.state import WorldState
from .diff import DiffReport, DiffRow, solution_of, whole_machines
from .jobs import JobKey, group_key
from .startup import Commissioning

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..solver.prepare import PreparedPlan

__all__ = [
    "DARK_STATES",
    "ENERGISED_CAVEAT",
    "MONITORED_STATES",
    "NO_MONITOR",
    "RANGE_CAVEAT",
    "RUNNING_STATES",
    "Stage",
    "StageRow",
    "Tracking",
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
    by_state: Counter[str] = field(default_factory=Counter[str])
    draw_mw: float = 0.0
    generation_mw: float = 0.0
    #: ``verb``/``free`` are the WHOLE plan's free action for this build job -- unpausing
    #: three pumps is one job however the waves split them -- so a stage renders them as an
    #: aside and never as its own instruction.
    verb: str = "OK"
    free: int = 0
    note: str = ""
    key: JobKey = ()
    instances: list[str] = field(default_factory=list[str])

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
    rows: list[StageRow] = field(default_factory=list[StageRow])
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
    def by_state(self) -> Counter[str]:
        total: Counter[str] = Counter()
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
    stages: list[Stage] = field(default_factory=list[Stage])
    ok: bool = True
    #: The stage the player is in: the first one not fully built. 0 when the whole plan
    #: stands, because the save cannot say which block of a built plant is energised.
    current: int = 0
    #: Name of the stored plan this partition came from. Empty means the numbering was
    #: derived from arguments given on the call and will renumber when they change.
    plan_name: str = ""
    warnings: list[str] = field(default_factory=list[str])

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


def _counted(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(((s, n) for s, n in counter.items() if n), key=lambda sn: (-sn[1], sn[0]))


def partition_id(tracking: Tracking) -> str:
    """A short hash of which build jobs each stage energises, and how many of each."""
    if not tracking.ok or not tracking.stages:
        return ""
    shape = [
        [stage.index, [[repr(row.key), row.machines] for row in stage.rows]]
        for stage in tracking.stages
    ]
    digest = hashlib.sha1(json.dumps(shape).encode("utf-8"), usedforsecurity=False)
    return digest.hexdigest()[:10]


def machine_states(report: DiffReport, game: GameData, state: WorldState) -> dict[str, str]:
    """One health pass over every machine the diff matched: instance -> graph.health state."""
    matched = [name for r in report.rows for name in r.have_instances]
    if not matched:
        return {}
    return {m.instance: m.state for m in assess("plan", matched, game, state.projection).machines}


def _states_for(row: DiffRow, health: dict[str, str]) -> list[tuple[str, str, float]]:
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


class _JobPool:
    """One build job's matched machines, handed to the waves that want them BY RATE, so
    machines spread at a lower clock fill a stage as the fewer machines they stand in for.

    ``certain_rate_left`` is the pessimistic rate: where machines cannot be attributed, only
    those standing among the plan's own are certainly its own.
    """

    def __init__(self, row: DiffRow, health: dict[str, str]) -> None:
        self.machines = deque(_states_for(row, health))
        self.clock = row.plan_clock or 1.0
        self.rate_left = sum(clock for _, _, clock in self.machines)
        self.certain_rate_left = (
            self.rate_left if row.have_min is None else row.have_min * self.clock
        )

    def take(self, rate: float) -> list[tuple[str, str, float]]:
        """Machines off the front, running ones first, until their clocks reach ``rate``."""
        taken: list[tuple[str, str, float]] = []
        total = 0.0
        while self.machines and total < rate - 1e-6:
            machine = self.machines.popleft()
            taken.append(machine)
            total += machine[2]
        return taken


def track(
    prepared: PreparedPlan,
    startup: Commissioning,
    report: DiffReport,
    game: GameData,
    state: WorldState,
    plan_name: str = "",
    health: dict[str, str] | None = None,
) -> Tracking:
    """Group a diff by startup wave: which stage is built, and which is proven running.

    ``commission`` owns the partition and ``build_diff`` owns the matching; this only joins
    them on ``jobs.group_key``, so the two can never disagree about what one build job is.
    """
    out = Tracking(plan_name=plan_name)
    if not startup.ok or not startup.waves:
        out.ok = False
        out.warnings.append(
            "no startup order exists at this headroom, so the plan has no stages to "
            "match the save against"
        )
        return out

    by_key = {r.key: r for r in report.rows if r.key}
    key_of_pid = {p["pid"]: group_key(p) for p in solution_of(prepared).processes}

    # One health pass over every machine the diff matched, anywhere in the plan; split per
    # row it would rescan the whole projection once per build job.
    if health is None:
        health = machine_states(report, game, state)
    pools = {key: _JobPool(row, health) for key, row in by_key.items()}

    for wave in startup.waves:
        stage = Stage(
            index=wave.index,
            draw_mw=wave.draw_mw,
            generation_mw=wave.generation_mw,
            available_before=wave.available_before,
            available_after=wave.available_after,
            fill_s=wave.fill_s(),
            waits_for_fill=wave.waits_for_fill,
        )
        for wave_row in wave.rows:
            key: JobKey = key_of_pid.get(wave_row.pid, ())
            diff_row = by_key.get(key)
            pool = pools.get(key)
            clock = pool.clock if pool is not None else 1.0
            wanted = wave_row.machines * clock
            reached_rate = min(wanted, pool.rate_left if pool is not None else 0.0)
            certain_rate = min(wanted, pool.certain_rate_left if pool is not None else 0.0)
            taken: list[tuple[str, str, float]] = []
            if pool is not None:
                pool.rate_left -= reached_rate
                pool.certain_rate_left = max(0.0, pool.certain_rate_left - wanted)
                taken = pool.take(reached_rate)
            reached = min(wave_row.machines, whole_machines(reached_rate, clock))
            certain = min(reached, whole_machines(certain_rate, clock))
            stage.rows.append(
                StageRow(
                    stage=wave.index,
                    label=wave_row.label,
                    kind=wave_row.kind,
                    building=wave_row.building,
                    machines=wave_row.machines,
                    total=wave_row.total,
                    built=certain,
                    built_max=reached,
                    by_state=Counter(s for _, s, _ in taken),
                    key=key,
                    instances=[name for name, _, _ in taken],
                    draw_mw=wave_row.draw_mw,
                    generation_mw=wave_row.generation_mw,
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
