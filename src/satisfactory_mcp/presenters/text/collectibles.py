"""Map collectibles as text: the per-category census, and the per-placement listings.

Which placements answer the question is ``collectibles.service``'s decision; this module only
says it, in three shapes -- a census is a tally with its caveats, a listing is coordinates
with their hazards, and the degraded save-only answer is a name-prefix guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from ...core.collectible_rows import HazardContext
from ...core.gamedata.model import GameData
from ...domain.collectibles.service import GENERATOR_COMMAND, CollectiblesView, census_rows
from ...domain.collectibles.table import CollectibleTable
from ...domain.collectibles.views import CensusRow, CollectedSummary, Placement
from ...domain.spatial import geo, maplink
from ...domain.world.state import WorldState
from . import primitives as render

__all__ = ["render_collectibles"]


def _table(view: CollectiblesView) -> CollectibleTable:
    """The map table of a view that is not the save-only census."""
    assert view.table is not None, "no map table"
    return view.table


def _scope(view: CollectiblesView) -> tuple[list[str], list[tuple[float, float]]]:
    """The categories this answer is about, and every placement of theirs in centimetres.

    A listing answers for the rows it printed; a census, and a listing that printed nothing,
    answer for every category, whose placements have to be read back off the table. Pedestals
    drop out of an unfiltered answer on the same grounds the listing drops them: a shrine
    layer would draw a second marker a metre from the sphere.
    """
    table = _table(view)
    if view.group:
        cats = [view.group]
    elif view.rows:
        cats = sorted({r["category"] for r in view.rows})
    else:
        cats = [c for c in sorted(table.by_category) if not table.pedestal_of(c)]
    if view.rows:
        return cats, [(r["pos"][0], r["pos"][1]) for r in view.rows]
    return cats, [(r["x"], r["y"]) for c in cats for r in table.by_category.get(c, ())]


def _map_links(st: WorldState, view: CollectiblesView) -> tuple[str, list[str]]:
    """Both map links for the placements this answer is about, the local one first.

    Local first and unconditional because it is the only one of the two that knows what THIS
    save has taken: it draws ``/api/collectibles?mode=remaining``, so its layer is what is
    left. The public map draws the vanilla world's full placement list and cannot subtract a
    save from it, which is why the note beside the link says so rather than the link being
    quietly dropped.
    """
    cats, points = _scope(view)
    if view.origin is not None:
        centre, zoom = view.origin, 0.0
    elif points:
        centre = (
            sum(p[0] for p in points) / len(points),
            sum(p[1] for p in points) / len(points),
        )
        zoom = float(maplink.LOCAL_WORLD_ZOOM)
    else:
        centre, zoom = (0.0, 0.0), float(maplink.LOCAL_WORLD_ZOOM)

    local = maplink.local_map_url(
        centre[0] / 100, centre[1] / 100, zoom=zoom, world=st.world_id, pickups=cats
    )
    tokens = maplink.collectible_layers(cats)
    body = f"local map: {local}\npublic map: {maplink.calculator_map_url(*centre, tokens)}"
    if tokens:
        body += "\n# public layers: " + ", ".join(tokens)

    notes = [
        "the local map is this project's own and switches the pickup layer on for "
        + ", ".join(cats)
        + ". It draws what is REMAINING in YOUR save, so a placement you already took is "
        "not on it; the public map is satisfactory-calculator.com and draws the vanilla "
        "world's full list, including everything you have collected"
    ]
    if not tokens:
        notes.append(
            "the public map has no collectible layer for "
            + ", ".join(cats)
            + ", so its link is centred and carries no overlay"
        )
    if view.mode == "collected":
        notes.append(
            "the rows below are gone and the local map's layer is what is LEFT, so none of "
            "them is drawn on it. It answers the next question, not this one"
        )
    if "crashed_drop_pod" in cats:
        notes.append(
            "a looted pod stays standing, so a pod on either map is not a hard drive. The "
            "local map tells them apart -- a looted one is a hollow ring, one no save has "
            "had loaded is faint, and only a solid dot still holds a drive; the public map "
            "draws all three alike. show='remaining' group='crashed_drop_pod' has a holds "
            "column, and LOOTED there means the drive is already yours"
        )
    return body, notes


def _hazard_tokens(hazard: HazardContext) -> str:
    """The hazard block as a few tokens, distances in metres."""
    out: list[str] = []
    if hostiles := hazard.get("hostiles_nearby"):
        nearest = hazard.get("nearest_hostile_cm") or 0
        out.append(f"hostiles{sum(hostiles.values())}@{nearest / 100:.0f}m")
    if hazard.get("spawns_here"):
        out.append("spawner")
    if hazard.get("inside_spore_flower_damage_sphere"):
        out.append("spore")
    elif gas := hazard.get("nearest_gas_cm"):
        out.append(f"gas@{gas / 100:.0f}m")
    if uranium := hazard.get("nearest_uranium_cm"):
        out.append(f"uranium@{uranium / 100:.0f}m")
    if hazard.get("nearest_nuclear_hog_spawner_cm"):
        out.append("nuclear-hog")
    return " ".join(out)


def _holds(row: Placement, g: GameData) -> str:
    """What is in this one, where the map records it.

    A looted drop pod is reported as LOOTED and nothing else: its ``mUnlockCost`` is still on
    the actor after it has given up its hard drive, so quoting the price would offer a player
    something already taken.
    """
    contents = row["contents"]
    if contents is not None and (item := contents["item"]):
        return f"{contents.get('count', 0):g} {g.item_name(item)}"
    if row.get("looted"):
        return "LOOTED"
    cost = row["unlock_cost"]
    if cost is not None and (wanted := cost.get("item")):
        return f"wants {cost.get('amount', 0):g} {g.item_name(wanted)}"
    #: An unlooted pod that does not serialise mUnlockCost holds its class default, which
    #: the map cannot read. Still worth saying it is unlooted.
    return "unlooted, cost unknown" if row.get("looted") is False else ""


def _metres_away(row: Placement) -> str:
    """A nearest row's distance: the service measures every row of that mode."""
    assert "distance_m" in row, "a nearest row without its distance"
    return f"{row['distance_m']:.0f}m"


def _placement_table(
    rows: Sequence[Placement], g: GameData, distance: bool, total: int, limit: int, offset: int
) -> str:
    """One row per placement, with the empty optional columns dropped.

    Names are never truncated: ``(cell, name)`` is the only identity a placement has, and
    half a key joins to nothing.
    """
    hazard = [_hazard_tokens(r["hazard"]) for r in rows]
    holds = [_holds(r, g) for r in rows]
    optional = [("hazard", hazard), ("holds", holds)]
    shown = [(head, values) for head, values in optional if any(values)]
    body = [
        (
            r["category"],
            r["name"],
            *((_metres_away(r),) if distance else ()),
            r["observed"] or ("collected" if r["collected"] else "-"),
            geo.grid_cell(r["pos"][0], r["pos"][1]),
            f"{int(r['pos'][0] / 100)},{int(r['pos'][1] / 100)}",
            f"{r['pos'][2] / 100:.0f}",
            *(values[i] for _head, values in shown),
        )
        for i, r in enumerate(rows)
    ]
    headers = (
        "category",
        "name",
        *(("dist",) if distance else ()),
        "observed",
        "grid",
        "x,y",
        "z",
        *(head for head, _values in shown),
    )
    return render.table(headers, body, total=total, offset=offset, limit=limit)


#: Census columns beyond the four every save has, rendered only when some row is non-zero:
#: ``gone_later`` needs an older save than the newest on disk, and ``unstated`` needs a table
#: newer than this code. Neither is dropped from the arithmetic when it is hidden.
_CONDITIONAL_COLUMNS: tuple[tuple[Literal["gone_in_a_later_save", "unstated"], str], ...] = (
    ("gone_in_a_later_save", "gone_later"),
    ("unstated", "unstated"),
)


#: The first save version whose destroyed actors are keyed by world-partition cell; older
#: ones key them through Persistent_Level, where the map places almost nothing.
_FIRST_WORLD_PARTITION_SAVE = 52


def _predates_world_partition(st: WorldState) -> bool:
    version = st.header.get("save_version")
    return bool(version) and version < _FIRST_WORLD_PARTITION_SAVE


def _census_notes(
    st: WorldState,
    census: Sequence[CensusRow],
    removed: CollectedSummary,
    table: CollectibleTable,
    group: str | None,
) -> list[str]:
    """How to read the census: what each column is, and the rows it cannot speak for."""
    notes = [
        (
            "placed is the MAP's own count and collected is THIS save's own destroyed list; "
            "both are exact, and remaining is their subtraction. Wherever remaining is a "
            "number, standing + never_streamed (+ any further column) adds up to it"
        ),
        (
            "never_streamed is a placement in a cell no save on disk has ever loaded. It "
            "counts as remaining because nothing collected it, and it is NOT present: the "
            "map says where it is and nothing says whether it is still there"
        ),
    ]
    untracked = [r for r in census if not r["state_tracked"]]
    if untracked:
        notes.append(
            "remaining is withheld (-) for "
            + ", ".join(f"{r['category']} ({r['cls']}, {r['placed']} placed)" for r in untracked)
            + ": no save on disk mentions that class at all, live or gone, so taking one "
            "would leave nothing to read and placed-minus-collected would be a fabrication"
        )
    pedestals = [r for r in census if r["pedestal_of"]]
    if pedestals:
        notes.append(
            "never add categories together: "
            + "; ".join(
                f"{r['category']} is the base {r['pedestal_of']} stands on, paired 1:1 by the "
                f"map's own AttachParent, so the two are {r['placed']} finds and not "
                f"{r['placed'] * 2}"
                for r in pedestals
            )
        )
    pods = next((r for r in census if r["looted_and_standing"]), None)
    if pods:
        notes.append(
            f"{pods['category']}: {pods['looted_and_standing']} of the {pods['standing']} "
            "standing ones are already LOOTED. A pod stays in the world after it is emptied "
            "-- only a dismantled one is destroyed -- so its remaining is not a count of "
            f"hard drives left. Use show='remaining' group='{pods['category']}' to see which"
        )
    if unresolved := removed.get("unresolved", 0):
        notes.append(
            f"{unresolved} of the {removed['total']} destroyed records join no "
            "placement: either a class the map table excludes on purpose (crash-site "
            "scenery, regrowing berry and nut bushes, resource nodes) or an actor the map "
            "never placed -- which is what a pickup the PLAYER dropped is, and it shares its "
            "native class with the loot caches"
        )
    if _predates_world_partition(st):
        notes.append(
            "this save predates world partition, so its destroyed actors are keyed through "
            "Persistent_Level and the map places only 32 rows there: nearly everything will "
            "read as unresolved. Load a newer save of the same world for a real census"
        )
    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")
    return notes


def _unresolved_stems_table(
    removed: CollectedSummary, table: CollectibleTable, window: render.Page
) -> str:
    """The destroyed records that join no placement, by name stem, with why."""
    unjoined = removed.get("unresolved_stems", {})
    stems = [(k, v, table.excluded_reason(k) or "") for k, v in unjoined.items()]
    return render.table(
        ("name_stem", "destroyed", "why the map table has no row for it"),
        [(k, v, why[:96]) for k, v, why in window.of(stems)],
        total=len(stems),
        offset=window.start,
        limit=window.size,
    )


def _census(st: WorldState, view: CollectiblesView, limit: int, offset: int) -> str:
    """The per-category table: placed, collected, remaining, and how much is observed.

    ``group`` narrows the table to one category and takes its notes with it; the summary line
    stays whole-world.
    """
    removed, table, group = view.removed, _table(view), view.group
    census = [r for r in census_rows(st) if group is None or r["category"] == group]
    extra = [(key, head) for key, head in _CONDITIONAL_COLUMNS if any(r[key] for r in census)]
    rows: list[tuple[object, ...]] = [
        (
            row["category"],
            row["placed"],
            row["collected"],
            "-" if row["remaining"] is None else row["remaining"],
            row["standing"],
            row["never_streamed"],
            *(row[key] for key, _head in extra),
        )
        for row in census
    ]

    notes = _census_notes(st, census, removed, table, group)
    links, link_notes = _map_links(st, view)
    notes += link_notes
    body = [
        links,
        render.table(
            (
                "category",
                "placed",
                "collected",
                "remaining",
                "standing",
                "never_streamed",
                *(head for _key, head in extra),
            ),
            rows,
        ),
    ]
    if group is None:
        body.append(_unresolved_stems_table(removed, table, render.page(limit, offset, default=25)))
    body.append(render.ids_footer([(r["category"], r["cls"]) for r in census], "classes"))
    return render.envelope(
        f"# {st.age_note}\n"
        f"# map table: {len(table)} placements, {table.build}\n"
        # Whole-world because they do not narrow with `group`: a scoped table under an
        # unscoped total is how one gets read as the other.
        + render.kv(
            [
                ("whole_world_collected", removed.get("resolved", 0)),
                ("destroyed_records", removed["total"]),
                ("unresolved", removed.get("unresolved", 0)),
                ("save_cells", removed["cells"]),
                ("showing", group or "every category"),
            ]
        ),
        "\n\n".join(b for b in body if b),
        notes,
    )


def _listing(st: WorldState, view: CollectiblesView, limit: int, offset: int) -> str:
    """Individual placements: collected, remaining, or remaining by distance."""
    g = st.game
    table, group, mode = _table(view), view.group, view.mode
    rows, origin, where = view.rows or [], view.origin, view.where
    pedestals, hidden, counts = view.pedestals, view.hidden, view.counts

    window = render.page(limit, offset, default=25)
    page = window.of(rows)

    notes: list[str] = []
    if mode == "collected":
        notes.append(
            "these are gone -- the coordinates say where they WERE. The map is the only "
            "source of a position here; a destroyed record carries none"
        )
    else:
        notes.append(
            "never_streamed rows are placements no save has ever loaded: the position is the "
            "map's and is exact, the state is unobserved. They are still remaining"
        )
    if origin is not None:
        notes.append(
            f"distance is planar metres from {where}, straight-line and not a walk: nothing "
            "here knows about cliffs, and a slug 80 m away can be 80 m up"
        )
    if any(r["hazard"] for r in page):
        notes.append(
            "the hazard column is INFERENCE, not placement: geometry between this placement "
            "and other map actors, plus the radii those actors' own classes declare. Gas is "
            "presence-only -- the volume's shape is level geometry and is in no file read here"
        )
    if group and (note := table.note_for(group)):
        notes.append(f"{group}: {note}")
    if group is None:
        notes.append(
            "every category at once. Pass group= to narrow it -- the names are in show='census'"
        )
    if hidden:
        notes.append(
            f"{hidden} {'/'.join(pedestals)} row(s) are not shown: a shrine is the base its "
            "artifact stands on, 1:1 by the map's own AttachParent, so listing both would put "
            "two rows a metre apart for one find. Ask for it by group to see them"
        )

    links, link_notes = _map_links(st, view)
    notes += link_notes
    return render.envelope(
        f"# {st.age_note}\n"
        f"# show={mode}"
        + (f" group={group}" if group else " all categories")
        + (f" from {where}" if origin else "")
        + "\n"
        + render.kv([("rows", len(rows)), *sorted(counts.items())]),
        links
        + "\n\n"
        + _placement_table(
            page, g, origin is not None, total=len(rows), limit=window.size, offset=window.start
        ),
        notes,
    )


def _save_only(st: WorldState, view: CollectiblesView, limit: int, offset: int) -> str:
    """The census a save can build alone: collected counts by name prefix, and wrong.

    Reached only when ``data/world_collectibles.json`` is absent, which a fresh clone is,
    since the file is untracked.
    """
    removed, group = view.removed, view.group
    notes = [
        (
            "DEGRADED: data/world_collectibles.json has never been generated, so there is "
            "no map table to join against. Groups below come from a name-prefix rule over "
            "the destroyed actors' instance names, which is measurably wrong -- on the "
            "reference save it misfiles 51 of 713 (40 yellow slugs read as blue) and leaves "
            f"65 undecidable. Generate the table with {GENERATOR_COMMAND}"
        ),
        (
            "collected, not remaining: without the map table nothing here knows how many of "
            "anything exists, so these are absolute counts and not a fraction of a total. "
            "show=remaining and show=nearest need the table and are unavailable"
        ),
        (
            "'artifact_unsplit' is names of the shape BP_WAT<n>, where the placement counter "
            "is glued onto a stem that is either BP_WAT1 (somersloop) or BP_WAT2 (Mercer "
            "sphere). Splitting them by name would be invention; the map table splits them"
        ),
        "'dropped_pickup' is loot the player dropped and re-collected, not a map collectible",
        (
            "no map link either: the web map's pickup layers are drawn from that same table, "
            "so a link would open a layer that is empty because nothing was generated -- "
            "which on a map reads as 'you have taken them all'"
        ),
    ]
    if other := removed.get("other"):
        notes.append(
            "'other' is classes no prefix matched, reported rather than dropped: "
            + ", ".join(f"{k} {v}" for k, v in list(other.items())[:6])
        )
    if not removed["total"]:
        notes.append(
            "nothing recorded as removed. On a projection older than schema 11 that means "
            "unreadable rather than none -- re-read the save"
        )

    body = [
        render.table(
            ("group", "collected"), [(k, str(v)) for k, v in removed.get("groups", {}).items()]
        )
    ]
    if group is not None:
        window = render.page(limit, offset, default=25)
        actors = removed.get("actors", [])
        rows = [(a["name"], a["cell"]) for a in window.of(actors)]
        body.append(
            render.table(
                ("actor", "cell"),
                rows,
                total=len(actors),
                offset=window.start,
                limit=window.size,
            )
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv([("total_removed", removed["total"]), ("map_cells", removed["cells"])]),
        "\n\n".join(body),
        notes,
    )


def render_collectibles(st: WorldState, view: CollectiblesView, limit: int, offset: int = 0) -> str:
    """The one entry point: a refusal, the degraded census, the census, or a listing."""
    if view.error:
        return view.error
    if view.save_only:
        return _save_only(st, view, limit, offset)
    if view.mode == "census":
        return _census(st, view, limit, offset)
    return _listing(st, view, limit, offset)
