"""Recalling a stored plan: what its arguments were, and what this call overrode.

A stored plan is a stored planning REQUEST, so merging one back in is a planning
decision rather than an argument-parsing detail of the tool that happens to take it.

It is also the one place every planning tool passes through -- plan_factory, plan_layout,
diff_vs_save, commission_plan and rank_unlocks all reach a stored plan through here -- so
it is where the field check belongs. Written into each tool instead it would be five
copies, and the copy that was forgotten would be the tool that answered silently.
"""

from __future__ import annotations

from .. import siting as siting_mod
from . import provenance as prov

#: The declared default of every stored planning argument. Needed because MCP fills
#: defaults in before the tool sees them, so "objective" always arrives as "max_mw" and
#: a naive merge would clobber every recalled plan with it. A supplied value counts as an
#: override only when it DIFFERS from the default here.
#:
#: The cost is one honest limitation: recalling a plan cannot explicitly reset a
#: parameter back to its default. Edit the plan (save_as over the same name) for that.
#: The tail of the override note; a caller that then saves swaps it for what happened.
UNSAVED_OVERRIDE = "(not saved -- pass save_as to keep it)"

PLAN_DEFAULTS: dict = {
    "objective": "max_mw",
    "target_item": None,
    "sources": None,
    "exports": None,
    "export_minimums": None,
    "only_free_nodes": False,
    "allow_sinks": True,
    "clocks": None,
    "extractor_clocks": None,
    "machine_cost_mw": 5.0,
    "exclude_recipes": None,
    "required": None,
    "only_recipes": None,
    "water_extractors": None,
    "sloops": 0,
    "recycle_once": None,
    "supplied": None,
    # Carrier throughput SHAPES THE SOLVE -- belt_ipm prices sinks and both split blocks
    # and trunks -- so it belongs with the stored arguments, not with presentation.
    "belt_ipm": None,
    "pipe_m3min": None,
    # None follows the shared default, so 0 and false are overrides a caller can name, and
    # "default" puts a recalled plan back on the shared value.
    "payback_hours": None,
    "overclock_last": None,
    "power_price": None,
    # Per recipe id: "last" or "spread". A call names only the rows it changes and
    # "default" puts one back on the plan's switch (``merge_rows``).
    "row_overclock": None,
}


def merge_rows(stored: dict | None, given: dict | None) -> dict | None:
    """``row_overclock`` after a call: the rows it names replace or, as "default", drop theirs."""
    out = dict(stored or {})
    for row, choice in (given or {}).items():
        if choice is None or choice == "default":
            out.pop(row, None)
        else:
            out[row] = choice
    return out or None


def with_overrides(stored: dict, overrides: dict) -> dict:
    """A stored plan's arguments with this call's overrides laid over them."""
    merged = {**PLAN_DEFAULTS, **stored, **overrides}
    if "row_overclock" in overrides:
        merged["row_overclock"] = merge_rows(
            stored.get("row_overclock"), overrides["row_overclock"]
        )
    return merged


def overrides_of(supplied: dict) -> dict:
    """The arguments this call set away from their declared default: what it overrode."""
    return {k: v for k, v in supplied.items() if k in PLAN_DEFAULTS and v != PLAN_DEFAULTS[k]}


def plan_ref(st, plan: str | None) -> tuple[str | None, str]:
    """``plan`` with a ``pin:N`` swapped for the plan key it pins, and the echo; else as given.

    Raises ``KeyError`` with the refusal, as an unknown plan name does.
    """
    from ...session import pins

    n = pins.parse(plan)
    if n is None:
        return plan, ""
    try:
        found, echo = pins.terms(st, n, "plan")
    except pins.PinError as exc:
        raise KeyError(str(exc)) from None
    return found[0], echo


def recall_plan(st, plan: str | None, supplied: dict) -> tuple[dict, str, list[str]]:
    """Merge a stored plan's arguments with anything explicitly overridden this call.

    Returns (kwargs, resolved plan name, notes).
    """
    clean = {k: v for k, v in supplied.items() if k in PLAN_DEFAULTS}
    if clean.get("row_overclock"):
        clean["row_overclock"] = merge_rows(None, clean["row_overclock"])
    if not plan:
        return clean, "", []
    plan, echo = plan_ref(st, plan)
    stored = st.plans.find(plan)
    if stored is None:
        known = ", ".join(x.name for x in st.plans.plans) or "(none saved yet)"
        raise KeyError(f"no saved plan named {plan!r}. Saved: {known}")

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


def _field_notes(st, stored, merged: dict) -> list[str]:
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
