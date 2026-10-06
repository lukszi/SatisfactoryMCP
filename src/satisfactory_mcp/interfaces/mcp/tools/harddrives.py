"""Hard-drive research: what is waiting to be picked, and which option to take.

Split from ``planning`` because the question is different in kind. The optimiser sizes a
factory you have decided to build; this ranks a one-off choice against the factory you
already have.

**The choice is not irreversible, and saying it was made this advice worse.** Confirmed by
the player in-game (2026-07-28), closing OQ2: the option you do NOT take **returns to the
pool** and can be offered by a later drive. Only the drive is spent, never the schematic.

That inverts the advice. Picking is low-stakes, so the right move is to take whatever helps
the factory you have now and let the other alternate come back around -- there is nothing
to agonise over and no reason to hoard an unclaimed drive waiting for a better roll. The
old framing ("irreversible") argued for exactly the opposite behaviour.
"""

from __future__ import annotations

from ....domain.planning.analysis import advisor
from ....presenters.text import primitives as render
from ..app import AsOf, Limit, _state, mcp

#: Said on every hard-drive response, because it is the fact that decides how hard to
#: think about the choice, and it is not visible anywhere in the game's own UI.
POOL_RULE = (
    "the option you do NOT pick is not lost -- it returns to the pool and a later drive "
    "can offer it again. Only the drive is spent, so pick what helps now"
)


#: Character budget for one option's grant list. 25 drives x 2 options is most of this
#: response, and the whole response has to stay inside a model's context.
_GRANT_CHARS = 90


def _grants(option: dict, game) -> str:
    """What an option is actually offering: what its recipes make, and in what.

    The name of an alternate schematic is the name of the recipe it grants, so listing
    the recipes by name says nothing a reader cannot already see. What it makes and where
    is the part that decides between two drives, and it is the part that used to cost a
    ``recipe_detail`` call per option.
    """
    if option["slots"]:
        return f"+{option['slots']} inventory slots"
    if not option["recipes"]:
        return "nothing new"
    out = []
    for r in option["recipes"]:
        made = render.flows((game.item_name(f.item), f.per_min, False) for f in r.products)
        machine = game.machine(r)
        out.append(f"{made}{f' @{machine.name}' if machine else ''}")
    # Whole entries or none of them: half a rate ("270 Silic") reads as a smaller number
    # rather than as a truncation, which is the one thing this column must not do.
    kept: list[str] = []
    while out and len(", ".join([*kept, out[0]])) <= _GRANT_CHARS:
        kept.append(out.pop(0))
    return ", ".join(kept + ([f"+{len(out)} more"] if out else []))


@mcp.tool(structured_output=False)
def list_pending_hard_drive_choices(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """The pending hard-drive choices stored in the save, with rerolls left."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    offers = st.hard_drive_offers
    rows = []
    start = max(0, offset)
    n = render.clamp(limit, default=25)
    for o in offers[start : start + n]:
        opts = [f"{opt['name']} ({_grants(opt, g)})" for opt in o.options]
        rows.append((o.hard_drive_id, o.rerolls_left, " | ".join(opts)))
    last = st.harddrive_desk.last_used_hard_drive_id
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(offers)} unclaimed hard drive(s), each a live choice; "
        f"{st.spare_hard_drives()} unanalysed drive(s) on hand"
        + (f"; drive {last} was the last one analysed" if last is not None else ""),
        render.table(("id", "rerolls", "options"), rows, total=len(offers), offset=start, limit=n),
        [
            "use advise_hard_drive_pick(hard_drive_id=N) to rank one drive's options",
            "an option shows what its recipes MAKE and where; recipe_detail has the inputs",
            POOL_RULE,
            # Measured across all 25 offers on the reference save: every drive shows
            # exactly 2 options and starts with exactly 1 reroll, which matches
            # mNumSchematicsPerHardDrive and mNumRerollsPerHardDrive in the headers.
            (
                "each drive offers 2 options and allows 1 reroll; a reroll can re-serve "
                "an excluded schematic when the pool is thin, so it is never simply wasted"
            ),
        ],
    )


@mcp.tool(structured_output=False)
def advise_hard_drive_pick(
    hard_drive_id: int,
    sources: list[str] | None = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
) -> str:
    """Rank one pending drive's options by marginal value, via counterfactual LP.

    Each option is solved for and against across several objectives, because a
    recipe can be worthless for power yet excellent for parts. Deltas are reported
    per objective and never collapsed into one score.

    ``sources`` is plan_factory's selector list and means the same thing here, so the
    baseline printed is the same quantity plan_factory reports for the same nodes.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"

    try:
        results = advisor.advise_hard_drive(st, sources, hard_drive_id)
    except ValueError as exc:
        return str(exc)
    if not results:
        return f"no unclaimed hard drive with id {hard_drive_id}"
    res = results[0]

    rows_out = []
    for opt in res["options"]:
        d = opt["deltas"]
        rows_out.append(
            (
                opt["name"],
                render.num(d.get("net_mw")),
                render.num(d.get("mw_with_products")),
                render.num(d.get("min_machines_for_plastic")),
                f"{render.num(d.get('own_output_machines'))} ({opt.get('own_output_item') or '-'})",
                ", ".join(opt["new_recipes"]) or "-",
                "; ".join(opt["new_buildings"] + opt["blocked_by"] + opt["notes"]) or "",
            )
        )
    base = res["baseline"]
    last = st.harddrive_desk.last_used_hard_drive_id
    return render.envelope(
        "\n".join(
            [
                f"# hard drive {res['hard_drive_id']}, rerolls left {res['rerolls_left']}"
                + (f" (drive {last} was the last one analysed)" if last is not None else ""),
                f"# {st.age_note}",
                f"# sources: {res['basket']}",
                "# baseline: " + render.kv([(k, render.num(v)) for k, v in base.items()]),
                f"# {res['baseline_note']}",
                f"# suggestion: {res['suggestion']}",
            ]
        ),
        render.table(
            (
                "option",
                "d_MW",
                "d_MW+products",
                "d_plastic_mach",
                "d_own_output_mach",
                "new recipes",
                "caveats",
            ),
            rows_out,
        ),
        [
            *res.get("selector_errors", []),
            *res.get("notes", []),
            "deltas are marginal value vs this world's current recipes",
            "a 0 delta means the player already has a route that dominates it",
            POOL_RULE,
        ],
    )
