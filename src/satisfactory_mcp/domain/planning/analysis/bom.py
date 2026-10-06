"""Bill of materials: the flattened raw and intermediate bill for a target rate.

A presentation layer over the LP, never a recursive expansion: Recycled Plastic and Recycled
Rubber are a real 2-cycle, so a tree walk has no correct depth. Water is priced last, a
lexicographic tie-break for the degeneracy (docs/mcp-surface.md §10.1c, docs/planning.md §8.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ....core.gamedata.constants import UNLIMITED_RATE, WATER
from ....core.gamedata.model import GameData
from ....core.gamedata.search import resolve_item
from ....core.text import num
from ...world.state import WorldState
from ..solver.graph import item_cycles
from ..solver.model import Solution
from ..solver.optimize import solve
from ..solver.scenario import chain_scenario

__all__ = ["BOM", "BomRow", "build_bom"]

#: A process under this share of the plan's largest is LP noise, not a building; relative,
#: so a bill for 0.1/min is not filtered away (docs/mcp-surface.md §10.1c).
_NOISE_FRACTION = 1e-4

_EPS = 1e-7


@dataclass
class BomRow:
    item: str
    name: str
    #: GROSS production per minute: in a recipe loop, more than what leaves the plant.
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
    #: Items that must leave the plant besides the target, and items sunk: obligations,
    #: since an unconsumed byproduct stalls the line.
    byproducts: dict[str, float] = field(default_factory=dict)
    sunk: dict[str, float] = field(default_factory=dict)
    #: Item names caught in a production cycle among the chosen recipes.
    loops: list[tuple[str, ...]] = field(default_factory=list)
    alternates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    solves: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "optimal"


def _loops(game: GameData, processes: list[dict]) -> list[tuple[str, ...]]:
    """Item names on each cycle among the chosen recipes, so a looped line is explained."""
    return [tuple(game.item_name(item) for item in cycle) for cycle in item_cycles(processes)]


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
            # Machines count under one product so the column sums to the plan; the recipe
            # NAME is listed under every product, or a byproduct row names no recipe.
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
    known = game.items.get(target)
    if known is not None and known.is_resource:
        # Its own bill: solving would report the recipe that MAKES ore as infeasible.
        return BOM(
            item=target,
            item_name=name,
            qty=qty,
            status="raw",
            raw={target: qty},
            notes=[f"{name} is a raw resource: its bill of materials is {num(qty)} of itself"],
        )

    chain = chain_scenario(
        game,
        state,
        target,
        outlets=outlets or [],
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
        only_recipes=only_recipes,
    )
    base = replace(chain.scenario, export_minimums={target: qty})

    bom = BOM(item=target, item_name=name, qty=qty, status="infeasible")
    bom.notes.extend(chain.request.recipe_errors)
    if chain.request.excluded:
        bom.notes.append("excluded: " + ", ".join(chain.request.excluded))

    # Phase 1 of the tie-break: water free, everything else priced.
    sol = solve(replace(base, raw_weights={WATER: 0.0}))
    bom.solves = 1
    if sol.ok and sol.raw_used.get(WATER, 0.0) > _EPS:
        # Phase 2: pin what phase 1 reached and minimise water alone. The caps carry the
        # 4 dp rounding of raw_used plus MILP slack, or phase 2 is infeasible (§10.1c).
        pinned = {k: sol.raw_used.get(k, 0.0) * (1 + 1e-6) + 5e-5 for k in chain.raw_caps}
        pinned[WATER] = UNLIMITED_RATE
        second = solve(
            replace(
                base,
                raw_caps=pinned,
                raw_weights={k: 0.0 for k in chain.raw_caps if k != WATER},
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
    # A 1e-4/min draw is LP residue, and it would print as a "0 Water" line.
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
