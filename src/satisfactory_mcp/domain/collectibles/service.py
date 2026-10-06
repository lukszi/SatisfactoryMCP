"""What the map placed, what this save took, and which rows answer the question asked.

Which placements answer a question is a domain decision, so it is made here and the presenter
gets a finished view. Every refusal is a string on the view rather than formatted text,
because the caller that renders is not always the caller that decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..spatial.places import resolve_place
from ..world.state import WorldState
from .removed import observed_session
from .table import CollectiblesUnreadable, CollectibleTable, load_collectibles

__all__ = [
    "GENERATOR_COMMAND",
    "LABELS",
    "NEVER_SPOILER",
    "RETIRED_GROUPS",
    "CollectiblesView",
    "census_rows",
    "collect_view",
    "found",
    "is_spoiler",
    "label",
    "state_counts",
    "table_age",
]

#: The views a collectibles question can ask for.
_MODES = ("census", "collected", "remaining", "nearest")

#: What to run when the table is not there. Said in full, because "regenerate it" is not a
#: command and the reader is an assistant relaying it to somebody at a prompt.
GENERATOR_COMMAND = "uv run python tools/gen_world_collectibles.py"

#: Group names this tool used to accept, and what to say instead. Every one of them was a
#: name-prefix bucket; the map's placement table resolves the classes those buckets guessed
#: at, so the answer is a rename in some cases and "that is not a collectible" in others.
RETIRED_GROUPS: dict[str, str] = {
    "slug_blue": "power_slug_blue",
    "slug_yellow": "power_slug_yellow",
    "slug_purple": "power_slug_purple",
    "artifact_unsplit": (
        "the map resolves every glued BP_WAT name, so nothing is unsplit any more: "
        "ask for 'somersloop' or 'mercer_sphere'"
    ),
    "flora": (
        "'flora' mixed four classes. The mushroom is 'mushroom'; berry and nut bushes "
        "REGROW and are deliberately untracked; a spore flower is a hazard, not a pickup"
    ),
    "debris": "crash-site scenery is not a collectible -- see the unresolved rows in the census",
    "crash_site": (
        "the pod itself is 'crashed_drop_pod'; the ship and debris beside it are scenery, "
        "and the parts scattered around it are 'loot_cache'"
    ),
    "dropped_pickup": (
        "a map-placed cache is 'loot_cache'; a pickup the PLAYER dropped is not map-placed "
        "at all and appears among the unresolved rows"
    ),
}


@dataclass
class CollectiblesView:
    """Which placements answer the question, and everything needed to say why."""

    #: The validated mode: census, collected, remaining or nearest.
    mode: str
    #: The category asked for, already folded and un-retired. ``None`` means all of them.
    group: str | None = None
    #: A refusal, already carrying its leading ``!``. Nothing else on the view is populated.
    error: str | None = None
    removed: dict = field(default_factory=dict)
    #: ``None`` when ``data/world_collectibles.json`` has never been generated.
    table: CollectibleTable | None = None
    #: Listing rows, sorted for the mode and distance-annotated when mode=nearest. ``None``
    #: for the census, which counts off ``removed`` instead.
    rows: list[dict] | None = None
    #: Pedestal rows dropped from an unfiltered listing.
    hidden: int = 0
    #: Every category that is the base another one stands on.
    pedestals: list[str] = field(default_factory=list)
    #: Observed state -> how many listing rows are in it.
    counts: dict[str, int] = field(default_factory=dict)
    #: Where distances are measured from, in centimetres, and what to call it.
    origin: tuple[float, float] | None = None
    where: str = ""
    #: True when there is no map table and only the save's own degraded census is possible.
    save_only: bool = False


def _refusal(mode: str, group: str | None, error: str) -> CollectiblesView:
    return CollectiblesView(mode=mode, group=group, error=error)


def _resolve_group(group: str | None, table) -> tuple[str | None, str | None]:
    """``(group, refusal)``: the name folded, and a retired bucket renamed where the map has
    the same thing under a new name and refused where it does not."""
    if not group:
        return group, None
    # Category names are lowercase snake_case, so folding is a normalisation, not a guess.
    group = group.strip().casefold()
    hint = RETIRED_GROUPS.get(group)
    if hint and table is not None and hint in table.by_category:
        return hint, None
    if hint and table is not None:
        return group, f"! '{group}' is no longer a category: {hint}"
    return group, None


def _nearest_origin(st, near: str | None) -> tuple[tuple[float, float] | None, str, str | None]:
    """``(origin, where, refusal)``: ``near`` resolved, else the player's own position."""
    if near:
        try:
            origin, where = resolve_place(st, near)
        except ValueError as exc:
            return None, "", f"! {exc}"
        return origin, where, None
    here = st.player_position()
    if here is None:
        return (
            None,
            "",
            (
                "! the 'nearest' view needs an origin and this save has no player pawn: "
                "pass near='x,y' in metres or a named factory"
            ),
        )
    return (here[0], here[1]), "you", None


def _listing_rows(st, mode: str, group: str | None, origin) -> list[dict]:
    """The placements one listing mode shows, in its order."""
    if mode == "nearest":
        return st.nearest_placements(origin, group)
    if mode == "remaining":
        rows = st.placements(group, remaining_only=True)
    else:
        rows = [p for p in st.placements(group) if p["collected"]]
    rows.sort(key=lambda r: (r["category"], r["name"]))
    return rows


def _without_pedestals(rows: list[dict], table, group: str | None):
    """``(rows, pedestals, hidden)``: a shrine row dropped from an unfiltered listing, where
    it would double-count the artifact standing on it; ``group='mercer_shrine'`` keeps them."""
    pedestals = sorted({c for c in table.by_category if table.pedestal_of(c)})
    if group is not None:
        return rows, pedestals, 0
    kept = [r for r in rows if r["category"] not in pedestals]
    return kept, pedestals, len(rows) - len(kept)


def state_counts(rows) -> dict[str, int]:
    """Listing rows by observed state, ``collected`` first and ``unstated`` for the unknown."""
    counts: dict[str, int] = {}
    for row in rows:
        key = "collected" if row["collected"] else row["observed"] or "unstated"
        counts[key] = counts.get(key, 0) + 1
    return counts


def collect_view(
    st: WorldState, group: str | None, mode: str, near: str | None
) -> CollectiblesView:
    """Pick the placements that answer one collectibles question.

    The order of the checks is the order of the refusals, and it is load-bearing: a caller
    who got two things wrong is told about the first one.
    """
    wanted = (mode or "census").strip().casefold()
    if wanted not in _MODES:
        return _refusal(
            wanted,
            None,
            f"! unknown view {mode!r}. Choose from: census, collected, remaining, nearest",
        )

    table = st.collectibles
    if table is None:
        # ``st.collectibles`` is None for a file never generated and for a broken one, which
        # need different answers; asking again strictly separates them.
        try:
            load_collectibles(strict=True)
        except CollectiblesUnreadable as exc:
            return _refusal(
                wanted,
                group,
                f"! the map's placement table is CORRUPT, not missing: {exc}. Delete it "
                f"and run {GENERATOR_COMMAND} -- until then nothing here knows how many "
                "collectibles exist or where they are",
            )
    group, refused = _resolve_group(group, table)
    if refused:
        return _refusal(wanted, group, refused)

    if table is None and wanted in ("remaining", "nearest"):
        # Refused rather than answered with the census, which would teach the caller that
        # the argument worked.
        return _refusal(
            wanted,
            group,
            f"! the {wanted!r} view needs the map's own placement table and "
            "data/world_collectibles.json has never been generated, so nothing here "
            "knows how many collectibles exist or where they are. Only the census and "
            f"collected views work from a save alone. Generate it with {GENERATOR_COMMAND}",
        )

    removed = st.collected_summary(group)
    if "error" in removed:
        return _refusal(wanted, group, "! " + removed["error"])

    view = CollectiblesView(
        mode=wanted,
        group=group,
        removed=removed,
        table=table,
        save_only=table is None,
    )
    if table is None or wanted == "census":
        return view

    origin, where = None, ""
    if wanted == "nearest":
        origin, where, refused = _nearest_origin(st, near)
        if refused:
            return _refusal(wanted, group, refused)
    rows, view.pedestals, view.hidden = _without_pedestals(
        _listing_rows(st, wanted, group, origin), table, group
    )
    view.rows = rows
    view.counts = state_counts(rows)
    view.origin = origin
    view.where = where
    return view


#: The page's and the tools' one word for each category.
LABELS: dict[str, str] = {
    "crashed_drop_pod": "drop pods",
    "hard_drive": "hard drives",
    "loot_cache": "loot caches",
    "mercer_sphere": "mercer spheres",
    "mercer_shrine": "mercer shrines",
    "mushroom": "mushrooms",
    "power_slug_blue": "blue power slugs",
    "power_slug_yellow": "yellow power slugs",
    "power_slug_purple": "purple power slugs",
    "somersloop": "somersloops",
    "somersloop_shrine": "somersloop shrines",
    "tape_pickup": "tapes",
    "customization_unlock_pickup": "customisation unlocks",
}

#: Categories that stand in plain sight, so seeing one spoils nothing.
NEVER_SPOILER = frozenset({"crashed_drop_pod", "loot_cache"})


def label(category: str) -> str:
    return LABELS.get(category) or category.replace("_", " ")


def is_spoiler(category: str, found_categories) -> bool:
    return category not in NEVER_SPOILER and category not in found_categories


def found(st, census: list[dict] | None = None) -> list[str]:
    """Categories this save has collected at least one of."""
    if census is not None:
        return sorted(r["category"] for r in census if r["collected"])
    table = st.collectibles
    if table is None:
        return []
    return sorted({table.by_key[k]["category"] for k in st.destroyed_keys if k in table.by_key})


def census_rows(st) -> list[dict]:
    """The per-category census, each row carrying its label and its spoiler flag."""
    rows = st.collectible_census()
    have = set(found(st, rows))
    return [
        {**r, "label": label(r["category"]), "spoiler": is_spoiler(r["category"], have)}
        for r in rows
    ]


_CL = re.compile(r"CL-(\d+)")


def table_age(st) -> dict | None:
    """The collectible table's age against this save; ``None`` without a table."""
    table = st.collectibles
    if table is None:
        return None
    match = _CL.search(table.build)
    cut = int(match.group(1)) if match else None
    build = st.header.get("build_version")
    behind = isinstance(build, int) and cut is not None and build > cut
    session = observed_session(table)
    name = st.header.get("session_name")
    matches = None if not session or not name else session == name
    notes = []
    if behind:
        notes.append(
            f"the pickup table predates this save's game update (build {cut} vs {build}): "
            "a placement a later update moved or added is not in it"
        )
    if matches is False:
        notes.append(
            f"seen and unseen states come from the saves of {session!r}, not of this world, "
            "so they are left out"
        )
    return {
        "table": "collectibles",
        "behind": behind,
        "gap": f"buildVersion {cut} -> {build}" if behind else None,
        "moved": 0,
        "unjoinable": 0,
        "observed_from": session or None,
        "observed_matches": matches,
        "notes": notes,
    }
