"""Hard-drive research: pending choices and which option to take (pool rule: docs/planning.md)."""

from __future__ import annotations

from ....domain.planning.analysis import advisor
from ....presenters.text import primitives as render
from ....presenters.text.search import item_flows
from .. import app
from ..params import AsOf, Limit

#: Said on every hard-drive response: it decides how hard to think about the choice, and
#: the game's own UI does not show it.
POOL_RULE = (
    "the option you do NOT pick is not lost -- it returns to the pool and a later drive "
    "can offer it again. Only the drive is spent, so pick what helps now"
)

#: Character budget for one option's grant list: 25 drives x 2 options is most of a response.
_GRANT_CHARS = 90


def _grants(option: dict, game) -> str:
    """What an option offers: what its recipes make, and in what.

    The schematic's name is already the recipe's name; what it makes and where is the part
    that decides between two drives.
    """
    if option["slots"]:
        return f"+{option['slots']} inventory slots"
    if not option["recipes"]:
        return "nothing new"
    out = []
    for r in option["recipes"]:
        made = item_flows(game, r.products)
        machine = game.machine(r)
        out.append(f"{made}{f' @{machine.name}' if machine else ''}")
    # Whole entries or none: half a rate ("270 Silic") reads as a smaller number.
    kept: list[str] = []
    while out and len(", ".join([*kept, out[0]])) <= _GRANT_CHARS:
        kept.append(out.pop(0))
    return ", ".join(kept + ([f"+{len(out)} more"] if out else []))


@app.tool()
def list_pending_hard_drive_choices(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """The pending hard-drive choices stored in the save, with rerolls left."""
    st = app.load_world(save, world, as_of)
    g = st.game
    offers = st.hard_drive_offers
    window = render.page(limit, offset, default=25)
    rows = []
    for offer in window.of(offers):
        options = [f"{opt['name']} ({_grants(opt, g)})" for opt in offer.options]
        rows.append((offer.hard_drive_id, offer.rerolls_left, " | ".join(options)))
    last = st.harddrive_desk.last_used_hard_drive_id
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(offers)} unclaimed hard drive(s), each a live choice; "
        f"{st.spare_hard_drives()} unanalysed drive(s) on hand"
        + (f"; drive {last} was the last one analysed" if last is not None else ""),
        render.table(
            ("id", "rerolls", "options"),
            rows,
            total=len(offers),
            offset=window.start,
            limit=window.size,
        ),
        [
            "use advise_hard_drive_pick(hard_drive_id=N) to rank one drive's options",
            "an option shows what its recipes MAKE and where; recipe_detail has the inputs",
            POOL_RULE,
            # Every drive on the reference save shows 2 options and 1 reroll, matching
            # mNumSchematicsPerHardDrive and mNumRerollsPerHardDrive in the headers.
            (
                "each drive offers 2 options and allows 1 reroll; a reroll can re-serve "
                "an excluded schematic when the pool is thin, so it is never simply wasted"
            ),
        ],
    )


@app.tool()
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
    st = app.load_world(save, world, as_of)
    try:
        results = advisor.advise_hard_drive(st, sources, hard_drive_id)
    except ValueError as exc:
        return str(exc)
    if not results:
        return f"no unclaimed hard drive with id {hard_drive_id}"
    advice = results[0]

    rows = []
    for option in advice["options"]:
        deltas = option["deltas"]
        rows.append(
            (
                option["name"],
                render.num(deltas.get("net_mw")),
                render.num(deltas.get("mw_with_products")),
                render.num(deltas.get("min_machines_for_plastic")),
                (
                    f"{render.num(deltas.get('own_output_machines'))} "
                    f"({option.get('own_output_item') or '-'})"
                ),
                ", ".join(option["new_recipes"]) or "-",
                "; ".join(option["new_buildings"] + option["blocked_by"] + option["notes"]) or "",
            )
        )
    baseline = advice["baseline"]
    last = st.harddrive_desk.last_used_hard_drive_id
    return render.envelope(
        "\n".join(
            [
                f"# hard drive {advice['hard_drive_id']}, rerolls left {advice['rerolls_left']}"
                + (f" (drive {last} was the last one analysed)" if last is not None else ""),
                f"# {st.age_note}",
                f"# sources: {advice['basket']}",
                "# baseline: " + render.kv([(k, render.num(v)) for k, v in baseline.items()]),
                f"# {advice['baseline_note']}",
                f"# suggestion: {advice['suggestion']}",
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
            rows,
        ),
        [
            *advice.get("selector_errors", []),
            *advice.get("notes", []),
            "deltas are marginal value vs this world's current recipes",
            "a 0 delta means the player already has a route that dominates it",
            POOL_RULE,
        ],
    )
