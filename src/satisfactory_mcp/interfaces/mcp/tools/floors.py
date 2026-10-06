"""Storeys: the text twin of ``/api/floors``.

``domain.factories.floors`` recovers real floors from foundation geometry and was reachable
from the web map only. This parses the query, calls it once, and renders; every threshold
and every claim about what a floor is belongs to that module.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ....domain.factories import floors as ffloors
from ....domain.spatial import heightfield
from ....presenters.text import primitives as render
from .. import app
from ..params import AsOf, Limit

#: How many building kinds a band names before the rest become "+N more".
DECK_KINDS = 3


def _classes(st) -> dict[str, str]:
    """Instance leaf -> building class, for the ids a band lists its machines by.

    Read off the projection rather than off ``report.placements``: a narrowed report keeps
    the placements whose own 8 m cell is on the platform, while a band lists what was
    ASSIGNED to its deck, and a machine on the very edge can be one and not the other.
    """
    out: dict[str, str] = {}
    for key in ("machines", "extractors", "generators"):
        for record in st.projection.get(key) or ():
            if isinstance(record, dict):
                out[str(record.get("instance", "")).rsplit(".", 1)[-1]] = record.get("cls") or ""
    return out


def _what_stands(st, classes: dict[str, str], instances: list[str]) -> str:
    """What those machines are, biggest kind first."""
    counts: dict[str, int] = {}
    for leaf in instances:
        cls = classes.get(leaf, "")
        name = st.game.building_name(cls) or cls or "?"
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    shown = [f"{n}x {name}" for name, n in ranked[:DECK_KINDS]]
    extra = len(ranked) - len(shown)
    return ", ".join(shown + ([f"+{extra} more"] if extra else [])) or "-"


def _band_rows(st, classes: dict[str, str], platform: ffloors.Platform) -> list[tuple]:
    rows = []
    for band in platform.bands:
        rows.append(
            (
                band.ordinal,
                f"{band.top_cm / 100:.1f}",
                render.num(band.area_m2),
                band.cells,
                "minor" if band.minor else "",
                len(band.machines),
                len(band.attachments),
                _what_stands(st, classes, band.machines),
            )
        )
    return rows


@app.tool()
def factory_floors(
    factory: Annotated[
        str | None, Field(description="a named factory, or any machine selector")
    ] = None,
    platform: Annotated[
        int | None, Field(description="one platform by the index this tool hands out")
    ] = None,
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """How many decks a factory has and what stands on each.

    Nothing in the save says "floor". Floors are recovered from geometry: a platform is a
    4-connected run of 8 m foundation cells, and its storeys are the levels its tops
    cluster at, with no assumed storey pitch. A band holding less than a quarter of the
    platform's largest deck is marked ``minor`` -- a mezzanine or a machine plinth,
    reported rather than merged away.

    Whole-world by default: one row per platform, largest first. Narrow with ``platform=``
    (an index that is stable across calls on one save) or ``factory=``, and a single
    platform is answered floor by floor instead.
    """
    st = app.load_world(save, world, as_of)

    report = ffloors.floor_decomposition(
        st, platform=platform, label=factory, terrain_field=heightfield.load_field()
    )
    if not report.platforms:
        return f"# {st.age_note}\n! {report.note or 'no platform matches'}"

    counts = report.counts()
    # Machines only: ``counts["placements"]`` also holds the belt and pipe attachments, and
    # "1,263 machines on a deck" over a world with 441 of them is a wrong sentence. What is
    # ON a deck is counted off the bands, so this line and the table agree; the other three
    # groups have no deck to be counted from.
    standing = {
        group: sum(1 for p in report.group(group) if p.kind != "attachments")
        for group in ffloors.GROUPS
        if group != "band"
    }
    standing["band"] = sum(len(b.machines) for p in report.platforms for b in p.bands)
    notes = [
        (
            "a platform is foundation, not a factory: two factories poured onto one "
            "continuous slab are one platform, and a walkway bridging two buildings joins "
            "them"
        ),
        (
            "factory_map show=slabs counts storeys as the z span divided by 4 m over a slab "
            "welded through ramps -- a different measurement, and the two disagree wherever "
            "a ramp climbs. These bands are the measured decomposition"
        ),
    ]
    if not report.terrain_measured:
        notes.append(
            "no heightfield on this machine, so nothing can be called 'on terrain' -- "
            f"{standing['off-deck']} machine(s) on no deck are reported as "
            "off-deck, which is the weaker claim"
        )

    header = (
        f"# {st.age_note}\n"
        f"# {counts['platforms']} platform(s), {counts['bands']} floor(s); "
        f"{standing['band']} machine(s) on a deck, "
        f"{standing['terrain']} on terrain, "
        f"{standing['off-deck']} off-deck, "
        f"{standing['exempt']} on a resource node or on water"
    )
    if factory is not None and report.selection:
        header += f"\n# {report.selection}"

    if len(report.platforms) == 1:
        one = report.platforms[0]
        cx, cy = one.centre_cm
        connectors = report.connectors
        header += (
            f"\n# platform {one.index}"
            + (f" ({one.label})" if one.label else "")
            + f": {render.num(one.area_m2)}m2 over {one.cells} tile(s), centre "
            f"{cx / 100:.0f},{cy / 100:.0f}, {len(one.bands)} floor(s); "
            f"{len(connectors)} belt/pipe run(s) leave a floor, "
            f"{sum(1 for r in connectors if r.riser)} of them climbing 6m or more"
        )
        if one.bands:
            notes.append(
                f"{one.clean:.1%} of this platform's foundation pieces sit within 25cm of "
                "one of these bands -- the premise of the decomposition, measured here"
            )
        else:
            notes.append(
                "no floor was detected here: no level of this pour carries the three "
                "foundation pieces a band is made of, which is what a helper pad looks like"
            )
        rows = _band_rows(st, _classes(st), one)
        start = max(0, offset)
        return render.envelope(
            header,
            render.table(
                ("floor", "top(m)", "m2", "tiles", "note", "machines", "belt_ends", "what"),
                rows[start : start + render.clamp(limit)],
                total=len(rows),
                offset=start,
                limit=limit,
            ),
            notes,
        )

    # A pour with no band at all is a helper pad -- a tile under a power pole, a jump-pad
    # landing. Left out of the rows and counted in a note, never silently dropped.
    pads = [p for p in report.platforms if not p.bands]
    ordered = sorted((p for p in report.platforms if p.bands), key=lambda p: -p.cells)
    if pads:
        notes.append(
            f"{len(pads)} pour(s) carry no floor at all -- no level of theirs holds three "
            f"foundation pieces, and the largest is {max(p.cells for p in pads)} tile(s). "
            "Not listed"
        )
    start = max(0, offset)
    rows = []
    for one in ordered[start : start + render.clamp(limit)]:
        cx, cy = one.centre_cm
        tops = [b.top_cm / 100 for b in one.bands]
        rows.append(
            (
                one.index,
                one.label or "-",
                len(one.bands),
                sum(1 for b in one.bands if b.minor),
                render.num(one.area_m2),
                f"{cx / 100:.0f},{cy / 100:.0f}",
                f"{min(tops):.0f}..{max(tops):.0f}" if tops else "-",
                sum(len(b.machines) for b in one.bands),
            )
        )
    notes.append("factory_floors(platform=<index>) lists one platform floor by floor")
    return render.envelope(
        header,
        render.table(
            ("platform", "factory", "floors", "minor", "m2", "centre(m)", "tops(m)", "machines"),
            rows,
            total=len(ordered),
            offset=start,
            limit=limit,
        ),
        notes,
    )
