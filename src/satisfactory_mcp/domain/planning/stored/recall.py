"""Recalling a stored plan: its arguments merged with this call's overrides.

Every planning tool passes through here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, TypeVar

from .. import siting as siting_mod
from . import provenance as prov
from .plan_args import PLAN_DEFAULTS

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from ...world.state import WorldState
    from .store import StoredPlan

#: The tail of the override note; a caller that then saves swaps it for what happened.
UNSAVED_OVERRIDE = "(not saved -- pass save_as to keep it)"

_Plan = TypeVar("_Plan", bound=str | None)


def _rows(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def merge_rows(stored: object, given: object) -> dict[str, object] | None:
    """``row_overclock`` after a call: the rows it names replace or, as "default", drop theirs."""
    out = dict(_rows(stored))
    for row, choice in _rows(given).items():
        if choice is None or choice == "default":
            out.pop(row, None)
        else:
            out[row] = choice
    return out or None


def with_overrides(stored: Mapping[str, object], overrides: Mapping[str, object]) -> dict:
    """A stored plan's arguments with this call's overrides laid over them."""
    merged = {**PLAN_DEFAULTS, **stored, **overrides}
    if "row_overclock" in overrides:
        merged["row_overclock"] = merge_rows(
            stored.get("row_overclock"), overrides["row_overclock"]
        )
    return merged


def overrides_of(supplied: Mapping[str, object]) -> dict:
    """The arguments this call set away from their declared default: what it overrode."""
    return {k: v for k, v in supplied.items() if k in PLAN_DEFAULTS and v != PLAN_DEFAULTS[k]}


def expand_plan_pin(st: WorldState, plan: _Plan) -> tuple[_Plan | str, str]:
    """``plan`` with a ``pin:N`` swapped for the plan key it pins, and the echo; else as given.

    Raises ``KeyError`` with the refusal, as an unknown plan name does.
    """
    from ...session import pins

    n = pins.parse(plan)
    if n is None:
        return plan, ""
    try:
        found, echo = pins.selector_terms(st, n, "plan")
    except pins.PinError as exc:
        raise KeyError(str(exc)) from None
    return found[0], echo


def recall_plan(
    st: WorldState, plan: str | None, supplied: Mapping[str, object]
) -> tuple[dict, str, list[str]]:
    """Merge a stored plan's arguments with anything explicitly overridden this call.

    Returns (kwargs, resolved plan name, notes).
    """
    clean = {k: v for k, v in supplied.items() if k in PLAN_DEFAULTS}
    if clean.get("row_overclock"):
        clean["row_overclock"] = merge_rows(None, clean["row_overclock"])
    if not plan:
        return clean, "", []
    wanted, echo = expand_plan_pin(st, plan)
    stored = st.plans.find(wanted)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none saved yet)"
        raise KeyError(f"no saved plan named {wanted!r}. Saved: {known}")

    overrides = overrides_of({**clean, "row_overclock": supplied.get("row_overclock")})
    merged = with_overrides(stored.kwargs(), overrides)
    notes = [f'recalled plan "{stored.name}" v{stored.rev}']
    if echo:
        notes.append(echo)
    if stored.notes:
        notes.append(f"{stored.name}: {stored.notes}")
    # The siting rides along on every recall, whichever tool recalled it -- this is the
    # one place all five pass through, the same reason the field check lives here.
    sit = siting_mod.parse(stored)
    if sit is not None:
        notes.append(f"sited: {sit.describe()}")
    changed = sorted(k for k in overrides if stored.kwargs().get(k) != merged.get(k))
    if changed:
        notes.append(
            f"plan {stored.name!r} overridden this call: {', '.join(changed)} {UNSAVED_OVERRIDE}"
        )
    notes.extend(_field_notes(st, stored, merged))
    return merged, stored.name, notes


def _field_notes(st: WorldState, stored: StoredPlan, merged: Mapping[str, object]) -> list[str]:
    """Whether the stored selectors still mean what they meant. See ``provenance``.

    Two conditions buy silence, and both are the right kind. A caller who passed
    ``sources`` this call is not planning over the stored field at all, so a note about it
    would describe a plan that is not being run. And a state with no game data attached
    cannot resolve a selector -- the test doubles in this suite are exactly that -- so
    there is nothing to compare and nothing to claim.
    """
    game = getattr(st, "game", None)
    if game is None or merged.get("sources") != stored.kwargs().get("sources"):
        return []
    try:
        return prov.notes(game, st, stored)
    except FileNotFoundError:
        # The node or region table is not on this machine. The solve is about to fail on
        # the same missing file with a better message; a recall must not pre-empt it with
        # a traceback out of the staleness check.
        return []
