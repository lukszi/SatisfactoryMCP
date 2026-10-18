"""A stored plan made from a built factory: the request that reproduces it, and how a solve of
that request differs from what stands.

docs/planning.md, "A plan from a built factory", has the mapping and why each piece is so.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from ....core.gamedata.model import GameData
from ....core.saveio.records import instance_leaf
from ....core.saveio.schema import BuildableRecord
from ...factories.labels import edit_stamp
from ...factories.query import FactoryView, build_view
from ...factories.select import SelectorError
from ...spatial import geo
from ...world.state import WorldState
from .. import siting as siting_mod
from ..readout import summary
from ..readout.views import SolveResponse, SolveRow
from ..solver.scenario import build_scenario
from ..stored.plan_args import PlanArgs
from ..stored.planlog import Actor, NameTaken, PlanLog, PlanState, Pushed
from ..stored.views import PlanStamp

__all__ = ["AsBuilt", "BuiltRow", "FromFactory", "compare", "map_factory", "plan_from_factory"]

SUFFIX = "as built"
NAME_TRIES = 50
#: A difference smaller than this share of the built figure is rounding, not a difference.
REL_TOL = 0.01
MW_TOL = 1.0
#: Rates are kept to this many decimals: exports rounded down, supplies rounded up.
DP = 4
#: A net rate below this, items/min, is float residue rather than a flow.
NOISE = 1e-3
EXTRACT = "extract:"


@dataclass
class BuiltRow:
    """One recipe, or one resource's extractors, as built: running machines and their clocks."""

    label: str
    building: str
    clocks: list[float] = field(default_factory=list[float])
    #: Items/min an extractor row extracts; a recipe row is compared on its clocks instead.
    rate: float = 0.0

    @property
    def machines(self) -> int:
        return len(self.clocks)

    @property
    def equivalents(self) -> float:
        return sum(self.clocks)


@dataclass
class AsBuilt:
    """The plan request one built factory maps to, and the built figures it is compared with."""

    factory: str
    args: PlanArgs
    siting: Mapping[str, object] | None
    rows: dict[str, BuiltRow]
    draw_mw: float
    notes: list[str] = field(default_factory=list[str])


@dataclass
class FromFactory:
    """A plan made from a factory: where it landed, its solve and what differs from the build."""

    pushed: Pushed
    built: AsBuilt
    solve: SolveResponse
    differences: list[str]


def _down(rate: float) -> float:
    return math.floor(rate * 10**DP) / 10**DP


def _up(rate: float) -> float:
    return math.ceil(rate * 10**DP) / 10**DP


def _records(st: WorldState, group: str, wanted: set[str]) -> list[BuildableRecord]:
    return [r for r in st.projection.get(group, ()) if instance_leaf(r.get("instance")) in wanted]


def _clock(record: BuildableRecord) -> float:
    return float(record.get("clock") or 1.0)


def _without_generators(g: GameData, view: FactoryView, records: list[BuildableRecord]) -> None:
    """Take the fuel the factory's generators burn out of its flows: generators stay out of
    the plan, so their fuel reads as an output where it is made and as nothing where fed in."""
    for record in records:
        building = g.buildings.get(record.get("cls", ""))
        fuel = g.items.get(record.get("fuel") or "")
        if record.get("paused") or building is None or fuel is None:
            continue
        flow = view.flows.get(fuel.name)
        if flow is not None:
            flow["consumed"] -= building.fuel_rate_per_min(fuel) * _clock(record)


def _item_id(g: GameData, name: str) -> str:
    return next((cls for cls, item in g.items.items() if item.name == name), name)


def _built_rows(
    g: GameData, view: FactoryView, recipes: list[BuildableRecord]
) -> dict[str, BuiltRow]:
    rows: dict[str, BuiltRow] = {}
    for record in recipes:
        recipe = g.recipes.get(record.get("recipe") or "")
        if recipe is None or record.get("paused"):
            continue
        row = rows.setdefault(
            recipe.cls, BuiltRow(recipe.name, g.building_name(record.get("cls")) or "")
        )
        row.clocks.append(_clock(record))
    for machine in view.machines:
        for item in machine.makes if not machine.recipe else ():
            row = rows.setdefault(EXTRACT + item, BuiltRow(item, ""))
            name = g.building_name(machine.building) or machine.building
            row.building = ", ".join(sorted({*row.building.split(", "), name} - {""}))
            row.clocks.append(machine.clock)
            row.rate += machine.makes[item]
    return rows


def _siting(records: list[BuildableRecord], name: str, when: str) -> Mapping[str, object] | None:
    box = geo.bbox([(pos[0], pos[1]) for r in records if (pos := r.get("pos"))])
    if box is None:
        return None
    try:
        return siting_mod.fit_to_bbox([v / 100 for v in box], name, when)
    except ValueError:
        return None


def _extractor_records(st: WorldState, wanted: set[str]) -> list[BuildableRecord]:
    return [r for r in _records(st, "extractors", wanted) if not r.get("paused")]


def _is_water_pump(record: BuildableRecord) -> bool:
    return "WaterPump" in record.get("cls", "")


def _sources(
    g: GameData, st: WorldState, args: PlanArgs, extractors: list[BuildableRecord]
) -> tuple[list[str], set[str]]:
    """``node:`` selectors for the tapped nodes the planner can model, and the leaves it cannot
    (a fracking satellite, a node missing from the table): their output is supplied instead."""
    tapped = sorted(
        {
            instance_leaf(r.get("node"))
            for r in extractors
            if r.get("node") and not _is_water_pump(r)
        }
    )
    if not tapped:
        return ["none"], set()
    wanted = [f"node:{leaf}" for leaf in tapped]
    probe = PlanArgs.from_dict({**args.to_dict(), "sources": wanted})
    request = build_scenario(g, st, **probe.kwargs())
    modelled = {
        instance_leaf(r["instance"])
        for r in request.node_rows
        if r["kind"] == "node" and r["rate"] > 0
    }
    kept = [leaf for leaf in tapped if leaf in modelled]
    return ([f"node:{leaf}" for leaf in kept] or ["none"]), set(tapped) - modelled


def _unmodelled_output(
    view: FactoryView, extractors: list[BuildableRecord], leaves: set[str]
) -> dict[str, float]:
    """Items/min the extractors on ``leaves`` make, by item name."""
    machines = {m.instance: m for m in view.machines}
    out: dict[str, float] = defaultdict(float)
    for record in extractors:
        if _is_water_pump(record) or instance_leaf(record.get("node")) not in leaves:
            continue
        machine = machines.get(instance_leaf(record.get("instance")))
        for item, rate in (machine.makes if machine else {}).items():
            out[item] += rate
    return out


def _named_factory(st: WorldState, factory: str) -> tuple[str, list[str]]:
    label = st.labels.find(factory)
    if label is None:
        known = ", ".join(x.name for x in st.labels.labels) or "(none named yet)"
        raise SelectorError(f"no factory named {factory!r}. Named factories: {known}")
    alive = set(st.graph.machines())
    machines = [m for m in label.anchors if m in alive]
    if not machines:
        raise SelectorError(f"factory {label.name!r} has no standing machines in this save")
    return label.name, machines


def map_factory(st: WorldState, factory: str) -> AsBuilt:
    """The plan request that reproduces the named factory ``factory`` as it stands."""
    g = st.game
    name, machines = _named_factory(st, factory)
    wanted = set(machines)
    view = build_view(name, machines, st.graph, g, st.projection, st.labels)
    recipes = _records(st, "machines", wanted)
    generators = _records(st, "generators", wanted)
    extractors = _extractor_records(st, wanted)
    _without_generators(g, view, generators)

    running = sorted({r for r in (x.get("recipe") for x in recipes if not x.get("paused")) if r})
    running = [rid for rid in running if rid in g.recipes]
    water = [r for r in extractors if _is_water_pump(r)]
    clocks = sorted({1.0, *(_clock(r) for r in extractors)})
    args = PlanArgs.from_dict(
        {
            "objective": "min_raw_power",
            "required": running,
            "only_recipes": running,
            "extractor_clocks": clocks if len(clocks) > 1 else None,
            "water_extractors": len(water),
            "payback_hours": 0.0,
            "overclock_last": False,
        }
    )
    sources, unmodelled = _sources(g, st, args, extractors)
    supplied: dict[str, float] = defaultdict(float)
    for item, rate in view.inputs():
        supplied[item] += rate
    for item, rate in _unmodelled_output(view, extractors, unmodelled).items():
        supplied[item] += rate
    exports = {_item_id(g, item): _down(rate) for item, rate in view.outputs(NOISE)}
    args.sources = sources
    args.exports = list(exports)
    args.export_minimums = exports
    args.supplied = {
        _item_id(g, item): _up(rate) for item, rate in supplied.items() if rate >= NOISE
    }

    notes = [f"made from the built factory “{name}”: {len(running)} recipes pinned"]
    if generators:
        notes.append(
            f"its {len(generators)} generators stay out of the plan: what they burn is an "
            "export, and the plan draws its power from the grid"
        )
    if unmodelled:
        notes.append(f"{len(unmodelled)} extractors the planner cannot model are supplied")
    return AsBuilt(
        factory=name,
        args=args,
        siting=_siting(
            [*recipes, *generators, *_records(st, "extractors", wanted)],
            name,
            edit_stamp(st.header),
        ),
        rows=_built_rows(g, view, recipes),
        draw_mw=view.draw_mw,
        notes=notes,
    )


def _row_key(row: SolveRow) -> str:
    return row["recipe_id"] or EXTRACT + (row["item"] or row["recipe"])


def _equivalents(row: SolveRow) -> float:
    last = row["last_clock"]
    if last is not None:
        return row["machines"] - 1 + last
    return row["machines"] * row["clock"]


def _pct(clock: float) -> str:
    return f"{clock * 100:.4g}%"


def _clocks(clocks: list[float]) -> str:
    low, high = min(clocks), max(clocks)
    return f"at {_pct(low)}" if math.isclose(low, high) else f"at {_pct(low)}–{_pct(high)}"


def _differs(a: float, b: float) -> bool:
    return abs(a - b) > max(1e-6, REL_TOL * max(abs(a), abs(b)))


def _extract_line(built: BuiltRow, planned: list[SolveRow]) -> str:
    rate = sum(r["per_min"] for row in planned for r in row["outputs"] if r["item"] == built.label)
    machines = sum(r["machines"] for r in planned)
    buildings = ", ".join(sorted({r["building"] for r in planned}))
    if (
        machines == built.machines
        and buildings == built.building
        and not _differs(rate, built.rate)
    ):
        return ""
    clock = sum(_equivalents(r) for r in planned) / machines if machines else 0.0
    plan = f"{machines} {buildings} at {_pct(clock)}" if machines else "none"
    return (
        f"{built.label}: built {built.machines} {built.building} {_clocks(built.clocks)} for "
        f"{built.rate:,.4g}/min, the plan {plan} for {rate:,.4g}/min"
    )


def _row_line(key: str, built: BuiltRow | None, planned: list[SolveRow]) -> str:
    label = built.label if built else planned[0]["recipe"]
    if built is not None and key.startswith(EXTRACT):
        return _extract_line(built, planned)
    if built is None:
        machines = sum(r["machines"] for r in planned)
        return f"{label}: not built, the plan adds {machines} {planned[0]['building']}"
    have = f"built {built.machines} {built.building} {_clocks(built.clocks)}"
    if not planned:
        return f"{label}: {have}, the plan needs none"
    machines = sum(r["machines"] for r in planned)
    need = sum(_equivalents(r) for r in planned)
    if machines == built.machines and not _differs(need, built.equivalents):
        return ""
    what = planned[0]["building"]
    clock = need / machines if machines else 0.0
    return (
        f"{label}: {have} ({built.equivalents:.4g} machine-equivalents), the plan "
        f"{machines} {what} at {_pct(clock)} ({need:.4g})"
    )


def compare(built: AsBuilt, solved: SolveResponse) -> list[str]:
    """What a solve of ``built.args`` does differently from the factory as it stands."""
    if not solved["feasible"]:
        return [f"the plan does not solve: {solved['cause'] or solved['headline']}"]
    planned: dict[str, list[SolveRow]] = defaultdict(list)
    for row in solved["rows"]:
        planned[_row_key(row)].append(row)
    lines = [
        _row_line(key, built.rows.get(key), planned.get(key, []))
        for key in sorted(set(built.rows) | set(planned))
    ]
    out = [line for line in lines if line]
    draw = float(solved["mw_draw"] or 0.0)
    if abs(draw - built.draw_mw) > max(MW_TOL, REL_TOL * built.draw_mw):
        out.append(f"power: built draws {built.draw_mw:,.1f} MW, the plan {draw:,.1f} MW")
    return out


def _free_name(log: PlanLog, base: str) -> str:
    for n in range(1, NAME_TRIES + 1):
        wanted = f"{base} {SUFFIX}" if n == 1 else f"{base} {SUFFIX} ({n})"
        try:
            return log.free_name(wanted)
        except NameTaken:
            continue
    raise NameTaken(f"{base} {SUFFIX}")


def plan_notes(built: AsBuilt, differences: list[str]) -> str:
    lines = list(built.notes)
    if differences:
        lines.append("differs from the build: " + "; ".join(differences))
    else:
        lines.append("solves to the build as it stands")
    return "\n".join(lines)


def plan_from_factory(
    st: WorldState, factory: str, *, actor: Actor, sav: str = "", note: str = ""
) -> FromFactory:
    """A new plan at v1 that mirrors the named factory: one ``create``, so forget undoes it.

    Every call makes a new plan (``<factory> as built``, then ``(2)``, ``(3)`` ...): each is
    the factory as it stood when asked, and an earlier one may have been edited since.
    """
    g = st.game
    built = map_factory(st, factory)
    solved = summary.solve_summary(g, st, built.args.kwargs())
    differences = compare(built, solved)
    log = PlanLog(st.world_id, st.session_name)
    name = _free_name(log, built.factory)
    draft = PlanState(key="", rev=1, name=name, args=built.args)
    try:
        stamped = summary.stamp_for(st)(draft)
    except Exception:
        stamped = PlanStamp(plan_id="", provenance={})
    pushed = log.create(
        name,
        built.args,
        actor=actor,
        sav=sav,
        notes=plan_notes(built, differences),
        factory=built.factory,
        siting=built.siting,
        plan_id=stamped.get("plan_id", ""),
        provenance=stamped.get("provenance") or {},
        created=edit_stamp(st.header),
        note=note or f"made from built factory {built.factory!r}"[:200],
    )
    return FromFactory(pushed=pushed, built=built, solve=solved, differences=differences)
