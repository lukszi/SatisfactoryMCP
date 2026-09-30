"""What to change to get from the factory in the save to the factory in the plan.

The hard part is not arithmetic, it is deciding which machine that already exists
COUNTS toward the plan. Three rules do the work, and all three are forced by what the
save actually stores rather than chosen for elegance.

**Identity, never position.** A manufacturer is identified by ``(building, recipe)``,
because ``mCurrentRecipe`` is a per-machine setting and is exactly what the player
would have to change. A generator is identified by its building alone, because a
generator has no recipe -- its fuel is whatever is piped into it, so two fuels are one
build job. An extractor is identified by the node it occupies, which the save records
directly and which is therefore an exact join.

Class-only matching for manufacturers is the failure this design exists to avoid. This
world has 36 Refineries; 5 run Alternate Heavy Oil Residue and 31 run seven other
recipes, making copper, plastic and alumina. "You have 36 Refineries, build 10" is
arithmetically true and would tell the player to break their copper line. Off-recipe
machines are reported as a reuse pool and never counted.

**Position reports; it never matches.** The region raster is +/-256 m advisory and
splits this world's single 458-building site across three region names, and 300 m
single-linkage merges the oil plant and the main base, which stand only ~900 m apart,
into one cluster. No spatial rule separates them, so none is used for matching.
Proximity decides only what to MENTION: which idle machines are plausibly reusable, and
what stands among the plant without being in the plan.

**Where identity is unavailable, emit a range.** Water Extractors have no recipe, and
all 23 in this save point at ``FGWaterVolume`` objects that are not node keys (OQ5), so
they cannot be matched at all. 31 needed against 23 built is reported as "build 8..27":
the lower bound counts every pump in the world, the upper counts only those standing
among the plan's own machines. A single number there would be a confident lie in
whichever direction it fell.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from ...core.gamedata.model import GameData
from ...core.text import plural
from ..spatial import geo
from ..spatial import nodes as nodes_mod
from ..world.state import WorldState
from .layout import chain_depth
from .optimize import MW, Solution
from .scenario import PlanRequest

__all__ = [
    "NEIGHBOUR_RADIUS_M",
    "RECLOCK_TOLERANCE",
    "CostLine",
    "DiffReport",
    "DiffRow",
    "build_diff",
    "group_key",
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
    #: What this row is matched ON (see group_key). The join key for anything that needs
    #: to say something else about the same build job, such as which startup wave it is in.
    key: tuple = ()
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


# ------------------------------------------------------------------- plan side


def _resource_of(proc: dict) -> str:
    """The single item an extractor column produces."""
    produced = [item for item, rate in proc.get("rates", {}).items() if rate > 0]
    return produced[0] if produced else ""


def group_key(proc: dict) -> tuple:
    """The identity a plan row is matched on, as a hashable tuple.

    Public because it is the join between the two things this package says about one
    machine: what to BUILD (here) and when to SWITCH IT ON (commission). Both must agree
    on what counts as the same build job, and the only way to guarantee that is for both
    to call this.
    """
    if proc["kind"] == "recipe":
        return ("recipe", proc["building_id"], proc["recipe"])
    if proc["kind"] == "generator":
        return ("generator", proc["building_id"])
    return ("extractor", proc["building_id"], _resource_of(proc), proc.get("purity", ""))


def _group_processes(sol: Solution) -> list[dict]:
    """Collapse solution rows into one entry per build job.

    Only generators actually merge, and that is the point: the plan runs 176 Fuel
    Generators on Fuel and 20 on Turbofuel, but that is 196 identical buildings and one
    plumbing decision, not two different machines to place.
    """
    groups: dict[tuple, dict] = {}
    for proc in sol.processes:
        key = group_key(proc)
        entry = groups.get(key)
        if entry is None:
            entry = {
                "key": key,
                "kind": proc["kind"],
                "building_id": proc["building_id"] or "",
                "building": proc["building"],
                "recipe": proc.get("recipe"),
                "resource": _resource_of(proc) if proc["kind"] == "extractor" else "",
                "purity": proc.get("purity", ""),
                "machines": 0,
                "clock": 0.0,
                "mw": 0.0,
                "labels": [],
                "rates": {},
            }
            groups[key] = entry
        entry["machines"] += proc["machines"]
        entry["clock"] += proc["machines"] * float(proc.get("clock") or 1.0)
        entry["mw"] += proc["mw"]
        entry["labels"].append((proc["label"], proc["machines"]))
        for item, rate in proc.get("rates", {}).items():
            if item != MW:
                entry["rates"][item] = entry["rates"].get(item, 0.0) + rate
    return list(groups.values())


# ------------------------------------------------------------------- save side


def _xy(record: dict) -> tuple[float, float] | None:
    pos = record.get("pos")
    return (pos[0], pos[1]) if pos else None


def _short(record: dict) -> str:
    """The leaf of an instanceName -- the spelling every selector and health check takes."""
    return str(record.get("instance") or "").rsplit(".", 1)[-1]


def _nearest_m(
    point: tuple[float, float] | None, others: list[tuple[float, float]]
) -> float | None:
    if point is None or not others:
        return None
    return min(geo.distance_m(point, other) for other in others)


@dataclass
class _SaveIndex:
    by_recipe: dict[tuple[str, str], list[dict]]
    by_generator: dict[str, list[dict]]
    by_extractor_class: dict[str, list[dict]]
    idle: dict[str, list[dict]]
    #: (extractor class, resource, purity) -> in-scope node rows already tapped.
    tapped: dict[tuple[str, str, str], list[dict]]
    #: (resource, purity) -> in-scope node rows with nothing on them.
    free: dict[tuple[str, str], list[dict]]
    #: node instanceName -> the extractor actor sitting on it.
    extractor_on: dict[str, dict]


def _index(state: WorldState, request: PlanRequest, scope: set[str] | None = None) -> _SaveIndex:
    """What the save already offers this plan.

    ``scope`` restricts reuse to one factory's machines. Without it, "you already have
    12 of these" counts constructors on the far side of the map that are busy doing
    something else, which is the wrong answer to "how far along is the aluminium setup".
    """

    def inside(record: dict) -> bool:
        return scope is None or record["instance"].rsplit(".", 1)[-1] in scope

    by_recipe: dict[tuple[str, str], list[dict]] = {}
    idle: dict[str, list[dict]] = {}
    for m in state.projection.get("machines", ()):
        if not inside(m):
            continue
        recipe = m.get("recipe")
        if recipe:
            by_recipe.setdefault((m["cls"], recipe), []).append(m)
        else:
            # No recipe set means no output, so reusing one has no opportunity cost.
            idle.setdefault(m["cls"], []).append(m)

    by_generator: dict[str, list[dict]] = {}
    for entry in state.projection.get("generators", ()):
        if inside(entry):
            by_generator.setdefault(entry["cls"], []).append(entry)

    by_extractor_class: dict[str, list[dict]] = {}
    extractor_on: dict[str, dict] = {}
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
    points = []
    for rows in index.tapped.values():
        for row in rows:
            actor = index.extractor_on.get(row["instance"])
            if actor and _xy(actor):
                points.append(_xy(actor))
    return geo.centroid(points)


def _save_id(state: WorldState) -> str:
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


def _matched(group: dict, index: _SaveIndex) -> list[dict]:
    """Machines in the save that already do this plan row's job.

    Recipe rows join on (building, recipe); a Refinery on another recipe is busy, not
    spare. Generator rows join on building only. Extractor rows join through the node,
    which is the only exact machine-level match the save supports.
    """
    if group["kind"] == "recipe":
        return list(index.by_recipe.get((group["building_id"], group["recipe"] or ""), []))
    if group["kind"] == "generator":
        return list(index.by_generator.get(group["building_id"], []))
    return [
        actor
        for row in index.tapped.get((group["building_id"], group["resource"], group["purity"]), [])
        if (actor := index.extractor_on.get(row["instance"])) is not None
    ]


def _node_backed(group: dict, index: _SaveIndex) -> bool:
    """Whether this extractor row has any node in scope to join against.

    Data-driven rather than a hardcoded class check: water volumes are simply absent
    from the node table, so a water row sees no candidates at all and falls back to
    counting the class. The same fallback would catch any future resource the table
    does not cover.
    """
    return bool(
        index.tapped.get((group["building_id"], group["resource"], group["purity"]))
        or index.free.get((group["resource"], group["purity"]))
    )


def _clock(record: dict) -> float:
    clock = record.get("clock")
    return 1.0 if clock is None else float(clock)


def _reclock_note(records: list[dict], to_build: int, group: dict) -> str:
    """A note when the built clocks plus the machines still to build at the plan's clock
    miss the plan's total, or "" when they meet it. More machines at a lower clock is
    the same rate, so neither the count nor one machine's clock decides it."""
    need, planned = group["machines"], group["clock"]
    if not records or not need or planned <= 0:
        return ""
    each = planned / need
    ratio = (sum(_clock(r) for r in records) + to_build * each) / planned
    if abs(ratio - 1.0) <= RECLOCK_TOLERANCE or (ratio > 1.0 and len(records) > need):
        return ""
    return f"clocks give {ratio * 100:.0f}% of the planned rate (plan: {need} at {each * 100:.4g}%)"


def _row_for(
    state: WorldState,
    group: dict,
    index: _SaveIndex,
    stage: int,
    anchor: tuple[float, float] | None,
    matched_points: list[tuple[float, float]],
    claimed_idle: set[str],
) -> DiffRow:
    need = group["machines"]
    records = _matched(group, index)
    notes: list[str] = []
    page: list[str] = []
    build_max: int | None = None
    have_min: int | None = None
    targets: list[tuple[str, float]] = []

    if group["kind"] == "extractor" and not _node_backed(group, index):
        # Nothing to join against, so fall back to counting the class. That cannot say
        # which pump serves which plant, so the answer has to be an interval.
        records = list(index.by_extractor_class.get(group["building_id"], []))
        near = [
            r
            for r in records
            if (d := _nearest_m(_xy(r), matched_points)) is not None and d <= NEIGHBOUR_RADIUS_M
        ]
        build_max = max(0, need - len(near))
        have_min = len(near)
        # Nearest first, so anything downstream that samples this row's machines samples
        # the ones plausibly at the plant before the ones 2.5 km away. It changes no
        # count -- both bounds are already fixed above -- only which machines get asked.
        close = {id(r) for r in near}
        records = [*near, *(r for r in records if id(r) not in close)]
        notes.append("no node link (OQ5), low bound counts every one built")
        if have_min != len(records):
            page.append("not tied to a node, so built is a range")
    elif group["kind"] == "extractor":
        free = sorted(
            index.free.get((group["resource"], group["purity"]), []),
            key=lambda r: geo.distance_m((r["x"], r["y"]), anchor) if anchor else 0.0,
        )
        targets = [
            (
                _short(r),
                geo.distance_m((r["x"], r["y"]), anchor) if anchor else 0.0,
            )
            for r in free[: max(0, need - len(records))]
        ]

    have = len(records)
    paused = [r for r in records if r.get("paused")]
    unpause = paused[: max(0, need - (have - len(paused)))]
    build = max(0, need - have)

    reused: list[dict] = []
    setrecipe = 0
    if group["kind"] == "recipe" and build > 0:
        for cand in index.idle.get(group["building_id"], []):
            if setrecipe >= build:
                break
            key = cand.get("instance") or ""
            if key in claimed_idle:
                continue
            distance = _nearest_m(_xy(cand), matched_points)
            if distance is not None and distance <= NEIGHBOUR_RADIUS_M:
                claimed_idle.add(key)
                setrecipe += 1
                reused.append(cand)
        build -= setrecipe
    if build_max is not None:
        build_max = max(build, build_max)

    verb, count = "OK", 0
    if unpause:
        verb, count = "UNPAUSE", len(unpause)
    elif setrecipe:
        verb, count = "SETRECIPE", setrecipe
    elif build > 0:
        verb, count = "BUILD", build

    if verb != "BUILD" and build > 0:
        notes.append(f"then BUILD {build}..{build_max}" if build_max else f"then BUILD {build}")
    if setrecipe:
        away = [d for d in (_nearest_m(_xy(r), [anchor] if anchor else []) for r in reused) if d]
        mean = sum(away) / len(away) if away else None
        idle = f"{setrecipe} idle {plural(group['building'], setrecipe)}"
        where = f" {mean / 1000:.1f}km out" if mean is not None else ""
        notes.append(f"{idle}{where}, no output today")
        page.append(f"{idle}{f' {mean:,.0f} m out' if mean is not None else ''}, no output today")

    if len(paused) > len(unpause) and (have_min is None or have_min >= need):
        spare = len(paused) - len(unpause)
        notes.append(f"{spare} {'more ' if unpause else ''}paused, not needed to cover this job")
        page.append(notes[-1])

    if have_min is None:
        reclock = _reclock_note([*records, *reused], build, group)
    else:
        reclock = _reclock_note(records[:have_min], build_max or 0, group)
    if reclock:
        # Not a change the plan asks for, so it never becomes the verb.
        notes.append(reclock)
        page.append(notes[-1])

    if len(group["labels"]) > 1:
        notes.append(
            " + ".join(f"{n} on {lbl.rsplit(' on ', 1)[-1]}" for lbl, n in group["labels"])
        )
        page.append(notes[-1])
    elif group["kind"] == "recipe" and have and verb == "BUILD":
        # Pre-empts "but I already own 36 Refineries": 31 of them are making copper,
        # plastic and alumina, and counting them would tell the player to break those.
        busy = sum(
            len(v)
            for (cls, rid), v in index.by_recipe.items()
            if cls == group["building_id"] and rid != group["recipe"]
        )
        if busy:
            notes.append(f"{busy} {plural(group['building'], busy)} busy on other recipes")
            page.append(notes[-1])
    if group["building_id"] and state.built(group["building_id"]) == 0:
        notes.append("NEW BUILDING TYPE")

    added = build + len(unpause) + setrecipe
    per_machine = group["mw"] / group["machines"] if group["machines"] else 0.0

    return DiffRow(
        stage=stage,
        key=group["key"],
        have_instances=[_short(r) for r in records],
        act_instances=[_short(r) for r in (unpause if verb == "UNPAUSE" else reused)]
        if verb in ("UNPAUSE", "SETRECIPE")
        else [],
        have_min=have_min,
        verb=verb,
        count=count,
        process=(
            group["labels"][0][0].split(" on ", 1)[-1]
            if group["kind"] == "extractor"
            else (group["labels"][0][0] if len(group["labels"]) == 1 else group["building"])
        ),
        building_id=group["building_id"],
        building=group["building"],
        need=need,
        have=have,
        build=build,
        build_max=build_max,
        have_distances=sorted(
            d
            for d in (_nearest_m(_xy(r), [anchor] if anchor else []) for r in records)
            if d is not None
        ),
        reuse=setrecipe,
        targets=targets,
        note="; ".join(notes),
        page_note="; ".join(page),
        delta_mw=added * per_machine,
    )


# ------------------------------------------------------------ cost, neighbours


def _cost(game: GameData, state: WorldState, rows: list[DiffRow]) -> list[CostLine]:
    """Materials for the build counts, against what the player can actually spend.

    Uses the LOWER bound of any range, since that is what will certainly be built, and
    WorldState.stock() rather than every stack in the world: machine buffers and pipe
    contents are not carryable, and summing them reported Water 5,556,375.
    """
    needed: dict[str, float] = {}
    for row in rows:
        b = game.buildings.get(row.building_id)
        if b is None or row.build <= 0:
            continue
        for flow in b.build_cost:
            needed[flow.item] = needed.get(flow.item, 0.0) + flow.amount * row.build

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
    groups: list[dict],
    matched_points: list[tuple[float, float]],
    claimed_idle: set[str],
) -> list[tuple[str, int]]:
    """Machines standing among the plan's own that the plan does not include.

    Reported, never actioned. There is deliberately no DISMANTLE verb: this is the
    player's factory. Three guards keep it from being alarming nonsense:

    * the proximity radius, which excludes this world's 32 Coal Generators at 887 m;
    * generators are skipped entirely, since every generator in the world "produces"
      the MW a max_mw plan produces and would all look superseded;
    * and a neighbour must share an ITEM with the plan. Without that last test the
      block filled up with 22 Iron Ingot Smelters and 19 Iron Rod Constructors -- the
      main base, swept in because one matched Assembler happens to stand in it. What
      is worth naming is the machines contending for the same materials: the Diluted
      Packaged Fuel route the plan's Blenders replace.
    """
    in_plan = {(g["building_id"], g["recipe"]) for g in groups if g["kind"] == "recipe"}
    plan_items = {item for g in groups for item in g["rates"] if item != MW}

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
    index = _index(state, request, scope)
    anchor = _anchor(index)
    groups = _group_processes(sol)

    # Build order is chain depth over the plan's own item graph, condensed so that the
    # genuine Recycled Plastic / Recycled Rubber cycle shares a stage instead of
    # running the relaxation to its cap. Extractors fall out at the bottom and
    # generators at the top without being special-cased.
    depths = chain_depth(
        [
            (
                [i for i, rate in g["rates"].items() if rate < 0],
                [i for i, rate in g["rates"].items() if rate > 0],
            )
            for g in groups
        ]
    )
    for group, depth in zip(groups, depths):
        group["depth"] = depth
    stage_of = {d: n + 1 for n, d in enumerate(sorted({g["depth"] for g in groups}))}

    # Every matched machine, gathered before any row is built: the proximity tests need
    # the whole plant, not the part of it seen so far.
    matched_points = [
        p for g in groups for p in (_xy(r) for r in _matched(g, index)) if p is not None
    ]

    claimed_idle: set[str] = set()
    rows = [
        _row_for(state, g, index, stage_of[g["depth"]], anchor, matched_points, claimed_idle)
        for g in sorted(groups, key=lambda g: (g["depth"], -g["machines"]))
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
        cost=_cost(game, state, rows),
        neighbours=_neighbours(game, state, groups, matched_points, claimed_idle),
        notes=notes,
        to_build=sum(r.build for r in rows),
        to_build_max=sum(r.build_max if r.build_max is not None else r.build for r in rows),
        headroom_mw=headroom,
        deficit_mw=deficit,
        slices=slices,
        anchor=anchor,
        save_id=_save_id(state),
    )
