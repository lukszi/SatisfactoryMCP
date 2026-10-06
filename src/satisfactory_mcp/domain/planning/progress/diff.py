"""What to change to get from the factory in the save to the factory in the plan.

Deciding which existing machine COUNTS toward the plan is the hard part: identity never
position, position only seeds, and a range where identity is unavailable. docs/planning.md
§8.6 has the rules and the measurements that forced them.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ....core.saveio.schema import (
    BuildableRecord,
    ExtractorRecord,
    GeneratorRecord,
    MachineRecord,
)
from ....core.text import plural
from ...spatial import geo
from ...spatial import nodes as nodes_mod
from ...world.state import WorldState
from ..layout.materials import cost_of
from ..solver.graph import chain_depth_of_rates
from ..solver.model import MW, Solution
from ..solver.scenario import PlanRequest
from .jobs import BuildJob, JobKey, group_processes

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ..solver.prepare import PreparedPlan
    from .built import BuiltAt

__all__ = [
    "NEIGHBOUR_RADIUS_M",
    "RECLOCK_TOLERANCE",
    "CostLine",
    "DiffReport",
    "DiffRow",
    "build_diff",
    "machine_rate",
    "request_of",
    "save_id",
    "solution_of",
    "whole_machines",
]

#: How close a machine must stand to one of the plan's own matched machines before it
#: counts as plausibly part of this plant. Borrowed from the node-clustering constant;
#: it gates reporting only, so approximately right is enough. Verified to exclude this
#: world's 32 Coal Generators, which sit 887-1060 m from the oil plant.
NEIGHBOUR_RADIUS_M = 200.0

#: How far the built clocks' total may stray from the plan's before a job gets a clock
#: note, as a share of the plan's total (docs/planning.md, "Clocks").
RECLOCK_TOLERANCE = 0.02


@dataclass
class DiffRow:
    """One build job from the plan, and what the player does about it."""

    stage: int
    verb: str
    count: int
    process: str
    building_id: str
    building: str
    need: int
    have: int
    #: Machines still to place after the free actions. The lower bound when ambiguous.
    build: int = 0
    #: Upper bound on ``build``; None when the match is exact.
    build_max: int | None = None
    #: Lower bound on ``have`` where identity is unavailable -- the machines of this
    #: class standing among the plan's own, as opposed to every one in the world. None
    #: when the match is exact and ``have`` needs no interval.
    have_min: int | None = None
    #: What this row is matched ON (see ``jobs.group_key``). The join key for anything that needs
    #: to say something else about the same build job, such as which startup wave it is in.
    key: JobKey = ()
    #: instanceNames of the machines counted in ``have``, so a caller can ask the save
    #: what those machines are actually doing rather than only how many there are.
    have_instances: list[str] = field(default_factory=list)
    #: instanceNames the VERB applies to: the paused machines for UNPAUSE, the idle ones
    #: being re-recipe'd for SETRECIPE. Never ``have_instances[:count]`` -- the paused
    #: three are anywhere in the matched set, and the idle ones are not in it at all.
    act_instances: list[str] = field(default_factory=list)
    #: Distance in metres of each matched machine from the plan's ground anchor.
    have_distances: list[float] = field(default_factory=list)
    #: (node id, metres from the anchor) to build on. Ids paste back as node: selectors.
    targets: list[tuple[str, float]] = field(default_factory=list)
    #: Idle machines re-recipe'd into this row rather than built.
    reuse: int = 0
    note: str = ""
    #: ``note`` for the page: no issue codes, and nothing the action cell or a chip says.
    page_note: str = ""
    #: MW these actions ADD. Incremental on purpose: machines that already exist and
    #: already run are already in the world's draw, so charging the plan's full figure
    #: would double-count them and overstate what the build needs.
    delta_mw: float = 0.0
    #: The job's rate in full-speed machines: planned, and summed over the counted clocks.
    #: ``have`` and ``build`` are these over ``plan_clock`` (docs/planning.md, "Counting by rate").
    need_rate: float = 0.0
    have_rate: float = 0.0
    plan_clock: float = 1.0
    #: Each counted machine's clock, beside ``have_instances``.
    have_clocks: list[float] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.verb != "OK"


@dataclass
class CostLine:
    item: str
    name: str
    need: float
    stock: float
    #: Machines in the save whose current recipe produces this item. Zero means the
    #: player has no production line for it at all, which is a different problem from
    #: merely needing a lot.
    lines: int

    @property
    def shortfall(self) -> float:
        return max(0.0, self.need - self.stock)


@dataclass
class DiffReport:
    rows: list[DiffRow]
    cost: list[CostLine]
    #: (label, count) for machines standing among the plan's own but not in the plan.
    #: Informational only: there is deliberately no DISMANTLE action.
    neighbours: list[tuple[str, int]]
    notes: list[str]
    to_build: int
    to_build_max: int
    headroom_mw: float
    #: Deepest the cumulative incremental power balance dips while building in order.
    deficit_mw: float
    slices: int
    anchor: tuple[float, float] | None
    save_id: str
    #: Where a stored plan's built machines were found and how sure that is.
    built_at: BuiltAt | None = None


def solution_of(prepared: PreparedPlan) -> Solution:
    """The solution of a plan whose ``failure`` the caller has ruled out."""
    assert prepared.solution is not None, "the plan did not solve"
    return prepared.solution


def request_of(prepared: PreparedPlan) -> PlanRequest:
    """The request of a plan whose ``failure`` the caller has ruled out."""
    assert prepared.request is not None, "the plan did not solve"
    return prepared.request


# ------------------------------------------------------------------- save side


def _xy(record: BuildableRecord) -> tuple[float, float] | None:
    pos = record.get("pos")
    return (pos[0], pos[1]) if pos else None


def _nearest_m(
    point: tuple[float, float] | None, others: list[tuple[float, float]]
) -> float | None:
    if point is None or not others:
        return None
    return min(geo.distance_m(point, other) for other in others)


def machine_rate(record: BuildableRecord) -> float:
    """One machine's rate in full-speed machines: its clock, and 1.0 where the save omits it."""
    clock = record.get("clock")
    return 1.0 if clock is None else float(clock)


def whole_machines(rate: float, clock: float) -> int:
    """Whole machines at ``clock`` that ``rate`` covers."""
    return math.floor(rate / clock + 1e-6) if clock > 0 else 0


def _machines_to_add(need_rate: float, have_rate: float, clock: float) -> int:
    """Machines at ``clock`` still to add before ``have_rate`` meets ``need_rate``."""
    gap = need_rate - have_rate
    return max(0, math.ceil(gap / clock - 1e-6)) if clock > 0 and gap > 0 else 0


@dataclass
class _SaveIndex:
    by_recipe: dict[tuple[str, str], list[MachineRecord]]
    by_generator: dict[str, list[GeneratorRecord]]
    by_extractor_class: dict[str, list[ExtractorRecord]]
    idle: dict[str, list[MachineRecord]]
    #: (extractor class, resource, purity) -> in-scope node rows already tapped.
    tapped: dict[tuple[str, str, str], list[dict]]
    #: (resource, purity) -> in-scope node rows with nothing on them.
    free: dict[tuple[str, str], list[dict]]
    #: node instanceName -> the extractor actor sitting on it.
    extractor_on: dict[str, ExtractorRecord]


def _index_save(
    state: WorldState, request: PlanRequest, scope: set[str] | None = None
) -> _SaveIndex:
    """What the save already offers this plan.

    ``scope`` restricts reuse to one factory's machines. Without it, "you already have
    12 of these" counts constructors on the far side of the map that are busy doing
    something else, which is the wrong answer to "how far along is the aluminium setup".
    """

    def inside(record: BuildableRecord) -> bool:
        return scope is None or instance_leaf(record["instance"]) in scope

    by_recipe: dict[tuple[str, str], list[MachineRecord]] = {}
    idle: dict[str, list[MachineRecord]] = {}
    for m in state.projection.get("machines", ()):
        if not inside(m):
            continue
        recipe = m.get("recipe")
        if recipe:
            by_recipe.setdefault((m["cls"], recipe), []).append(m)
        else:
            # No recipe set means no output, so reusing one has no opportunity cost.
            idle.setdefault(m["cls"], []).append(m)

    by_generator: dict[str, list[GeneratorRecord]] = {}
    for entry in state.projection.get("generators", ()):
        if inside(entry):
            by_generator.setdefault(entry["cls"], []).append(entry)

    by_extractor_class: dict[str, list[ExtractorRecord]] = {}
    extractor_on: dict[str, ExtractorRecord] = {}
    in_scope_nodes: set[str] = set()
    for entry in state.projection.get("extractors", ()):
        if inside(entry):
            by_extractor_class.setdefault(entry["cls"], []).append(entry)
            if entry.get("node"):
                in_scope_nodes.add(entry["node"])
        if entry.get("node"):
            # Kept whole: a node tapped by ANOTHER factory is still occupied, and the
            # plan must not be told it is free.
            extractor_on[entry["node"]] = entry

    # The one exact machine match available: annotate() resolved node -> extractor from
    # mExtractableResource, and the plan's extractor columns were built from these very
    # rows, so this join needs no inference at all.
    tapped: dict[tuple[str, str, str], list[dict]] = {}
    free: dict[tuple[str, str], list[dict]] = {}
    for row in request.node_rows:
        if row["kind"] != "node" or row["rate"] <= 0:
            continue
        if row["tapped"]:
            # Only a node this factory taps counts as already built for it. One tapped
            # by a different factory is neither reusable NOR free -- it drops out of
            # both, because offering it as free would plan a second miner onto it.
            if scope is not None and row["instance"] not in in_scope_nodes:
                continue
            tapped.setdefault((row["tapped_by"], row["resource"], row["purity"]), []).append(row)
        else:
            free.setdefault((row["resource"], row["purity"]), []).append(row)
    return _SaveIndex(by_recipe, by_generator, by_extractor_class, idle, tapped, free, extractor_on)


def _anchor(index: _SaveIndex) -> tuple[float, float] | None:
    """The plan's centre of gravity on the ground.

    Taken from the in-scope tapped extractors, because those are the only machines a
    plan actually pins to a coordinate. Everything else could be built anywhere, so
    anchoring on it would be a preference dressed up as a derivation.
    """
    points: list[tuple[float, float]] = []
    for rows in index.tapped.values():
        for row in rows:
            actor = index.extractor_on.get(row["instance"])
            xy = _xy(actor) if actor else None
            if xy:
                points.append(xy)
    return geo.centroid(points)


def save_id(state: WorldState) -> str:
    """Short hash of the machine census.

    Pairs with the plan id: same plan id and a different save id means the plan did not
    move but the factory did, which is exactly what a player wants to see mid-build.
    """
    census = sorted(
        f"{r['cls']}|{r.get('recipe') or r.get('fuel') or r.get('node') or ''}|{r.get('paused')}"
        for r in (
            *state.projection.get("machines", ()),
            *state.projection.get("extractors", ()),
            *state.projection.get("generators", ()),
        )
    )
    return hashlib.sha256("\n".join(census).encode("utf-8")).hexdigest()[:4]


# ----------------------------------------------------------------- the matching


def _machines_doing(job: BuildJob, index: _SaveIndex) -> list[BuildableRecord]:
    """Machines in the save that already do this plan row's job.

    Recipe rows join on (building, recipe); a Refinery on another recipe is busy, not
    spare. Generator rows join on building only. Extractor rows join through the node,
    which is the only exact machine-level match the save supports.
    """
    if job.kind == "recipe":
        return list(index.by_recipe.get((job.building_id, job.recipe or ""), []))
    if job.kind == "generator":
        return list(index.by_generator.get(job.building_id, []))
    return [
        actor
        for row in index.tapped.get((job.building_id, job.resource, job.purity), [])
        if (actor := index.extractor_on.get(row["instance"])) is not None
    ]


def _node_backed(job: BuildJob, index: _SaveIndex) -> bool:
    """Whether this extractor row has any node in scope to join against.

    Data-driven rather than a hardcoded class check: water volumes are simply absent
    from the node table, so a water row sees no candidates at all and falls back to
    counting the class. The same fallback would catch any future resource the table
    does not cover.
    """
    return bool(
        index.tapped.get((job.building_id, job.resource, job.purity))
        or index.free.get((job.resource, job.purity))
    )


def _plan_clock(job: BuildJob) -> float:
    """The clock the plan runs this job at, 1.0 when it only differs by a derived ratio."""
    clock = job.clock_sum / job.machines if job.machines else 1.0
    return 1.0 if abs(clock - 1.0) <= RECLOCK_TOLERANCE else clock


def _reclock_note(records: Sequence[BuildableRecord], to_build: int, job: BuildJob) -> str:
    """A note when the built clocks plus the machines still to build at the plan's clock
    miss the plan's total, or "" when they meet it. More machines at a lower clock is
    the same rate, so neither the count nor one machine's clock decides it."""
    need, planned = job.machines, job.clock_sum
    if not records or not need or planned <= 0:
        return ""
    each = planned / need
    ratio = (sum(machine_rate(r) for r in records) + to_build * each) / planned
    if abs(ratio - 1.0) <= RECLOCK_TOLERANCE or (ratio > 1.0 and len(records) > need):
        return ""
    return f"clocks give {ratio * 100:.0f}% of the planned rate (plan: {need} at {each * 100:.4g}%)"


@dataclass
class _Notes:
    """A row's notes in chat's words and in the page's, which leave some out or reword them."""

    chat: list[str] = field(default_factory=list)
    page: list[str] = field(default_factory=list)

    def both(self, text: str) -> None:
        self.chat.append(text)
        self.page.append(text)


def _class_count_range(
    job: BuildJob,
    index: _SaveIndex,
    matched_points: list[tuple[float, float]],
    need_rate: float,
    plan_clock: float,
) -> tuple[list[BuildableRecord], list[BuildableRecord], int, int]:
    """Count a job with no node to join on by its class: (machines nearest first, the ones
    among the plant, the most still to build, the fewest already built)."""
    records: list[BuildableRecord] = list(index.by_extractor_class.get(job.building_id, []))
    near = [
        r
        for r in records
        if (d := _nearest_m(_xy(r), matched_points)) is not None and d <= NEIGHBOUR_RADIUS_M
    ]
    near_rate = sum(machine_rate(r) for r in near)
    build_max = _machines_to_add(need_rate, near_rate, plan_clock)
    have_min = whole_machines(near_rate, plan_clock)
    # Nearest first, so anything downstream that samples this row's machines samples the
    # ones plausibly at the plant before the ones 2.5 km away; no count changes.
    close = {id(r) for r in near}
    return [*near, *(r for r in records if id(r) not in close)], near, build_max, have_min


def _free_node_targets(
    job: BuildJob, index: _SaveIndex, anchor: tuple[float, float] | None, count: int
) -> list[tuple[str, float]]:
    """The ``count`` free nodes nearest the anchor to build this extractor job on."""

    def metres(row: dict) -> float:
        return geo.distance_m((row["x"], row["y"]), anchor) if anchor else 0.0

    free = sorted(index.free.get((job.resource, job.purity), []), key=metres)
    return [(instance_leaf(r.get("instance")), metres(r)) for r in free[: max(0, count)]]


def _claim_idle(
    job: BuildJob,
    index: _SaveIndex,
    matched_points: list[tuple[float, float]],
    claimed_idle: set[str],
    build: int,
) -> list[MachineRecord]:
    """Idle machines of this job's class among the plant, up to ``build``, claimed once."""
    reassigned_machines: list[MachineRecord] = []
    if job.kind != "recipe" or build <= 0:
        return reassigned_machines
    for idle_machine in index.idle.get(job.building_id, []):
        if len(reassigned_machines) >= build:
            break
        key = idle_machine.get("instance") or ""
        if key in claimed_idle:
            continue
        distance = _nearest_m(_xy(idle_machine), matched_points)
        if distance is not None and distance <= NEIGHBOUR_RADIUS_M:
            claimed_idle.add(key)
            reassigned_machines.append(idle_machine)
    return reassigned_machines


def _choose_verb(
    unpause: Sequence[BuildableRecord], reassigned: int, build: int
) -> tuple[str, int]:
    """The one action a row asks for, free ones first: unpause, then set a recipe, then build."""
    if unpause:
        return "UNPAUSE", len(unpause)
    if reassigned:
        return "SETRECIPE", reassigned
    if build > 0:
        return "BUILD", build
    return "OK", 0


def _process_label(job: BuildJob) -> str:
    if job.kind == "extractor":
        return job.labels[0][0].split(" on ", 1)[-1]
    return job.labels[0][0] if len(job.labels) == 1 else job.building


def _row_for(
    state: WorldState,
    job: BuildJob,
    index: _SaveIndex,
    stage: int,
    anchor: tuple[float, float] | None,
    matched_points: list[tuple[float, float]],
    claimed_idle: set[str],
) -> DiffRow:
    need = job.machines
    plan_clock = _plan_clock(job)
    need_rate = need * plan_clock
    notes = _Notes()
    built = _count_built(job, index, anchor, matched_points, need_rate, plan_clock, notes)
    records, have_min = built.records, built.have_min

    have_rate = sum(machine_rate(r) for r in records)
    have = whole_machines(have_rate, plan_clock)
    paused = [r for r in records if r.get("paused")]
    unpause = paused[: max(0, need - (have - len(paused)))]
    build = _machines_to_add(need_rate, have_rate, plan_clock)

    reassigned_machines = _claim_idle(job, index, matched_points, claimed_idle, build)
    reassigned = len(reassigned_machines)
    build -= reassigned
    build_max = None if built.build_max is None else max(build, built.build_max)

    verb, count = _choose_verb(unpause, reassigned, build)
    _note_actions(notes, job, anchor, verb, build, build_max, reassigned_machines)

    if len(paused) > len(unpause) and (have_min is None or have_min >= need):
        spare = len(paused) - len(unpause)
        notes.both(f"{spare} {'more ' if unpause else ''}paused, not needed to cover this job")

    if have_min is None:
        reclock = _reclock_note([*records, *reassigned_machines], build, job)
    else:
        reclock = _reclock_note(records[: len(built.near)], build_max or 0, job)
    if reclock:
        # Not a change the plan asks for, so it never becomes the verb.
        notes.both(reclock)
    _note_context(notes, state, job, index, have, verb)

    added = build + len(unpause) + reassigned
    per_machine = job.mw / job.machines if job.machines else 0.0
    acted = unpause if verb == "UNPAUSE" else reassigned_machines

    return DiffRow(
        stage=stage,
        key=job.key,
        have_instances=[instance_leaf(r.get("instance")) for r in records],
        act_instances=[instance_leaf(r.get("instance")) for r in acted]
        if verb in ("UNPAUSE", "SETRECIPE")
        else [],
        have_min=have_min,
        verb=verb,
        count=count,
        process=_process_label(job),
        building_id=job.building_id,
        building=job.building,
        need=need,
        have=have,
        build=build,
        build_max=build_max,
        have_distances=sorted(
            d
            for d in (_nearest_m(_xy(r), [anchor] if anchor else []) for r in records)
            if d is not None
        ),
        reuse=reassigned,
        targets=built.targets,
        note="; ".join(notes.chat),
        page_note="; ".join(notes.page),
        delta_mw=added * per_machine,
        need_rate=need_rate,
        have_rate=have_rate,
        plan_clock=plan_clock,
        have_clocks=[machine_rate(r) for r in records],
    )


@dataclass
class _Built:
    """The machines that count toward one job, and the interval when identity is missing."""

    records: list[BuildableRecord]
    near: list[BuildableRecord] = field(default_factory=list[BuildableRecord])
    build_max: int | None = None
    have_min: int | None = None
    targets: list[tuple[str, float]] = field(default_factory=list)


def _count_built(
    job: BuildJob,
    index: _SaveIndex,
    anchor: tuple[float, float] | None,
    matched_points: list[tuple[float, float]],
    need_rate: float,
    plan_clock: float,
    notes: _Notes,
) -> _Built:
    """What already does this job, by identity; a range for extractors with no node link."""
    records = _machines_doing(job, index)
    if job.kind == "extractor" and not _node_backed(job, index):
        # Nothing to join against, so fall back to counting the class. That cannot say
        # which pump serves which plant, so the answer has to be an interval.
        records, near, build_max, have_min = _class_count_range(
            job, index, matched_points, need_rate, plan_clock
        )
        notes.chat.append("no node link (OQ5), low bound counts every one built")
        if len(near) != len(records):
            notes.page.append("not tied to a node, so built is a range")
        return _Built(records, near, build_max, have_min)
    if job.kind == "extractor":
        return _Built(
            records, targets=_free_node_targets(job, index, anchor, job.machines - len(records))
        )
    return _Built(records)


def _note_actions(
    notes: _Notes,
    job: BuildJob,
    anchor: tuple[float, float] | None,
    verb: str,
    build: int,
    build_max: int | None,
    reassigned_machines: Sequence[BuildableRecord],
) -> None:
    """The builds that follow a cheaper verb, and the idle machines it reassigns."""
    reassigned = len(reassigned_machines)
    if verb != "BUILD" and build > 0:
        notes.chat.append(
            f"then BUILD {build}..{build_max}" if build_max else f"then BUILD {build}"
        )
    if reassigned:
        away = [
            d
            for d in (_nearest_m(_xy(r), [anchor] if anchor else []) for r in reassigned_machines)
            if d
        ]
        mean = sum(away) / len(away) if away else None
        idle = f"{reassigned} idle {plural(job.building, reassigned)}"
        where = f" {mean / 1000:.1f}km out" if mean is not None else ""
        notes.chat.append(f"{idle}{where}, no output today")
        notes.page.append(
            f"{idle}{f' {mean:,.0f} m out' if mean is not None else ''}, no output today"
        )


def _note_context(
    notes: _Notes, state: WorldState, job: BuildJob, index: _SaveIndex, have: int, verb: str
) -> None:
    """Which labels share the job, machines busy on other recipes, a first-ever building."""
    if len(job.labels) > 1:
        notes.both(" + ".join(f"{n} on {lbl.rsplit(' on ', 1)[-1]}" for lbl, n in job.labels))
    elif job.kind == "recipe" and have and verb == "BUILD":
        # Pre-empts "but I already own 36 Refineries": 31 of them are making copper,
        # plastic and alumina, and counting them would tell the player to break those.
        busy = sum(
            len(v)
            for (cls, rid), v in index.by_recipe.items()
            if cls == job.building_id and rid != job.recipe
        )
        if busy:
            notes.both(f"{busy} {plural(job.building, busy)} busy on other recipes")
    if job.building_id and state.built(job.building_id) == 0:
        notes.chat.append("NEW BUILDING TYPE")


# ------------------------------------------------------------ cost, neighbours


def _shortfall_lines(game: GameData, state: WorldState, rows: list[DiffRow]) -> list[CostLine]:
    """Materials for the build counts, against what the player can actually spend.

    Uses the LOWER bound of any range, since that is what will certainly be built, and
    WorldState.stock() rather than every stack in the world: machine buffers and pipe
    contents are not carryable, and summing them reported Water 5,556,375.
    """
    needed: dict[str, float] = {}
    for row in rows:
        if row.build <= 0:
            continue
        for item, amount in cost_of(game, row.building_id, row.build).parts.items():
            needed[item] = needed.get(item, 0.0) + amount

    stock = state.stock()
    produced: dict[str, int] = {}
    for m in state.projection.get("machines", ()):
        recipe = game.recipes.get(m.get("recipe") or "")
        if recipe is None:
            continue
        for flow in recipe.products:
            produced[flow.item] = produced.get(flow.item, 0) + 1

    lines = [
        CostLine(
            item=item,
            name=game.item_name(item),
            need=amount,
            stock=stock.get(item, 0.0),
            lines=produced.get(item, 0),
        )
        for item, amount in needed.items()
    ]
    # Only what the player is short of, hardest first. "Hardest" is the shortfall over
    # the number of machines already making it, so an item with no line at all outranks
    # a larger number that an existing line already covers.
    lines = [line for line in lines if line.shortfall > 0]
    # An item with no automatable recipe at all (Portable Miner) has zero lines by
    # nature, not by neglect, so it must not outrank a real missing production line.
    lines.sort(
        key=lambda c: (
            c.lines > 0 or not game.producers_of(c.item, "part"),
            -c.shortfall / max(c.lines, 1),
        )
    )
    return lines


def _neighbours(
    game: GameData,
    state: WorldState,
    jobs: list[BuildJob],
    matched_points: list[tuple[float, float]],
    claimed_idle: set[str],
) -> list[tuple[str, int]]:
    """Machines standing among the plan's own that the plan does not include.

    Reported, never actioned: there is deliberately no DISMANTLE verb. The radius, the
    skipped generators and the shared-item test keep it from naming the whole main base
    (docs/planning.md §8.6).
    """
    in_plan = {(j.building_id, j.recipe) for j in jobs if j.kind == "recipe"}
    plan_items = {item for j in jobs for item in j.rates if item != MW}

    counts: dict[str, int] = {}
    for m in state.projection.get("machines", ()):
        recipe = m.get("recipe")
        if not recipe or (m["cls"], recipe) in in_plan:
            continue
        if (m.get("instance") or "") in claimed_idle:
            continue
        r = game.recipes.get(recipe)
        if r is None:
            continue
        touches = {f.item for f in r.ingredients} | {f.item for f in r.products}
        if not touches & plan_items:
            continue
        distance = _nearest_m(_xy(m), matched_points)
        if distance is None or distance > NEIGHBOUR_RADIUS_M:
            continue
        b = game.buildings.get(m["cls"])
        label = f"{b.name if b else m['cls']} {r.name}"
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


# ----------------------------------------------------------------------- entry


def build_diff(
    game: GameData,
    state: WorldState,
    sol: Solution,
    request: PlanRequest,
    scope: set[str] | None = None,
    biomass: bool = False,
) -> DiffReport:
    """Match a solved plan against the save and derive the actions to reach it.

    ``scope`` limits what counts as already built to one factory's machines.
    """
    index = _index_save(state, request, scope)
    anchor = _anchor(index)
    jobs = group_processes(sol)

    # Build order is chain depth over the plan's own item graph, condensed so that the
    # genuine Recycled Plastic / Recycled Rubber cycle shares a stage.
    for job, depth in zip(jobs, chain_depth_of_rates([job.rates for job in jobs])):
        job.depth = depth
    stage_of = {d: n + 1 for n, d in enumerate(sorted({job.depth for job in jobs}))}

    # Every matched machine, gathered before any row is built: the proximity tests need
    # the whole plant, not the part of it seen so far.
    matched_points = [
        p for job in jobs for p in (_xy(r) for r in _machines_doing(job, index)) if p is not None
    ]

    claimed_idle: set[str] = set()
    rows = [
        _row_for(state, job, index, stage_of[job.depth], anchor, matched_points, claimed_idle)
        for job in sorted(jobs, key=lambda j: (j.depth, -j.machines))
    ]

    power = state.power_report(biomass=biomass)
    headroom = power["headroom_mw"]
    # Cumulative INCREMENTAL power while building in stage order. Charging the plan's
    # own total would double-count every machine that already exists and already draws.
    running = 0.0
    trough = 0.0
    for stage in sorted({r.stage for r in rows}):
        running += sum(r.delta_mw for r in rows if r.stage == stage)
        trough = min(trough, running)
    deficit = -trough
    slices = math.ceil(deficit / headroom) if deficit > 0 and headroom > 0 else 1

    notes = list(request.selection.errors)
    # Only the ones that could actually be occupying a table node. Water pumps make up
    # the bulk of the unresolved list and are handled by the range instead, so counting
    # them here would report 26 phantom hazards on top of an answer that already says so.
    shadowing = [
        e
        for e in nodes_mod.unresolved_extractors(state.projection)
        if e["cls"] in nodes_mod.EXTRACTOR_FOR_KIND["node"]
    ]
    if shadowing:
        notes.append(
            f"{len(shadowing)} {plural('extractor', len(shadowing))} "
            "unmatched to a node, so a node that looks "
            "free may already be taken"
        )

    return DiffReport(
        rows=rows,
        cost=_shortfall_lines(game, state, rows),
        neighbours=_neighbours(game, state, jobs, matched_points, claimed_idle),
        notes=notes,
        to_build=sum(r.build for r in rows),
        to_build_max=sum(r.build_max if r.build_max is not None else r.build for r in rows),
        headroom_mw=headroom,
        deficit_mw=deficit,
        slices=slices,
        anchor=anchor,
        save_id=save_id(state),
    )
