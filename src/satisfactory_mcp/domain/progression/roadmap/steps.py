"""What to do next toward the target phase: at most five steps, prerequisites first.

Each owed part asks for one of three things: a line (its recipe is unlocked and nothing makes
it), more of a line (made slower than the horizon needs), or the unlock its recipe waits on.
An unlock asks in turn for the items its bill is short of that nothing makes, and for its own
prerequisites. docs/progress_roadmap.md, "Next steps", has the ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ....core.gamedata.model import Schematic
from ..ladder import Rung, SchematicLadder
from .bill import SYSTEM_OF_TYPE, BillPart, Makers, default_recipe, unlock_of
from .views import NextStep, ShortItem

if TYPE_CHECKING:
    from ...world.state import WorldState

__all__ = ["MAX_STEPS", "next_steps"]

MAX_STEPS = 5

#: How deep an unlock's prerequisites are followed; the HUB and MAM run two levels at most.
_DEPTH = 3

_TOL = 1e-6

_KIND_ORDER = {"make": 0, "line": 1, "grow": 2, "unlock": 3}

Key = tuple[str, str]


@dataclass
class _Step:
    kind: str
    subject: str
    amount: float = 0.0
    per_min: float = 0.0
    schematic: Schematic | None = None
    rung: Rung | None = None
    #: Unlock name -> how much of the item it is short; a make step's amount is their sum.
    wanted_by: dict[str, float] = field(default_factory=dict[str, float])
    deps: set[Key] = field(default_factory=set[Key])
    parts: set[str] = field(default_factory=set[str])


@dataclass
class _Walk:
    st: WorldState
    makers: Makers
    rungs: dict[str, Rung]
    steps: dict[Key, _Step] = field(default_factory=dict[Key, _Step])

    def unlock(self, schematic: Schematic, depth: int) -> Key | None:
        rung = self.rungs.get(schematic.cls)
        if rung is None or rung.done:
            return None
        key = ("unlock", schematic.cls)
        if key in self.steps:
            return key
        step = self.steps[key] = _Step("unlock", schematic.cls, schematic=schematic, rung=rung)
        if depth < _DEPTH:
            for dep in schematic.dependencies:
                parent = self.st.game.schematics.get(dep)
                found = self.unlock(parent, depth + 1) if parent is not None else None
                if found:
                    step.deps.add(found)
        for missing in rung.missing:
            if self.makers.rate(missing.item) <= _TOL:
                step.deps.add(self.make(missing.item, missing.short_by, schematic.name, depth))
        return key

    def make(self, item: str, amount: float, for_name: str, depth: int) -> Key:
        key = ("make", item)
        step = self.steps.setdefault(key, _Step("make", item))
        step.wanted_by[for_name] = amount
        step.amount = sum(step.wanted_by.values())
        recipe, unlocked = default_recipe(self.st.game, item, self.st.available_recipe_ids)
        if recipe is not None and not unlocked and depth < _DEPTH:
            source = unlock_of(self.st.game, recipe)
            found = self.unlock(source, depth + 1) if source is not None else None
            if found:
                step.deps.add(found)
        return key

    def spread(self, key: Key, part: str) -> None:
        """Mark ``key`` and everything it waits on as opening ``part``."""
        step = self.steps[key]
        if part in step.parts:
            return
        step.parts.add(part)
        for dep in step.deps:
            self.spread(dep, part)


def _order(steps: dict[Key, _Step]) -> list[Key]:
    """Prerequisites first; among steps free to go, work that can start now (make, line,
    grow) before an unlock, then the step opening most parts."""
    seq = {key: i for i, key in enumerate(steps)}
    done: list[Key] = []
    left = set(steps)
    while left:
        ready = [k for k in left if not (steps[k].deps & left)] or list(left)
        ready.sort(
            key=lambda k: (
                steps[k].kind == "unlock",
                -len(steps[k].parts),
                _KIND_ORDER[steps[k].kind],
                seq[k],
            )
        )
        done.append(ready[0])
        left.discard(ready[0])
    return done


def _inputs_held(st: WorldState, item: str, amount: float, stock: dict[str, float]) -> bool | None:
    recipe, unlocked = default_recipe(st.game, item, st.available_recipe_ids)
    if recipe is None or not unlocked:
        return None
    made = sum(f.amount for f in recipe.products if f.item == item) or 1.0
    return all(stock.get(f.item, 0.0) >= f.amount / made * amount for f in recipe.ingredients)


def _short(st: WorldState, rung: Rung | None) -> list[ShortItem]:
    if rung is None:
        return []
    return [
        {"item": m.item, "name": st.game.item_name(m.item), "short": round(m.short_by, 1)}
        for m in rung.missing
    ]


def _row(walk: _Walk, step: _Step, after: list[int], stock: dict[str, float]) -> NextStep:
    st, game = walk.st, walk.st.game
    schematic = step.schematic
    is_item = step.kind != "unlock"
    return {
        "kind": step.kind,
        "item": step.subject if is_item else None,
        "name": game.item_name(step.subject) if is_item else schematic.name if schematic else "",
        "amount": round(step.amount, 1),
        "per_min": round(step.per_min, 4),
        "made_per_min": walk.makers.rate(step.subject) if is_item else 0.0,
        "system": SYSTEM_OF_TYPE.get(schematic.type) if schematic else None,
        "status": step.rung.status if step.rung else None,
        "for_unlocks": list(step.wanted_by),
        "parts": sorted(game.item_name(p) for p in step.parts),
        "after": after,
        "inputs_held": _inputs_held(st, step.subject, step.amount, stock)
        if step.kind == "make"
        else None,
        "short": _short(st, step.rung),
    }


def next_steps(
    st: WorldState, parts: list[BillPart], makers: Makers, rates: dict[str, float]
) -> list[NextStep]:
    """The first ``MAX_STEPS`` steps toward delivering ``parts`` at ``rates`` per minute."""
    ladder = SchematicLadder(game=st.game, unlocks=st.unlocks, inventory=st.inventory)
    rungs = {r.schematic.cls: r for t in ("EST_Milestone", "EST_MAM") for r in ladder.rungs(t)}
    walk = _Walk(st=st, makers=makers, rungs=rungs)
    have = st.available_recipe_ids
    for part in parts:
        need = rates.get(part.item, 0.0)
        if need <= 0:
            continue
        recipe, unlocked = default_recipe(st.game, part.item, have)
        root: Key | None = None
        if unlocked:
            made = makers.rate(part.item)
            if made + _TOL < need:
                root = ("line" if made <= _TOL else "grow", part.item)
                walk.steps[root] = _Step(root[0], part.item, amount=part.short, per_min=need)
        elif recipe is not None:
            source = unlock_of(st.game, recipe)
            root = walk.unlock(source, 0) if source is not None else None
        if root:
            walk.spread(root, part.item)
    order = _order(walk.steps)[:MAX_STEPS]
    number = {key: i + 1 for i, key in enumerate(order)}
    stock = st.stock()
    return [
        _row(
            walk,
            walk.steps[key],
            sorted(number[d] for d in walk.steps[key].deps if d in number),
            stock,
        )
        for key in order
    ]
