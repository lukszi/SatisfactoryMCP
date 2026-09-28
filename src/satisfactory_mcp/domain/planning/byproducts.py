"""Why an equality-balanced plan has no answer: find the byproduct with no outlet.

Every item balance in the LP is an equality, so a byproduct that nothing consumes
does not silently vanish -- it makes the plan infeasible, or (worse, because it is
quiet about it) collapses the plan to nothing and lets a different resource win.
``plan_factory`` can only say "infeasible" and suggest adding something to
``exports``. Working out *which* item is stuck, and what could possibly eat it, is
the job this module does.

**The verdict is always the LP's, never a graph walk.** Two static analyses are
tempting here and both are wrong on this user's own recipe set:

* "an item with no consumer is stuck" misses the real trap. Polymer Resin has two
  unlocked consumers (Residual Plastic, Residual Rubber) and is stuck anyway,
  because what they make -- Plastic and Rubber -- has nowhere to go either.
* "an item whose consumers reach an outlet is fine" over-reports the other way. A
  grounded-chain search says Plastic terminates, via Empty Canister -> Packaged
  Liquid Biofuel -> a Biomass Burner. True as a chain, useless as a plan: nothing
  in a crude-oil scope can supply the biofuel. A chain test ignores co-inputs.

So candidates come from a *relaxed solve* -- every produced item made exportable,
deliberately the naive ``net >= 0`` formulation, used only as a probe and never
shown as a plan -- and each candidate is then confirmed by re-solving with that one
item opened up. An item is named as the blocker only when opening it alone
measurably rescues the plan, and the gain is quoted in the caller's own objective.

The graph work that remains is explanatory, not decisive: which recipes would
consume the item, split by whether this save has unlocked them, and whether the
chain out of it dead-ends in a closed loop. That last one is the trap the design
spec names: Recycled Plastic and Recycled Rubber consume each other's product, and
no non-negative combination of the two absorbs either -- the pair net-CREATES both
out of Fuel, so it can never soak up a resin surplus.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
from scipy.optimize import linprog

from ...core import solverlane
from ...core.gamedata.constants import AWESOME_SINK_MW
from ...core.gamedata.model import GameData
from ...core.gamedata.unlocks import granted_by_label
from ..world.state import WorldState
from .optimize import MW, Process, Scenario, Solution, build_processes, solve
from .scenario import build_scenario, resolve_item

__all__ = ["Blocker", "Fix", "Loop", "Outlet", "Report", "analyse"]

_EPS = 1e-6

#: Objectives whose value gets better as it gets bigger.
_MAXIMISE = ("max_mw", "max_item")

# --------------------------------------------------------------------- objective


def _value(objective: str, sol: Solution) -> float | None:
    """The number the caller actually asked to move, in its own units.

    Not ``objective_value`` for max_mw: that one carries the per-machine penalty, so
    quoting it as MW would understate the plan by a few thousand megawatts.
    """
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
    """Counted, budgeted re-solves.

    Budgeted because every probe is a full MILP: an unbounded fix search would turn
    a diagnostic into a minute of solving. Counted because the caller deserves to
    know how much work the answer cost.
    """

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
    """Net producers and net consumers of each item, over the LP's own columns.

    Built from ``build_processes`` rather than from a recipe list, so the census is
    exactly what the solver sees: recipes whose machine is not unlocked are already
    gone, and extractors and generators are already in.
    """
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
    """Every automatable recipe that consumes the item, unlocked or not.

    Deliberately over the FULL recipe set rather than the allowed one: "you already
    own the fix" and "you need a hard drive" are different answers, and a caller
    staring at an infeasible plan needs to know which of the two they are in.
    """
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
    """The Packager route that turns a fluid into a solid an AWESOME Sink can take.

    Fluids cannot be sunk at all (constants.FLUIDS_CANNOT_BE_SUNK), so for a fluid
    dead end this is frequently the only disposal that physically exists.
    """
    it = game.items.get(item_id)
    if it is None or not it.is_fluid:
        return None
    best: Outlet | None = None
    for r in game.consumers_of(item_id, "part"):
        if r.machine != "Build_Packager_C":
            continue
        packaged = [f.item for f in r.products if not game.items[f.item].is_fluid]
        if not packaged:
            continue
        has_machine = "Build_Packager_C" in state.unlocked_building_ids
        unlocked = state.has_recipe(r.cls) and has_machine
        cand = Outlet(
            recipe=r.cls,
            name=r.name,
            building="Packager",
            unlocked=unlocked,
            net_rate=r.rate_of(item_id),
            products=tuple(packaged),
            # Never left blank when locked: the response prints "LOCKED, <source>",
            # and a missing Packager is a different job from a missing recipe.
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
    #: True when no non-negative combination of the cycle's own recipes reduces any
    #: of its items. Such a cycle can never absorb a surplus, however many you build.
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
    """Can the cycle net-consume any member? And does it net-create them?

    A tiny LP, because the question really is an LP: it asks whether some
    non-negative mix of the cycle's recipes has a negative net on one member without
    a positive net on another. Reading the two Recycled recipes and concluding "they
    consume each other, so they must cancel" gets it exactly backwards.
    """
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
        bool(absorb.success and absorb.fun < -_EPS),
        bool(create.success and -create.fun > _EPS),
    )


def _loop_for(
    game: GameData, procs: list[Process], terminal: set[str], seeds: list[str]
) -> Loop | None:
    """Does the stuck item's chain terminate, or exchange inside a closed cycle?

    Scoped to the DIRECT products of the item's own unlocked consumers, not to a
    full strongly connected component. The big component is not a useful answer --
    it absorbs plenty in principle, none of which this scope can supply -- whereas
    the small one is the exact trap the player walks into: resin ends in Plastic and
    Rubber, whose only mutual consumers are Recycled Rubber and Recycled Plastic.
    """
    members = {s for s in dict.fromkeys(seeds) if s not in terminal and s != MW}
    if len(members) < 2:
        return None
    inner = _cycle_recipes(procs, members)
    if not inner:
        return None
    eats = {i for p in inner for i, r in p.rates.items() if r < -_EPS and i in members}
    makes = {i for p in inner for i, r in p.rates.items() if r > _EPS and i in members}
    # Every member both fed and produced by the same recipe set is what makes this a
    # closed exchange rather than an ordinary chain that happens to fork.
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
    #: Rate the plan would emit once the item is allowed out -- i.e. the size of the
    #: problem, and the belt or pipe count it implies.
    rate: float
    #: How many of the LP's own columns consume it. Zero means nothing you have
    #: touches it at all; non-zero with the item still stuck is the loop trap.
    allowed_consumers: int
    producers: tuple[str, ...]
    outlets: list[Outlet]
    #: False when the item was named by the caller rather than confirmed by a solve.
    confirmed: bool = True
    fixes: list[Fix] = field(default_factory=list)
    loop: Loop | None = None
    packaging: Outlet | None = None

    @property
    def unlocked_outlets(self) -> list[Outlet]:
        return [o for o in self.outlets if o.unlocked]

    @property
    def locked_outlets(self) -> list[Outlet]:
        return [o for o in self.outlets if not o.unlocked]


@dataclass
class Report:
    plan_id: str
    objective: str
    unit: str
    scope: str
    age_note: str
    base_value: float | None
    open_value: float | None
    blockers: list[Blocker]
    #: Items produced with no outlet that opening alone does NOT rescue -- either
    #: irrelevant at this scope, or only blocking jointly with another item.
    also_stuck: list[tuple[str, float]]
    #: Items whose only outlet in the current plan is an AWESOME Sink, and what the
    #: plan is worth if sinking is taken away.
    sink_only: dict[str, float]
    no_sink_value: float | None
    notes: list[str]
    solves: int


# ---------------------------------------------------------------------- analysis


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
) -> Report:
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
        # A typo'd selector and a genuinely stuck byproduct look identical from the
        # solved plan -- both give nothing -- so the two must never be confused.
        notes.append("no node matched these sources, so nothing can be extracted at all")
    probes = _Probes(objective=objective, budget=max_probes)

    focus = resolve_item(game, item) if item else None
    if item and focus is None:
        notes.append(f"unknown item {item!r}; diagnosing the whole scope instead")

    base_value, base = probes.run(sc)

    # The relaxed probe. This IS the naive net>=0 formulation the design rejects,
    # and it exists only to size and rank candidates -- what it exports is precisely
    # the set of items the real, equality-balanced plan has nowhere to put.
    openable = tuple(i for i in sorted(produced) if i != MW and i not in sc.exports)
    open_value, opened = probes.run(replace(sc, exports=(*sc.exports, *openable)))

    surplus = {
        i: rate
        for i, rate in opened.exports.items()
        if i != MW and i not in terminal and rate > _EPS
    }
    if focus is not None:
        surplus = {focus: surplus.get(focus, 0.0)}

    # Every item balance is an EQUALITY, so anything the base plan produces at all is
    # already consumed by it exactly -- it provably has an outlet and cannot be the
    # dead end. Without this the relaxed probe is only a byproduct detector for the
    # maximising objectives: under min_power/min_raw/min_machines, dumping any
    # intermediate is cheaper than processing it, so the probe exports every one of
    # them and every one gets reported as STUCK in a plan that in fact works.
    absorbed = (
        {i for row in base.processes for i, r in row["rates"].items() if r > _EPS}
        if base.ok
        else set()
    )

    blockers: list[Blocker] = []
    also_stuck: list[tuple[str, float]] = []
    for item_id, rate in sorted(surplus.items(), key=lambda kv: -kv[1]):
        it = game.items.get(item_id)
        if it is None:
            continue
        already = item_id in absorbed
        if already and focus is None:
            continue
        # Confirmation, and the only claim this tool makes as fact: does opening
        # THIS item alone move the caller's objective? A candidate that does not is
        # noise -- it is only in the list because the relaxed probe let it out.
        if not probes.spend():
            also_stuck.append((it.name, rate))
            continue
        solo_value, _ = probes.run(replace(sc, exports=(*sc.exports, item_id)))
        confirmed = _improved(objective, base_value, solo_value) and not already
        if not confirmed and focus is None:
            also_stuck.append((it.name, rate))
            continue

        blocker = Blocker(
            item=item_id,
            name=it.name,
            is_fluid=it.is_fluid,
            sinkable=it.sinkable,
            sink_points=it.sink_points,
            rate=round(rate, 2),
            allowed_consumers=len(consumed.get(item_id, ())),
            producers=tuple(
                dict.fromkeys(p.label for p in produced.get(item_id, ()) if p.kind == "recipe")
            ),
            outlets=_outlets(game, state, item_id),
            confirmed=confirmed,
            packaging=_packaging(game, state, item_id),
        )
        blocker.loop = _loop_for(
            game,
            procs,
            terminal,
            [
                prod
                for o in blocker.unlocked_outlets
                for prod in o.products
                if prod not in terminal and prod != item_id
            ],
        )
        # Fixes are only meaningful for an item that is actually blocking. Probing
        # them for one the caller merely asked about measures how some OTHER route
        # opens up, and reports it under this item's name.
        if confirmed:
            _add_fixes(game, sc, objective, base_value, blocker, probes)
        blockers.append(blocker)

    # Sinking is neither free nor always wanted: it is a real belt and 30 MW per
    # Sink. When it is the only thing holding a plan up, that is a finding.
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

    return Report(
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


def _add_fixes(
    game: GameData,
    sc: Scenario,
    objective: str,
    base_value: float | None,
    blocker: Blocker,
    probes: _Probes,
) -> None:
    """Price the things the caller could actually do next, in the caller's units."""
    fixes: list[Fix] = []

    if not sc.allow_sinks and blocker.sinkable and probes.spend():
        belts = max(1, -int(-blocker.rate // max(sc.belt_ipm, 1.0)))
        value, _ = probes.run(replace(sc, allow_sinks=True))
        fixes.append(
            Fix(
                "sink",
                "allow_sinks=true",
                f"{blocker.sink_points} pts, {belts} belt(s) to an AWESOME Sink, "
                f"{belts * AWESOME_SINK_MW:g} MW",
                value,
                _improved(objective, base_value, value),
            )
        )

    seen: set[frozenset[str]] = set()
    for o in blocker.unlocked_outlets:
        wanted = tuple(p for p in o.products if p not in sc.exports)
        if not wanted or frozenset(wanted) in seen:
            continue
        if not probes.spend():
            break
        seen.add(frozenset(wanted))
        value, _ = probes.run(replace(sc, exports=(*sc.exports, *wanted)))
        fixes.append(
            Fix(
                "export",
                "exports+=" + ", ".join(game.item_name(p) for p in wanted),
                f"{o.name} ({o.building}) eats {-o.net_rate:g}/min per machine",
                value,
                _improved(objective, base_value, value),
            )
        )

    pack = blocker.packaging
    if pack is not None and probes.spend():
        packed = pack.products[0]
        # A recipe can be available while its machine is not, in which case it is
        # already in sc.recipes AND reported as locked. Re-adding it builds two
        # columns with the same process id, which build_processes rejects outright.
        extra = [] if pack.recipe in sc.recipes else [pack.recipe]
        value, _ = probes.run(
            replace(
                sc,
                recipes=[*sc.recipes, *extra],
                buildings_available=(sc.buildings_available or set()) | {"Build_Packager_C"},
                exports=(*sc.exports, packed),
            )
        )
        fixes.append(
            Fix(
                "package",
                f"package -> {game.item_name(packed)}",
                f"{pack.name} ({'unlocked' if pack.unlocked else pack.source}); costs an "
                "Empty Canister per m3 unless you unpackage it back",
                value,
                _improved(objective, base_value, value),
            )
        )

    for o in blocker.locked_outlets:
        if not probes.spend():
            break
        machine = game.recipes[o.recipe].machine
        value, _ = probes.run(
            replace(
                sc,
                recipes=[*sc.recipes, *([] if o.recipe in sc.recipes else [o.recipe])],
                buildings_available=(sc.buildings_available or set())
                | ({machine} if machine else set()),
            )
        )
        fixes.append(
            Fix(
                "unlock",
                f"unlock {o.name}",
                f"{o.source} -- {o.building}",
                value,
                _improved(objective, base_value, value),
            )
        )

    # Best first. Fixes that move nothing sink to the bottom instead of being
    # dropped: "that recipe would consume it and still gains you nothing" is a real
    # answer, and it is the one that stops a pointless hard-drive pick.
    fixes.sort(
        key=lambda f: (
            not f.gain,
            -(f.value if f.value is not None else 0.0)
            if objective in _MAXIMISE
            else (f.value if f.value is not None else 1e18),
        )
    )
    blocker.fixes = fixes
