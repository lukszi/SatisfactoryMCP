"""Rank alternate recipes by MARGINAL VALUE, via counterfactual solves.

Solve the player's objective without the candidate, then with it, and report the
delta in real units. That is a far better answer than a tier list, because a
recipe's worth depends entirely on what the player already has -- e.g. both
turbofuel alternates can be worth nothing to someone who already owns Diluted Fuel.

Methodology rules, each learned from a wrong answer during design:

* The counterfactual must add **every new recipe of the schematic**. Two alternate
  schematics carry three recipes, not one.
* The baseline must include everything the player actually has. A demo that omitted
  the 32 coal generators they had already built concluded turbofuel was worthless.
* Report deltas per objective and never collapse them into one score without naming
  the tradeoff: a recipe can be useless for power and excellent for parts.
* Deltas ramp, they do not step. Near a binding constraint a coarse sweep reports a
  flat delta and then a cliff, both wrong.
* **The baseline must be the same quantity plan_factory reports.** Every scenario
  here is built by ``solver.scenario.build_scenario``, the one construction path,
  so raw material arrives through real extractor processes on real nodes -- power
  charged, count capped by node availability. Feeding the same basket in as free
  ``raw_caps`` instead inflated the northern baseline from 92,269 MW to 171,882 MW.
  The deltas mostly survived that, because the bias cancels between the two solves,
  but the absolute number the user is invited to compare against plan_factory did
  not.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ....core.gamedata.model import GameData, Recipe
from ...spatial.select import SELECTOR_HELP
from ...world.state import WorldState
from ..solver.model import MW, Scenario, Solution
from ..solver.optimize import solve
from ..solver.scenario import PlanRequest, build_scenario

__all__ = [
    "CandidateVerdict",
    "Evaluation",
    "Objective",
    "advise_hard_drive",
    "evaluate_candidates",
    "standard_objectives",
]

#: Throughput the min-machines objectives are measured at. Any fixed rate works --
#: an LP solution is a ray -- but the number is reported to the user, so it is named.
_TARGET_RATE = 300.0


@dataclass
class Objective:
    """One yardstick to measure a candidate against."""

    key: str
    description: str
    unit: str
    #: Arguments for ``build_scenario``, NOT for ``Scenario`` directly. Going through
    #: the one construction path is what makes these figures mean the same thing as
    #: plan_factory's; a hand-built Scenario silently reintroduces free raw material.
    build_kwargs: dict = field(default_factory=dict)
    higher_is_better: bool = True
    #: Which field of the Solution is the answer: "objective_value" or "net_mw".
    #:
    #: For a power objective they are NOT the same number. The LP maximises
    #: ``net_mw - machine_cost_mw * machines`` so that spreading throughput over more
    #: machines has to pay for itself; the penalty is a shaping term, not power the
    #: player loses, and plan_factory reports ``net_mw``. Reading objective_value here
    #: put the baseline 4,966 MW below the plan_factory figure it invites comparison
    #: with -- a smaller version of exactly the bug free raw_caps caused.
    metric: str = "objective_value"

    def value_of(self, sol: Solution) -> float | None:
        if not sol.ok:
            return None
        return sol.net_mw if self.metric == "net_mw" else sol.objective_value


@dataclass
class CandidateVerdict:
    schematic: str
    name: str
    new_recipes: list[str]
    new_buildings: list[str]
    deltas: dict[str, float | None] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dependency_missing: list[str] = field(default_factory=list)
    own_output_item: str | None = None

    @property
    def any_gain(self) -> bool:
        return any(v is not None and v > 1e-6 for v in self.deltas.values())


@dataclass
class Evaluation:
    """Verdicts plus the baseline they were measured against.

    ``basket`` is worded exactly as plan_factory words its sources, because the two
    tools now describe the same node scope and a different phrasing would suggest
    otherwise.
    """

    verdicts: list[CandidateVerdict]
    baseline: dict[str, float | None]
    basket: str
    selector_errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def standard_objectives() -> list[Objective]:
    """A small battery, so a candidate is never judged on power alone."""
    return [
        Objective(
            key="net_mw",
            description="max net MW from the given resource basket",
            unit="MW",
            metric="net_mw",
            build_kwargs=dict(objective="max_mw", exports=[MW], allow_sinks=True),
        ),
        Objective(
            key="mw_with_products",
            description="max net MW while exporting Plastic and Rubber",
            unit="MW",
            metric="net_mw",
            build_kwargs=dict(
                objective="max_mw",
                exports=[MW, "Desc_Plastic_C", "Desc_Rubber_C"],
                allow_sinks=True,
            ),
        ),
        Objective(
            key="min_machines_for_plastic",
            description=f"fewest machines for {_TARGET_RATE:g} Plastic/min",
            unit="machines",
            higher_is_better=False,
            build_kwargs=dict(
                objective="min_machines",
                exports=["Desc_Plastic_C"],
                export_minimums={"Desc_Plastic_C": _TARGET_RATE},
                allow_sinks=True,
            ),
        ),
    ]


def _new_recipes_for(state: WorldState, schematic_id: str) -> list[Recipe]:
    s = state.game.schematics.get(schematic_id)
    if s is None:
        return []
    return state._schematic_recipes(s)


def _request(state: WorldState, sources: list[str] | None, obj: Objective) -> PlanRequest:
    """Baseline scenario for one objective, via the single construction path.

    build_scenario also derives ``grid_import_mw`` from the export set, so the
    min-machines objectives get their import allowance without this module having to
    restate a rule that lives there.
    """
    return build_scenario(state.game, state, sources=sources, **obj.build_kwargs)


def _solve_with(sc: Scenario, state: WorldState, extra: list[str]) -> Solution:
    """Re-solve a baseline scenario with a candidate's recipes bolted on."""
    if not extra:
        return solve(sc)
    have = set(sc.recipes)
    # A duplicate recipe id would collide two process columns and trip the optimizer's
    # duplicate-pid assertion, so filter rather than trust the caller.
    added = [r for r in extra if r not in have]
    return solve(
        replace(
            sc,
            recipes=[*sc.recipes, *added],
            buildings_available=(sc.buildings_available or set()) | _needed_buildings(state, added),
        )
    )


def _needed_buildings(state: WorldState, recipe_ids: list[str]) -> set[str]:
    """Buildings a candidate's recipes require.

    Included so a candidate is not silently judged infeasible for needing a machine
    the player has unlocked but never placed -- that is reported as a note instead.
    """
    out: set[str] = set()
    for rid in recipe_ids:
        r = state.game.recipes.get(rid)
        if r is not None and r.machine:
            out.add(r.machine)
    return out


def _own_output_objective(
    game: GameData, recipes: list[Recipe], rate: float = _TARGET_RATE
) -> tuple[Objective, str] | None:
    """An objective measured on what the candidate ITSELF makes.

    Without this a fixed battery reports 0 for every candidate outside its scope --
    Coated Cable makes Cable, so a plastic-and-power battery can never see its value.
    The primary product is the highest-throughput one, which is what the recipe is
    'for'.
    """
    products: dict[str, float] = {}
    for r in recipes:
        for f in r.products:
            products[f.item] = products.get(f.item, 0.0) + f.per_min
    if not products:
        return None
    item = max(products, key=lambda i: products[i])
    return (
        Objective(
            key="own_output_machines",
            description=f"fewest machines for {rate:g}/min {game.item_name(item)}",
            unit="machines",
            higher_is_better=False,
            build_kwargs=dict(
                objective="min_machines",
                exports=[item],
                export_minimums={item: rate},
                allow_sinks=True,
            ),
        ),
        item,
    )


def evaluate_candidates(
    state: WorldState,
    schematic_ids: list[str],
    sources: list[str] | None = None,
    objectives: list[Objective] | None = None,
) -> Evaluation:
    """Return one verdict per candidate, plus the baseline they are measured against.

    ``sources`` is plan_factory's selector list, not a resource-cap table: the two
    tools must be talking about the same nodes for their numbers to be comparable.
    """
    game = state.game
    objs = objectives or standard_objectives()

    requests = {obj.key: _request(state, sources, obj) for obj in objs}
    sel = next(iter(requests.values())).selection
    if sel.errors and not sel.nodes:
        # select_nodes already declines to widen (§7.3, docs/spatial-and-map.md), so the
        # danger is not a whole-map answer -- it is that the empty scope still SOLVES.
        # build_scenario always grants water pumps, so a typo'd region yields a feasible
        # baseline of 0 MW and a 0 delta on every option: a confident "neither is worth
        # anything" that reads as a verdict rather than as a misspelling. Refuse instead.
        raise ValueError("no sources selected: " + "; ".join([*sel.errors, SELECTOR_HELP]))

    base_values: dict[str, float | None] = {}
    for obj in objs:
        base_values[obj.key] = obj.value_of(_solve_with(requests[obj.key].scenario, state, []))

    notes: list[str] = []
    # A basket with no fuel or coal generates nothing, so every power delta is 0 for a
    # reason that has nothing to do with the candidates. Say so rather than let four
    # zeroes read as four verdicts.
    if "net_mw" in base_values and not base_values["net_mw"]:
        notes.append("this basket generates no power on its own -- power deltas will read 0")

    verdicts: list[CandidateVerdict] = []
    for sid in schematic_ids:
        s = game.schematics.get(sid)
        new = _new_recipes_for(state, sid)
        met, missing = state.dependencies_met(sid)
        v = CandidateVerdict(
            schematic=sid,
            name=s.name if s else sid,
            new_recipes=[r.name for r in new],
            new_buildings=[],
            dependency_missing=[] if met else missing,
        )
        if s is not None and s.grants_inventory_slots:
            v.notes.append(f"grants +{s.grants_inventory_slots} inventory slots, no recipe")
        if not new:
            v.notes.append("unlocks no new recipe for this save")
            verdicts.append(v)
            continue

        for r in new:
            if r.machine and not state.can_build(r.machine):
                v.new_buildings.append(f"{game.buildings[r.machine].name} (NOT unlocked)")
            elif r.machine and state.built(r.machine) == 0:
                v.new_buildings.append(f"{game.buildings[r.machine].name} (unlocked, 0 built)")

        extra = [r.cls for r in new]
        for obj in objs:
            after = obj.value_of(_solve_with(requests[obj.key].scenario, state, extra))
            before = base_values[obj.key]
            if after is None or before is None:
                v.deltas[obj.key] = None
            else:
                delta = after - before
                v.deltas[obj.key] = round(delta if obj.higher_is_better else -delta, 3)

        # Plus an objective on the candidate's own product, so it is judged on what
        # it is actually for.
        own = _own_output_objective(game, new)
        if own is not None:
            obj, item = own
            sc = _request(state, sources, obj).scenario
            before = obj.value_of(_solve_with(sc, state, []))
            after = obj.value_of(_solve_with(sc, state, extra))
            v.own_output_item = game.item_name(item)
            if before is None and after is not None:
                v.notes.append(f"makes {game.item_name(item)} possible where it was not")
                v.deltas["own_output_machines"] = None
            elif before is None or after is None:
                v.deltas["own_output_machines"] = None
            else:
                v.deltas["own_output_machines"] = round(before - after, 3)
        verdicts.append(v)

    verdicts.sort(key=lambda x: -max((d or 0.0) for d in x.deltas.values() or [0.0]))
    return Evaluation(
        verdicts=verdicts,
        baseline=base_values,
        basket=sel.description,
        selector_errors=list(sel.errors),
        notes=notes,
    )


def advise_hard_drive(
    state: WorldState,
    sources: list[str] | None = None,
    hard_drive_id: int | None = None,
) -> list[dict]:
    """Compare the options on one pending drive, or summarise every drive."""
    offers = state.hard_drive_offers
    if hard_drive_id is not None:
        offers = [o for o in offers if o.hard_drive_id == hard_drive_id]
        if not offers:
            raise ValueError(f"no unclaimed hard drive with id {hard_drive_id}")

    out: list[dict] = []
    for offer in offers:
        ids = [opt["schematic"] for opt in offer.options]
        ev = evaluate_candidates(state, ids, sources)
        # Only a candidate the player can actually research may be recommended.
        # Ranking alone would let a dependency-blocked option become the headline
        # advice purely because it scored well, and 24 of the 109 alternates are
        # blocked on this save.
        best = next((v for v in ev.verdicts if not v.dependency_missing), None)
        out.append(
            {
                "hard_drive_id": offer.hard_drive_id,
                "rerolls_left": offer.rerolls_left,
                "baseline": ev.baseline,
                "basket": ev.basket,
                # Named so the user can check the comparison the baseline invites,
                # rather than being left to assume the two tools agree.
                "baseline_note": (
                    "net_mw is plan_factory(objective=max_mw, same sources, exports=[MW])"
                ),
                "notes": ev.notes,
                "selector_errors": ev.selector_errors,
                "options": [
                    {
                        "name": v.name,
                        "schematic": v.schematic,
                        "new_recipes": v.new_recipes,
                        "new_buildings": v.new_buildings,
                        "deltas": v.deltas,
                        "own_output_item": v.own_output_item,
                        "notes": v.notes,
                        "blocked_by": v.dependency_missing,
                    }
                    for v in ev.verdicts
                ],
                "suggestion": (
                    best.name
                    if best and best.any_gain
                    else (
                        "every option that moves a metric is dependency-blocked"
                        if any(v.any_gain and v.dependency_missing for v in ev.verdicts)
                        else "neither moves any objective"
                    )
                ),
            }
        )
    return out
