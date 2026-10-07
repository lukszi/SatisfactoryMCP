"""The world's plan log as the planning tools write it: refusals, stamps, journal views."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping

from .....core.filelock import LockTimeout
from .....domain.planning.readout import summary
from .....domain.planning.stored.plan_args import PlanLogError
from .....domain.planning.stored.planlog import (
    AlreadyUndone,
    BaseRevRequired,
    Commit,
    Forgotten,
    Outdated,
    PlanLog,
    Pushed,
    Stamp,
    describe_commit,
    describe_op,
)
from .....domain.planning.stored.views import PlanOp
from .....domain.session import journal
from .....domain.world.state import WorldState
from ... import app

BUSY = "! plans are busy (another writer held the lock 10 s); nothing written"


def age(seconds: float) -> str:
    """A duration in its largest whole unit: ``42s``, ``5m``, ``3h``, ``2d``."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def world_plan_log(st: WorldState) -> PlanLog:
    return PlanLog(st.world_id, st.session_name)


def _with_recipe_names(ops: list[PlanOp], names: dict[str, str]) -> list[PlanOp]:
    out: list[PlanOp] = []
    for op in ops:
        member = op.get("member")
        if (
            op.get("field") in ("banned", "required")
            and isinstance(member, str)
            and member in names
        ):
            op = {**op, "member": names[member]}
        out.append(op)
    return out


def ops_text(ops: list[PlanOp], names: dict[str, str]) -> str:
    return " · ".join(t for t in (describe_op(o) for o in _with_recipe_names(ops, names)) if t)


def commit_text(commit: Commit, names: dict[str, str]) -> str:
    shown = copy.copy(commit)
    shown.ops = _with_recipe_names(commit.ops, names)
    return describe_commit(shown)


def needs_base(name: str, head: int, nothing: str) -> str:
    return (
        f'! plan "{name}" exists at v{head}: read it (list_plans name="{name}") and pass '
        f"base_rev={head}; {nothing}"
    )


def write(name: str, nothing: str, push: Callable[[], Pushed]) -> tuple[Pushed | None, str]:
    """Run one plan write and word its outcome: the merged note, or why nothing landed."""
    try:
        pushed = push()
    except Outdated as exc:
        return None, exc.text(name)
    except BaseRevRequired as exc:
        return None, needs_base(name, exc.head, nothing)
    except Forgotten as exc:
        return None, (
            f'! plan "{name}" was forgotten in v{exc.rev}; plan_log name="{name}" '
            f"undo={exc.rev} brings it back; {nothing}"
        )
    except AlreadyUndone as exc:
        return None, f"! v{exc.rev} was already undone by v{exc.by}; {nothing}"
    except LockTimeout:
        return None, BUSY
    except PlanLogError as exc:
        return None, f"! {exc}; {nothing}"
    return pushed, pushed.text(pushed.state.name or name)


def head_stamper(st: WorldState) -> Stamp:
    """What the store records on the new head: its solve-input hash and resolved field."""
    return summary.stamp_for(app.game(), st)


def journal_view(
    st: WorldState,
    plan: str | None,
    tool: str,
    ctx: app.ToolContext | None,
    args: Mapping[str, object] | None = None,
) -> None:
    if not plan:
        return
    stored = st.plans.find(plan)
    if stored is None:
        return
    journal.append(
        st.world_id,
        "plan.view",
        actor=app.actor(ctx),
        sav=app.save_token(st),
        tool=tool,
        plan=stored.key,
        rev=stored.rev,
        args=args,
        text=f'{tool} on plan "{stored.name}" v{stored.rev}',
    )
