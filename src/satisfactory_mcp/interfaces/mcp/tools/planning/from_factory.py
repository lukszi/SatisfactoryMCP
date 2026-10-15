"""``plan_from_factory``: a stored plan that mirrors a named factory as built."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .....domain.factories.select import SelectorError
from .....domain.planning.progress.as_built import FromFactory
from .....domain.planning.progress.as_built import plan_from_factory as make_plan
from .....domain.planning.stored.planlog import Pushed
from .....presenters.text import primitives as render
from ... import app
from ...params import AsOf
from ._plan_log import write

DIFF_LINES = 12


def _reply(made: FromFactory) -> str:
    plan, solved = made.pushed.state, made.solve
    head = [
        f'# plan "{plan.name}" v{plan.rev} (key {plan.key}) from factory "{made.built.factory}"',
        render.kv(
            [
                ("solves", "yes" if solved["feasible"] else "NO"),
                ("machines", solved["machines"] if solved["feasible"] else ""),
                ("plan_MW", solved["mw_draw"] if solved["feasible"] else ""),
                ("built_MW", round(made.built.draw_mw, 1)),
            ]
        ),
    ]
    shown = made.differences[:DIFF_LINES]
    body = "differs from the build:\n" + render.bullets(shown) if shown else "solves to the build"
    if len(made.differences) > DIFF_LINES:
        body += f"\n(+{len(made.differences) - DIFF_LINES} more in the plan's notes)"
    notes = [
        *made.built.notes,
        f"edit it with base_rev={plan.rev}; forget_plan name={plan.name!r} undoes it",
    ]
    return render.envelope("\n".join(head), body, notes)


@app.tool()
def plan_from_factory(
    factory: Annotated[str, Field(description="a named factory, as list_factories prints it")],
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    ctx: app.ToolContext | None = None,
) -> str:
    """Store a new plan that mirrors a named factory as built, to edit or extend.

    Its recipes are pinned, its surplus are exports and its inputs supplied at today's rates;
    it is sited on the factory and tracks it. Each call makes a new plan. What a solve of
    it does differently from the build is listed and kept in the plan's notes.
    """
    st = app.load_world(save, world, as_of)
    made: list[FromFactory] = []

    def push() -> Pushed:
        made.append(make_plan(st, factory, actor=app.actor(ctx), sav=app.save_token(st)))
        return made[-1].pushed

    try:
        pushed, text = write(factory, "no plan made", push)
    except SelectorError as exc:
        return f"! {exc}; no plan made"
    if pushed is None:
        return text
    return _reply(made[-1])
