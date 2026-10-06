"""What a planning call asks for: plan names and pins, recalled requests, argument checks."""

from __future__ import annotations

from dataclasses import dataclass

from .....domain.planning.stored.planlog import InvalidOp, PlanArgs
from .....domain.planning.stored.recall import PLAN_DEFAULTS, expand_plan_pin, recall_plan
from ... import app

#: The power-horizon arguments, checked together before anything is solved.
POWER_ARG_KEYS = ("payback_hours", "overclock_last", "power_price")


@dataclass(frozen=True)
class RecalledRequest:
    """A call's solve arguments, laid over the stored plan it named, if any."""

    plan: str | None
    kwargs: dict
    name: str
    notes: list[str]
    objective: str


def _refusal(exc: Exception) -> str:
    return exc.args[0] if isinstance(exc, KeyError) and exc.args else str(exc)


def _plan_pin(st, plan: str | None) -> tuple[str | None, list[str]]:
    """``plan`` with a plan pin swapped for its key, and the echo. Raises ``KeyError``."""
    found, echo = expand_plan_pin(st, plan)
    return found, [echo] if echo else []


def _pinned_plan_name(st, name: str) -> str:
    """``name`` with a plan pin swapped for its key; a ``Refusal`` for a pin that is not one."""
    try:
        found, _echo = _plan_pin(st, name)
    except KeyError as exc:
        raise app.Refusal(f"! {exc.args[0]}") from None
    return found


def _unknown_plan(st, name: str) -> app.Refusal:
    known = ", ".join(x.name for x in st.plans.plans) or "(none)"
    return app.Refusal(f"! no saved plan named {name!r}. Saved: {known}")


def _find_stored_plan(st, name: str):
    """The stored plan ``name`` or its pin names; a ``Refusal`` listing the saved ones."""
    name = _pinned_plan_name(st, name)
    stored = st.plans.find(name)
    if stored is None:
        raise _unknown_plan(st, name)
    return stored


def _solve_args(**given) -> dict:
    """The solve arguments a call supplied, under the names a stored plan keeps them by."""
    unknown = set(given) - set(PLAN_DEFAULTS)
    assert not unknown, f"not stored plan arguments: {sorted(unknown)}"
    return given


def _recall_request(st, plan: str | None, supplied: dict) -> RecalledRequest:
    """``supplied`` laid over the stored plan ``plan`` names; a ``Refusal`` for an unknown one."""
    try:
        plan, pin_notes = _plan_pin(st, plan)
        kwargs, name, notes = recall_plan(st, plan, supplied)
    except KeyError as exc:
        raise app.Refusal(f"! {exc.args[0]}") from None
    return RecalledRequest(
        plan=plan,
        kwargs=kwargs,
        name=name,
        notes=[*pin_notes, *notes],
        objective=kwargs.get("objective") or supplied["objective"],
    )


def _resolve_required(entries: list[str] | None) -> tuple[list[str] | None, str]:
    """Recipe class ids for ``required``, by exact id or exact display name, or a refusal."""
    if not entries:
        return None, ""
    recipes = app.game().recipes
    out = []
    for raw in entries:
        text = str(raw).strip()
        if text in recipes:
            out.append(text)
            continue
        hits = sorted(r.cls for r in recipes.values() if r.name.casefold() == text.casefold())
        if not hits:
            return None, (
                f"! required: no recipe is called {raw!r} -- pass its exact name or class id "
                "(search_recipes shows both); nothing solved"
            )
        if len(hits) > 1:
            return None, (
                f"! required: {raw!r} names {len(hits)} recipes ({', '.join(hits[:4])}) -- "
                "pass the class id; nothing solved"
            )
        out.append(hits[0])
    return out, ""


def _resolve_row_overclock(rows: dict | None) -> tuple[dict | None, str]:
    """``row_overclock`` keyed by recipe class id, as ``required`` resolves, or a refusal."""
    if not rows:
        return rows, ""
    out = {}
    for name, choice in rows.items():
        if choice not in ("last", "spread", "default", None):
            return None, (
                f"! row_overclock[{name!r}] must be 'last', 'spread' or 'default', not "
                f"{choice!r}; nothing solved"
            )
        ids, refused = _resolve_required([name])
        if refused:
            return None, refused.replace("! required:", "! row_overclock:", 1)
        out[ids[0]] = choice
    return out, ""


#: What ``factory=`` and ``for_factory=`` take besides a factory name.
_FACTORY_WORDS = {
    "auto": "",
    "automatic": "",
    "world": "/world",
    "whole world": "/world",
    "none": "/none",
    "nothing": "/none",
    "nothing built yet": "/none",
}


def _factory_value(text: str | None) -> str | None:
    """A ``factory`` argument as the stored value, or None when it was left blank."""
    if text is None or not text.strip():
        return None
    return _FACTORY_WORDS.get(text.strip().casefold(), text.strip())


def _power_refusal(supplied: dict) -> str:
    """Why the power-horizon arguments cannot be stored, or ''."""
    try:
        PlanArgs.from_dict({k: supplied.get(k) for k in POWER_ARG_KEYS})
    except InvalidOp as exc:
        return f"! {exc}; nothing solved"
    return ""
