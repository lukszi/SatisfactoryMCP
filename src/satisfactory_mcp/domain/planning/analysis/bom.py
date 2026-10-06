"""Bill of materials: the flattened raw and intermediate bill for a target rate.

**This is the LP, not a recursive expansion, and that is not a preference.** The
design spec's ban on ``plan_chain`` applies verbatim here: Recycled Plastic
(30 Rubber + 30 Fuel -> 60 Plastic) and Recycled Rubber (30 Plastic + 30 Fuel ->
60 Rubber) form a genuine 2-cycle and this save has both unlocked, so a tree walk
has no correct depth limit -- it either stops early and understates, or recurses
for ever. Worse, it is silent about which of the two it did. The LP has no depth at
all: it solves one flow balance per item and the cycle is just two more columns.
The loop, when the solver uses one, is detected and named in the response, because
a bill whose Plastic line reads 200/min for a 10/min export is not an error and the
reader has to be told why.

So this module is a presentation layer over ``solve``, and deliberately a thin one.
It answers a narrower question than ``plan_factory``: no nodes, no extractors, no
geography. Charging extraction here would make the bill depend on which miners this
save happens to have free, which is a different question and ``plan_factory``'s.

The recipe choice is the answer, not a detail
---------------------------------------------
An item's bill is only defined once you fix which recipe makes each intermediate,
and alternates move the numbers by more than any rounding: on Reinforced Iron Plate
the base chain costs **120 Iron Ore/min per 10 plates**, and the alternates this
save has unlocked bring that down. So every row names the recipe that produced it,
and ``only_recipes`` / ``exclude_recipes`` let a caller pin the chain and get an
arithmetic answer they can check by hand.

The degeneracy, and the one tie-break applied
---------------------------------------------
``min_raw`` LPs over this recipe set are degenerate -- equally optimal vertices
give materially different raw vectors -- and a bare ``min_raw`` also sums every
resource with weight one, so it will trade crude against water. Water is effectively
unlimited on this map, so that trade is always the wrong way round. The documented
tie-break is lexicographic: **minimise every other resource with water free, then
pin those and minimise water.** Whatever degeneracy survives that is labelled in the
response rather than presented as the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ....core.gamedata.model import GameData
from ....core.gamedata.search import resolve_item
from ....core.text import num
from ...world.state import WorldState
from ..solver.model import Solution
from ..solver.optimize import solve
from ..solver.scenario import build_scenario

__all__ = ["BOM", "BomRow", "build_bom"]

#: Stand-in for "unlimited". Every resource needs a cap because ``min_raw`` only
#: prices resources that HAVE a raw column; one left out has no column at all and
#: its balance row is unsatisfiable.
_UNLIMITED = 1e7

#: Water is the free half of the lexicographic tie-break. See the module docstring.
_WATER = "Desc_Water_C"

#: A process carrying less than this share of the plan's largest is LP noise, not a
#: building. A degenerate vertex routinely leaves a column at ~1e-6
#: machine-equivalents; ``ceil`` then turns it into one whole Smelter with a recipe
#: name, and the bill claims a second route for an item that has only one. Measured
#: on Reinforced Iron Plate: it added a Smelter to a plan whose Iron Ingot came
#: entirely from Pure Iron Ingot, and named two extra alternates that carry no flow.
#: Relative rather than absolute so a bill for 0.1/min is not filtered away.
_NOISE_FRACTION = 1e-4

_EPS = 1e-7


@dataclass
class BomRow:
    item: str
    name: str
    #: GROSS production per minute -- the capacity that has to exist, which in a
    #: recipe loop is strictly more than what leaves the plant.
    made: float
    used: float
    is_raw: bool = False
    is_target: bool = False
    machines: int = 0
    recipes: tuple[str, ...] = ()
    recipe_ids: tuple[str, ...] = ()
    building: str = ""


@dataclass
class BOM:
    item: str
    item_name: str
    qty: float
    status: str
    rows: list[BomRow] = field(default_factory=list)
    raw: dict[str, float] = field(default_factory=dict)
    machines: int = 0
    mw: float = 0.0
    #: Items that must leave the plant besides the target, and items sunk. Both are
    #: obligations, not spare output: an unconsumed byproduct stalls the line.
    byproducts: dict[str, float] = field(default_factory=dict)
    sunk: dict[str, float] = field(default_factory=dict)
    #: Items caught in a production cycle among the chosen recipes.
    loops: list[tuple[str, ...]] = field(default_factory=list)
    alternates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    solves: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


def _loops(game: GameData, processes: list[dict]) -> list[tuple[str, ...]]:
    """Items that lie on a cycle among the CHOSEN recipes.

    Mutual reachability by closure rather than Tarjan: the chosen set is a couple of
    dozen items, so the simple thing is the readable thing. This is the check that
    makes the response honest about a bill whose Plastic line is 20x its export.
    """
    edges: dict[str, set[str]] = {}
    for row in processes:
        rates = row.get("rates") or {}
        ins = [i for i, v in rates.items() if v < 0]
        outs = [i for i, v in rates.items() if v > 0]
        for i in ins:
            edges.setdefault(i, set()).update(outs)

    nodes = set(edges) | {j for outs in edges.values() for j in outs}
    reach = {n: set(edges.get(n, ())) for n in nodes}
    changed = True
    while changed:
        changed = False
        for n in nodes:
            grown = set(reach[n])
            for m in list(reach[n]):
                grown |= reach.get(m, set())
            if grown != reach[n]:
                reach[n] = grown
                changed = True

    seen: set[str] = set()
    out: list[tuple[str, ...]] = []
    for n in sorted(nodes):
        if n in seen or n not in reach[n]:
            continue
        component = sorted(m for m in nodes if m in reach[n] and n in reach.get(m, set()))
        seen.update(component)
        if len(component) > 1:
            out.append(tuple(game.item_name(m) for m in component))
    return out


def live_processes(sol: Solution) -> list[dict]:
    """The solve's processes with LP noise dropped. See ``_NOISE_FRACTION``."""
    peak = max((row["machine_equivalents"] for row in sol.processes), default=0.0)
    floor = peak * _NOISE_FRACTION
    return [row for row in sol.processes if row["machine_equivalents"] >= floor]


def _rows(
    game: GameData, processes: list[dict], raw_used: dict[str, float], target: str
) -> list[BomRow]:
    made: dict[str, float] = {}
    used: dict[str, float] = {}
    owners: dict[str, list[dict]] = {}
    sources: dict[str, list[dict]] = {}
    for row in processes:
        rates = row.get("rates") or {}
        positive = {i: v for i, v in rates.items() if v > _EPS}
        for item, v in positive.items():
            made[item] = made.get(item, 0.0) + v
            sources.setdefault(item, []).append(row)
        for item, v in rates.items():
            if v < -_EPS:
                used[item] = used.get(item, 0.0) - v
        if positive:
            # Machines are charged to ONE product per process, so the column sums to
            # the plan total instead of counting a Refinery under both of its
            # outputs. The recipe NAME is still listed against every product it
            # makes, or a byproduct row would name no recipe at all and read as
            # arriving from nowhere -- Polymer Resin off Alternate: Heavy Oil
            # Residue is exactly that row.
            primary = max(positive, key=lambda i: positive[i])
            owners.setdefault(primary, []).append(row)

    out: list[BomRow] = []
    for item, amount in raw_used.items():
        if amount <= _EPS:
            continue
        out.append(
            BomRow(
                item=item,
                name=game.item_name(item),
                made=amount,
                used=used.get(item, 0.0),
                is_raw=True,
                recipes=("RAW",),
            )
        )
    for item, amount in made.items():
        mine = owners.get(item, [])
        rows = sources.get(item, [])
        out.append(
            BomRow(
                item=item,
                name=game.item_name(item),
                made=amount,
                used=used.get(item, 0.0),
                is_target=item == target,
                machines=sum(int(r["machines"]) for r in mine),
                recipes=tuple(dict.fromkeys(r["label"] for r in rows)),
                recipe_ids=tuple(dict.fromkeys(r["recipe"] for r in rows if r.get("recipe"))),
                building=", ".join(dict.fromkeys(r["building"] for r in mine)),
            )
        )

    # Raw first -- that is the bill -- then intermediates by size, target last.
    out.sort(key=lambda r: (r.is_target, not r.is_raw, -r.made, r.name))
    return out


def build_bom(
    game: GameData,
    state: WorldState,
    item: str,
    qty: float = 60.0,
    allow_sinks: bool = True,
    outlets: list[str] | None = None,
    exclude_recipes: list[str] | None = None,
    only_recipes: list[str] | None = None,
) -> BOM:
    """Flattened bill for ``qty`` per minute of ``item``, solved by the LP."""
    target = resolve_item(game, item)
    if target is None:
        raise ValueError(f"no item matching {item!r}")
    if qty <= 0:
        raise ValueError("qty must be positive (it is a rate, per minute)")

    name = game.item_name(target)
    it = game.items.get(target)
    if it is not None and it.is_resource:
        # Its own bill. Solving would demand a recipe that MAKES the ore and report
        # "infeasible", which is a true statement about the wrong question.
        return BOM(
            item=target,
            item_name=name,
            qty=qty,
            status="raw",
            raw={target: qty},
            notes=[f"{name} is a raw resource: its bill of materials is {num(qty)} of itself"],
        )

    # build_scenario is the one path from tool arguments to a Scenario, so the recipe
    # set, the building set and the item resolution match what plan_factory solves.
    request = build_scenario(
        game,
        state,
        objective="min_raw",
        exports=["MW"],
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    raw_caps = {cls: _UNLIMITED for cls, i in game.items.items() if i.is_resource}
    raw_caps.pop(target, None)
    outlet_ids = [resolve_item(game, o) or o for o in (outlets or [])]

    base = replace(
        request.scenario,
        objective="min_raw",
        target_item=target,
        exports=(target, *(o for o in outlet_ids if o != target)),
        export_minimums={target: qty},
        raw_caps=raw_caps,
        # A bill is the chain, not the mine. Charging extractors would make it depend
        # on which nodes this save has free -- plan_factory's question, not this one.
        extractor_nodes={},
        allow_sinks=allow_sinks,
        grid_import_mw=_UNLIMITED,
    )

    bom = BOM(item=target, item_name=name, qty=qty, status="infeasible")
    bom.notes.extend(request.recipe_errors)
    if request.excluded:
        bom.notes.append("excluded: " + ", ".join(request.excluded))

    # Phase 1 of the tie-break: water free, everything else priced.
    sol = solve(replace(base, raw_weights={_WATER: 0.0}))
    bom.solves = 1
    if sol.ok and sol.raw_used.get(_WATER, 0.0) > _EPS:
        # Phase 2: pin what phase 1 proved reachable and minimise water alone. Without
        # the pin water returns at its stand-in cap, since phase 1 never priced it.
        #
        # The headroom is not decoration. ``Solution.raw_used`` is rounded to 4dp, so
        # a draw of 13.33333 is reported as 13.3333 and a cap derived from it sits
        # BELOW what the chain actually needs -- which made phase 2 infeasible on
        # Reinforced Iron Plate and silently threw the tie-break away. Widen by
        # exactly that rounding, plus slack for the MILP's own 1e-6 optimum.
        pinned = {k: sol.raw_used.get(k, 0.0) * (1 + 1e-6) + 5e-5 for k in raw_caps}
        pinned[_WATER] = _UNLIMITED
        second = solve(
            replace(
                base,
                raw_caps=pinned,
                raw_weights={k: 0.0 for k in raw_caps if k != _WATER},
            )
        )
        bom.solves = 2
        if second.ok:
            sol = second
        else:
            bom.notes.append("water tie-break failed; the water figure is one of several optima")

    if not sol.ok:
        bom.notes.append(
            f"no unlocked chain makes {name} at {num(qty)}/min -- "
            "a byproduct with no consumer makes a plan infeasible rather than wasteful; "
            "try explain_byproducts, or outlets=[...] to let one leave"
        )
        return bom

    live = live_processes(sol)
    # A raw draw of 1e-4/min is the same LP residue as a 1e-6 machine, and core.text.num
    # prints it as a flat "0" -- a bill line reading "0 Water" invites the reader to
    # go looking for a water supply that the plan does not need.
    raw = {k: v for k, v in sol.raw_used.items() if v > 1e-3}
    bom.status = "optimal"
    bom.rows = _rows(game, live, raw, target)
    bom.raw = raw
    bom.machines = sum(int(row["machines"]) for row in live)
    bom.mw = sum(row["mw"] for row in live)
    bom.byproducts = {k: v for k, v in sol.exports.items() if k != target and v > 1e-4}
    bom.sunk = {k: v for k, v in sol.sunk.items() if v > 1e-4}
    bom.loops = _loops(game, live)
    bom.alternates = sorted(
        {
            game.recipes[row["recipe"]].name
            for row in live
            if row.get("recipe") in game.recipes and game.recipes[row["recipe"]].is_alternate
        }
    )
    return bom
