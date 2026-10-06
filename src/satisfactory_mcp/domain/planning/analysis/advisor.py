"""Rank alternate recipes by MARGINAL VALUE, via counterfactual solves.

Solve the player's objective without the candidate, then with all of its schematic's recipes,
and report the delta in real units on a small battery of objectives. docs/planning.md §9.1 is
the method and §9.2 the rules each learned from a wrong answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....core.gamedata.model import GameData, Recipe
from ...spatial.nodes.selectors import SELECTOR_HELP
from ...world.state import WorldState
from ..solver.model import MW, Solution
from ..solver.optimize import solve
from ..solver.scenario import PlanRequest, build_scenario, with_recipes

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
    #: Arguments for ``build_scenario``, never a hand-built Scenario (docs/planning.md §9.2).
    build_kwargs: dict = field(default_factory=dict)
    higher_is_better: bool = True
    #: The Solution field that answers: ``objective_value``, or ``net_mw`` for a power goal,
    #: whose objective carries the machine price (docs/planning.md §9.2).
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

    ``basket`` is worded exactly as plan_factory words its sources: the two describe one scope.
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


def _baseline_request(state: WorldState, sources: list[str] | None, obj: Objective) -> PlanRequest:
    """Baseline scenario for one objective, via the single construction path, which also
    derives its grid import allowance from the export set."""
    return build_scenario(state.game, state, sources=sources, **obj.build_kwargs)


def _own_output_objective(
    game: GameData, recipes: list[Recipe], rate: float = _TARGET_RATE
) -> tuple[Objective, str] | None:
    """An objective on what the candidate itself makes: its highest-throughput product
    (docs/planning.md §9.1)."""
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


def _own_output_delta(
    verdict: CandidateVerdict,
    state: WorldState,
    sources: list[str] | None,
    new: list[Recipe],
    added: list[str],
) -> None:
    """Measure ``verdict`` on its own main product, so it is judged on what it is for."""
    game = state.game
    own = _own_output_objective(game, new)
    if own is None:
        return
    obj, item = own
    sc = _baseline_request(state, sources, obj).scenario
    before = obj.value_of(solve(sc))
    after = obj.value_of(solve(with_recipes(sc, added)))
    verdict.own_output_item = game.item_name(item)
    if before is None and after is not None:
        verdict.notes.append(f"makes {game.item_name(item)} possible where it was not")
        verdict.deltas[obj.key] = None
    elif before is None or after is None:
        verdict.deltas[obj.key] = None
    else:
        verdict.deltas[obj.key] = round(before - after, 3)


def _verdict_for(
    state: WorldState,
    schematic_id: str,
    sources: list[str] | None,
    objs: list[Objective],
    requests: dict[str, PlanRequest],
    base_values: dict[str, float | None],
) -> CandidateVerdict:
    """One candidate schematic, solved against every objective with all its recipes added."""
    game = state.game
    schematic = game.schematics.get(schematic_id)
    new = state.unlocks.schematic_recipes(schematic) if schematic is not None else []
    met, missing = state.dependencies_met(schematic_id)
    verdict = CandidateVerdict(
        schematic=schematic_id,
        name=schematic.name if schematic else schematic_id,
        new_recipes=[r.name for r in new],
        new_buildings=[],
        dependency_missing=[] if met else missing,
    )
    if schematic is not None and schematic.grants_inventory_slots:
        verdict.notes.append(
            f"grants +{schematic.grants_inventory_slots} inventory slots, no recipe"
        )
    if not new:
        verdict.notes.append("unlocks no new recipe for this save")
        return verdict

    for r in new:
        if r.machine and not state.can_build(r.machine):
            verdict.new_buildings.append(f"{game.buildings[r.machine].name} (NOT unlocked)")
        elif r.machine and state.built(r.machine) == 0:
            verdict.new_buildings.append(f"{game.buildings[r.machine].name} (unlocked, 0 built)")

    added = [r.cls for r in new]
    for obj in objs:
        after = obj.value_of(solve(with_recipes(requests[obj.key].scenario, added)))
        before = base_values[obj.key]
        if after is None or before is None:
            verdict.deltas[obj.key] = None
        else:
            delta = after - before
            verdict.deltas[obj.key] = round(delta if obj.higher_is_better else -delta, 3)
    _own_output_delta(verdict, state, sources, new, added)
    return verdict


def evaluate_candidates(
    state: WorldState,
    schematic_ids: list[str],
    sources: list[str] | None = None,
    objectives: list[Objective] | None = None,
) -> Evaluation:
    """Return one verdict per candidate, plus the baseline they are measured against.

    ``sources`` is plan_factory's selector list, so the two tools measure the same nodes.
    """
    objs = objectives or standard_objectives()
    requests = {obj.key: _baseline_request(state, sources, obj) for obj in objs}
    selection = next(iter(requests.values())).selection
    if selection.errors and not selection.nodes:
        # An empty scope still solves, to a confident zero (docs/planning.md §9.2).
        raise ValueError("no sources selected: " + "; ".join([*selection.errors, SELECTOR_HELP]))

    base_values = {obj.key: obj.value_of(solve(requests[obj.key].scenario)) for obj in objs}

    notes: list[str] = []
    # A basket that generates nothing reads 0 on every power delta, whatever the candidate.
    if "net_mw" in base_values and not base_values["net_mw"]:
        notes.append("this basket generates no power on its own -- power deltas will read 0")

    verdicts = [
        _verdict_for(state, sid, sources, objs, requests, base_values) for sid in schematic_ids
    ]
    verdicts.sort(key=lambda x: -max((d or 0.0) for d in x.deltas.values() or [0.0]))
    return Evaluation(
        verdicts=verdicts,
        baseline=base_values,
        basket=selection.description,
        selector_errors=list(selection.errors),
        notes=notes,
    )


def _suggestion(ev: Evaluation) -> str:
    """The best researchable option that moves a metric, or why there is none (§9.2)."""
    best = next((v for v in ev.verdicts if not v.dependency_missing), None)
    if best and best.any_gain:
        return best.name
    if any(v.any_gain and v.dependency_missing for v in ev.verdicts):
        return "every option that moves a metric is dependency-blocked"
    return "neither moves any objective"


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
        out.append(
            {
                "hard_drive_id": offer.hard_drive_id,
                "rerolls_left": offer.rerolls_left,
                "baseline": ev.baseline,
                "basket": ev.basket,
                # Named so the user can check the comparison the baseline invites.
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
                "suggestion": _suggestion(ev),
            }
        )
    return out
