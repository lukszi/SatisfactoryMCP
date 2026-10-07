"""Why an equality-balanced plan has no answer: find the byproduct with no outlet.

The verdict is always the LP's: a relaxed probe names the candidates, and an item is the
blocker only when opening it alone moves the caller's objective. The graph work left is
explanatory -- outlets, packaging, closed loops. docs/planning.md §8.2a.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import linprog

from ....core import solverlane
from ....core.gamedata.constants import AWESOME_SINK_MW
from ....core.gamedata.model import GameData
from ....core.gamedata.search import resolve_item
from ....core.gamedata.unlocks import granted_by_label
from ...world.state import WorldState
from ..solver.model import MW, Process, Scenario, Solution
from ..solver.optimize import solve
from ..solver.processes import build_processes
from ..solver.scenario import build_scenario, with_recipes

__all__ = ["Blocker", "ByproductReport", "Fix", "Loop", "Outlet", "analyse"]

_EPS = 1e-6

#: Objectives whose value gets better as it gets bigger.
_MAXIMISE = ("max_mw", "max_item")

#: The one building that turns a fluid into a sinkable solid.
_PACKAGER = "Build_Packager_C"

# --------------------------------------------------------------------- objective


def _value(objective: str, sol: Solution) -> float | None:
    """The number the caller asked to move, in its own units: ``net_mw`` for max_mw, since
    the objective carries the machine price (docs/planning.md §8.4)."""
    if not sol.ok:
        return None
    if objective == "max_mw":
        return sol.net_mw
    if objective == "min_machines":
        return sol.machines_total
    return sol.objective_value


def _unit(objective: str) -> str:
    return {
        "max_mw": "MW",
        "min_power": "MW",
        "min_machines": "machines",
        "min_raw": "raw/min",
    }.get(objective, "/min")


def _improved(objective: str, base: float | None, cand: float | None) -> bool:
    if cand is None:
        return False
    if base is None:
        return True  # anything at all beats infeasible
    span = max(abs(base), 1.0) * 1e-6
    return cand > base + span if objective in _MAXIMISE else cand < base - span


@dataclass
class _Probes:
    """Counted, budgeted re-solves: each is a full MILP, and the caller is told the count."""

    objective: str
    budget: int
    count: int = 0

    def run(self, sc: Scenario) -> tuple[float | None, Solution]:
        self.count += 1
        sol = solve(sc)
        return _value(self.objective, sol), sol

    def spend(self) -> bool:
        if self.budget <= 0:
            return False
        self.budget -= 1
        return True


# ------------------------------------------------------------------------ census


def _flows(procs: list[Process]) -> tuple[dict[str, list[Process]], dict[str, list[Process]]]:
    """Net producers and net consumers of each item, over the LP's own columns."""
    produced: dict[str, list[Process]] = {}
    consumed: dict[str, list[Process]] = {}
    for p in procs:
        for item, rate in p.rates.items():
            if rate > _EPS:
                produced.setdefault(item, []).append(p)
            elif rate < -_EPS:
                consumed.setdefault(item, []).append(p)
    return produced, consumed


def _terminal(sc: Scenario) -> set[str]:
    """Items that may legally leave: the export whitelist, plus sinks when allowed."""
    out = set(sc.exports) | set(sc.export_minimums)
    if sc.allow_sinks:
        out |= {cls for cls, it in sc.game.items.items() if it.sinkable}
    return out


# ----------------------------------------------------------------------- outlets


@dataclass
class Outlet:
    """A recipe that would consume the stuck item."""

    recipe: str
    name: str
    building: str
    unlocked: bool
    #: Net per-minute of the stuck item for one machine. Negative means it absorbs.
    net_rate: float
    products: tuple[str, ...]
    #: How to obtain it when locked: "hard drive: ...", "MAM research: ...", ...
    source: str = ""


def _outlets(game: GameData, state: WorldState, item_id: str) -> list[Outlet]:
    """Every automatable recipe that consumes the item, unlocked or not: "you own the fix"
    and "you need a hard drive" are different answers."""
    out: list[Outlet] = []
    for r in game.consumers_of(item_id, "part"):
        if r.is_event:
            continue
        net = r.rate_of(item_id)
        if net >= -_EPS:  # gives back at least as much as it takes; not an outlet
            continue
        unlocked = state.has_recipe(r.cls) and (
            r.machine is None or r.machine in state.unlocked_building_ids
        )
        b = game.buildings.get(r.machine or "")
        out.append(
            Outlet(
                recipe=r.cls,
                name=r.name,
                building=b.name if b else (r.machine or "-"),
                unlocked=unlocked,
                net_rate=net,
                products=tuple(f.item for f in r.products if f.item != item_id),
                source="" if unlocked else granted_by_label(game, r),
            )
        )
    out.sort(key=lambda o: (not o.unlocked, o.net_rate))
    return out


def _packaging(game: GameData, state: WorldState, item_id: str) -> Outlet | None:
    """The Packager route that turns a fluid into a solid an AWESOME Sink can take, often
    the only disposal a fluid has (constants.FLUIDS_CANNOT_BE_SUNK)."""
    it = game.items.get(item_id)
    if it is None or not it.is_fluid:
        return None
    best: Outlet | None = None
    for r in game.consumers_of(item_id, "part"):
        if r.machine != _PACKAGER:
            continue
        packaged = [f.item for f in r.products if not game.items[f.item].is_fluid]
        if not packaged:
            continue
        has_machine = _PACKAGER in state.unlocked_building_ids
        unlocked = state.has_recipe(r.cls) and has_machine
        cand = Outlet(
            recipe=r.cls,
            name=r.name,
            building="Packager",
            unlocked=unlocked,
            net_rate=r.rate_of(item_id),
            products=tuple(packaged),
            # Never blank when locked: a missing Packager is a different job from a recipe.
            source=""
            if unlocked
            else (
                granted_by_label(game, r) if not state.has_recipe(r.cls) else "no Packager unlocked"
            ),
        )
        if best is None or (cand.unlocked and not best.unlocked):
            best = cand
    return best


# -------------------------------------------------------------------- the loop


@dataclass
class Loop:
    """A closed cycle of recipes that the stuck item's chain runs into."""

    items: tuple[str, ...]  # display names
    recipes: tuple[str, ...]  # display names
    #: True when no non-negative mix of the cycle's own recipes reduces any of its items:
    #: such a cycle never absorbs a surplus, however many you build.
    absorbs_nothing: bool
    net_creates: bool


def _cycle_recipes(procs: list[Process], members: set[str]) -> list[Process]:
    return [
        p
        for p in procs
        if p.kind == "recipe"
        and any(r < -_EPS and i in members for i, r in p.rates.items())
        and any(r > _EPS and i in members for i, r in p.rates.items())
    ]


def _cycle_absorbs(inner: list[Process], members: set[str]) -> tuple[bool, bool]:
    """Can the cycle net-consume any member? And does it net-create them? A tiny LP, since
    "they consume each other, so they cancel" is backwards (docs/planning.md §8.2a)."""
    if not inner:
        return False, False
    order = sorted(members)
    a = np.array([[p.rates.get(i, 0.0) for p in inner] for i in order])
    total = a.sum(axis=0)
    # sum(lambda) <= 1 only bounds the LP; the question is a sign, not a magnitude.
    cap = np.ones((1, len(inner)))
    zeros = np.zeros(len(order))

    absorb = solverlane.run(
        lambda: linprog(
            c=total,
            A_ub=np.vstack([a, cap]),
            b_ub=np.concatenate([zeros, [1.0]]),
            bounds=(0, None),
        )
    )
    create = solverlane.run(
        lambda: linprog(
            c=-total,
            A_ub=np.vstack([-a, cap]),
            b_ub=np.concatenate([zeros, [1.0]]),
            bounds=(0, None),
        )
    )
    return (
        bool(absorb.success and absorb.fun is not None and absorb.fun < -_EPS),
        bool(create.success and create.fun is not None and -create.fun > _EPS),
    )


def _loop_for(
    game: GameData, procs: list[Process], terminal: set[str], seeds: list[str]
) -> Loop | None:
    """Does the stuck item's chain terminate, or exchange inside a closed cycle among the
    direct products of its unlocked consumers (docs/planning.md §8.2a)?"""
    members = {s for s in dict.fromkeys(seeds) if s not in terminal and s != MW}
    if len(members) < 2:
        return None
    inner = _cycle_recipes(procs, members)
    if not inner:
        return None
    eats = {i for p in inner for i, r in p.rates.items() if r < -_EPS and i in members}
    makes = {i for p in inner for i, r in p.rates.items() if r > _EPS and i in members}
    # Every member both fed and made by the set: a closed exchange, not a forking chain.
    if eats != members or makes != members:
        return None
    absorbs, creates = _cycle_absorbs(inner, members)
    return Loop(
        items=tuple(sorted(game.item_name(i) for i in members)),
        recipes=tuple(sorted({p.label for p in inner})),
        absorbs_nothing=not absorbs,
        net_creates=creates,
    )


# ------------------------------------------------------------------------- model


@dataclass
class Fix:
    """One thing the caller could change, priced in their own objective."""

    kind: str  # sink | export | package | unlock
    label: str
    detail: str
    value: float | None
    gain: bool


@dataclass
class Blocker:
    item: str
    name: str
    is_fluid: bool
    sinkable: bool
    sink_points: int
    #: Rate the plan would emit once the item is allowed out: the size of the problem.
    rate: float
    #: How many LP columns consume it. Zero: nothing touches it; non-zero and still stuck
    #: is the loop trap.
    allowed_consumers: int
    producers: tuple[str, ...]
    outlets: list[Outlet]
    #: False when the item was named by the caller rather than confirmed by a solve.
    confirmed: bool = True
    fixes: list[Fix] = field(default_factory=list[Fix])
    loop: Loop | None = None
    packaging: Outlet | None = None

    @property
    def unlocked_outlets(self) -> list[Outlet]:
        return [o for o in self.outlets if o.unlocked]

    @property
    def locked_outlets(self) -> list[Outlet]:
        return [o for o in self.outlets if not o.unlocked]


@dataclass
class ByproductReport:
    plan_id: str
    objective: str
    unit: str
    scope: str
    age_note: str
    base_value: float | None
    open_value: float | None
    blockers: list[Blocker]
    #: Items with no outlet that opening alone does NOT rescue: irrelevant at this scope,
    #: or blocking only jointly with another.
    also_stuck: list[tuple[str, float]]
    #: Items whose only outlet is an AWESOME Sink, and the plan's value without sinking.
    sink_only: dict[str, float]
    no_sink_value: float | None
    notes: list[str]
    solves: int


# ---------------------------------------------------------------------- analysis


def _surplus_candidates(
    sc: Scenario,
    produced: dict[str, list[Process]],
    terminal: set[str],
    focus: str | None,
    probes: _Probes,
) -> tuple[dict[str, float], float | None]:
    """What the relaxed probe exports with every produced item opened, and its value.

    The probe is the naive ``net >= 0`` formulation, used only to size and rank candidates.
    """
    openable = tuple(i for i in sorted(produced) if i != MW and i not in sc.exports)
    open_value, opened = probes.run(replace(sc, exports=(*sc.exports, *openable)))
    surplus = {
        i: rate
        for i, rate in opened.exports.items()
        if i != MW and i not in terminal and rate > _EPS
    }
    if focus is not None:
        surplus = {focus: surplus.get(focus, 0.0)}
    return surplus, open_value


def _absorbed_items(base: Solution) -> set[str]:
    """Items the base plan produces: already consumed exactly, so never the dead end (§8.2a)."""
    if not base.ok:
        return set()
    return {i for row in base.processes for i, r in row["rates"].items() if r > _EPS}


def _loop_seeds(blocker: Blocker, terminal: set[str]) -> list[str]:
    """What the blocker's unlocked consumers make that cannot leave the plant."""
    return [
        product
        for outlet in blocker.unlocked_outlets
        for product in outlet.products
        if product not in terminal and product != blocker.item
    ]


def _blocker_for(
    game: GameData,
    state: WorldState,
    item_id: str,
    rate: float,
    confirmed: bool,
    procs: list[Process],
    produced: dict[str, list[Process]],
    consumed: dict[str, list[Process]],
    terminal: set[str],
) -> Blocker:
    """One stuck item with its outlets, packaging route and the loop its chain runs into."""
    known = game.items[item_id]
    blocker = Blocker(
        item=item_id,
        name=known.name,
        is_fluid=known.is_fluid,
        sinkable=known.sinkable,
        sink_points=known.sink_points,
        rate=round(rate, 2),
        allowed_consumers=len(consumed.get(item_id, ())),
        producers=tuple(
            dict.fromkeys(p.label for p in produced.get(item_id, ()) if p.kind == "recipe")
        ),
        outlets=_outlets(game, state, item_id),
        confirmed=confirmed,
        packaging=_packaging(game, state, item_id),
    )
    blocker.loop = _loop_for(game, procs, terminal, _loop_seeds(blocker, terminal))
    return blocker


def analyse(
    game: GameData,
    state: WorldState,
    objective: str = "max_mw",
    target_item: str | None = None,
    sources: list[str] | None = None,
    exports: list[str] | None = None,
    export_minimums: dict[str, float] | None = None,
    only_free_nodes: bool = False,
    allow_sinks: bool = True,
    item: str | None = None,
    exclude_recipes: list[str] | None = None,
    max_probes: int = 8,
) -> ByproductReport:
    """Diagnose one plan scope's byproducts. Costs 2 + up to ``max_probes`` solves."""
    req = build_scenario(
        game,
        state,
        objective=objective,
        target_item=target_item,
        sources=sources,
        exports=exports,
        export_minimums=export_minimums,
        only_free_nodes=only_free_nodes,
        allow_sinks=allow_sinks,
        exclude_recipes=exclude_recipes,
    )
    sc = req.scenario
    procs = build_processes(sc)
    produced, consumed = _flows(procs)
    terminal = _terminal(sc)
    notes = [*req.selection.errors, *req.export_errors]
    if req.selection.errors and not req.selection.nodes:
        # A typo'd selector and a stuck byproduct both give nothing; never confuse them.
        notes.append("no node matched these sources, so nothing can be extracted at all")
    probes = _Probes(objective=objective, budget=max_probes)

    focus = resolve_item(game, item) if item else None
    if item and focus is None:
        notes.append(f"unknown item {item!r}; diagnosing the whole scope instead")

    base_value, base = probes.run(sc)
    surplus, open_value = _surplus_candidates(sc, produced, terminal, focus, probes)
    absorbed = _absorbed_items(base)

    blockers: list[Blocker] = []
    also_stuck: list[tuple[str, float]] = []
    for item_id, rate in sorted(surplus.items(), key=lambda kv: -kv[1]):
        known = game.items.get(item_id)
        if known is None:
            continue
        already = item_id in absorbed
        if already and focus is None:
            continue
        # The one claim made as fact: does opening THIS item alone move the objective?
        if not probes.spend():
            also_stuck.append((known.name, rate))
            continue
        solo_value, _ = probes.run(replace(sc, exports=(*sc.exports, item_id)))
        confirmed = _improved(objective, base_value, solo_value) and not already
        if not confirmed and focus is None:
            also_stuck.append((known.name, rate))
            continue
        blocker = _blocker_for(
            game, state, item_id, rate, confirmed, procs, produced, consumed, terminal
        )
        # Fixes for an item merely asked about would price some OTHER route's opening.
        if confirmed:
            _add_fixes(game, sc, objective, base_value, blocker, probes)
        blockers.append(blocker)

    # Sinking is a real belt and 30 MW per Sink; when it alone holds a plan up, say so.
    sink_only = {game.item_name(k): v for k, v in base.sunk.items()} if base.ok else {}
    no_sink_value: float | None = None
    if sink_only and sc.allow_sinks:
        no_sink_value, _ = probes.run(replace(sc, allow_sinks=False))

    if not blockers and also_stuck and _improved(objective, base_value, open_value):
        notes.append(
            "no single item unblocks this: "
            + ", ".join(n for n, _ in also_stuck[:4])
            + " have to be given an outlet together"
        )

    return ByproductReport(
        plan_id=req.plan_id,
        objective=objective,
        unit=_unit(objective),
        scope=req.selection.description,
        age_note=state.age_note,
        base_value=base_value,
        open_value=open_value,
        blockers=blockers,
        also_stuck=also_stuck,
        sink_only=sink_only,
        no_sink_value=no_sink_value,
        notes=notes,
        solves=probes.count,
    )


# -------------------------------------------------------------------------- fixes


def _priced_fix(
    kind: str,
    label: str,
    detail: str,
    scenario: Scenario,
    objective: str,
    base_value: float | None,
    probes: _Probes,
) -> Fix:
    """One fix, solved to its value in the caller's objective."""
    value, _ = probes.run(scenario)
    return Fix(kind, label, detail, value, _improved(objective, base_value, value))


def _sink_fix(
    sc: Scenario, objective: str, base_value: float | None, blocker: Blocker, probes: _Probes
) -> list[Fix]:
    if sc.allow_sinks or not blocker.sinkable or not probes.spend():
        return []
    belts = max(1, -int(-blocker.rate // max(sc.belt_ipm, 1.0)))
    detail = (
        f"{blocker.sink_points} pts, {belts} belt(s) to an AWESOME Sink, "
        f"{belts * AWESOME_SINK_MW:g} MW"
    )
    scenario = replace(sc, allow_sinks=True)
    return [
        _priced_fix("sink", "allow_sinks=true", detail, scenario, objective, base_value, probes)
    ]


def _export_fixes(
    game: GameData,
    sc: Scenario,
    objective: str,
    base_value: float | None,
    blocker: Blocker,
    probes: _Probes,
) -> list[Fix]:
    """Let each unlocked consumer's products leave, one distinct product set at a time."""
    fixes: list[Fix] = []
    seen: set[frozenset[str]] = set()
    for outlet in blocker.unlocked_outlets:
        wanted = tuple(p for p in outlet.products if p not in sc.exports)
        if not wanted or frozenset(wanted) in seen:
            continue
        if not probes.spend():
            break
        seen.add(frozenset(wanted))
        fixes.append(
            _priced_fix(
                "export",
                "exports+=" + ", ".join(game.item_name(p) for p in wanted),
                f"{outlet.name} ({outlet.building}) eats {-outlet.net_rate:g}/min per machine",
                replace(sc, exports=(*sc.exports, *wanted)),
                objective,
                base_value,
                probes,
            )
        )
    return fixes


def _package_fix(
    game: GameData,
    sc: Scenario,
    objective: str,
    base_value: float | None,
    blocker: Blocker,
    probes: _Probes,
) -> list[Fix]:
    pack = blocker.packaging
    if pack is None or not probes.spend():
        return []
    packed = pack.products[0]
    scenario = replace(with_recipes(sc, [pack.recipe]), exports=(*sc.exports, packed))
    detail = (
        f"{pack.name} ({'unlocked' if pack.unlocked else pack.source}); costs an "
        "Empty Canister per m3 unless you unpackage it back"
    )
    label = f"package -> {game.item_name(packed)}"
    return [_priced_fix("package", label, detail, scenario, objective, base_value, probes)]


def _unlock_fixes(
    sc: Scenario, objective: str, base_value: float | None, blocker: Blocker, probes: _Probes
) -> list[Fix]:
    """Each locked consumer, solved as if unlocked with its machine buildable."""
    fixes: list[Fix] = []
    for outlet in blocker.locked_outlets:
        if not probes.spend():
            break
        fixes.append(
            _priced_fix(
                "unlock",
                f"unlock {outlet.name}",
                f"{outlet.source} -- {outlet.building}",
                with_recipes(sc, [outlet.recipe]),
                objective,
                base_value,
                probes,
            )
        )
    return fixes


def _add_fixes(
    game: GameData,
    sc: Scenario,
    objective: str,
    base_value: float | None,
    blocker: Blocker,
    probes: _Probes,
) -> None:
    """Price the things the caller could actually do next, in the caller's units."""
    fixes = [
        *_sink_fix(sc, objective, base_value, blocker, probes),
        *_export_fixes(game, sc, objective, base_value, blocker, probes),
        *_package_fix(game, sc, objective, base_value, blocker, probes),
        *_unlock_fixes(sc, objective, base_value, blocker, probes),
    ]
    # Best first; a fix that moves nothing sinks rather than vanishing, since "it would
    # consume it and gain nothing" is what stops a pointless hard-drive pick.
    fixes.sort(
        key=lambda f: (
            not f.gain,
            -(f.value if f.value is not None else 0.0)
            if objective in _MAXIMISE
            else (f.value if f.value is not None else 1e18),
        )
    )
    blocker.fixes = fixes
