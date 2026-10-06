"""Save-state reporting: worlds, a world summary, unlocks, power, sites.

Everything here answers 'what does this save contain', with no planning."""

from __future__ import annotations

from .... import config
from ....core.saveio import projection as proj
from ....core.schema import NewerSchema
from ....core.text import ago, format_local_time
from ....domain import advice
from ....domain.power.report import biomass_note, starved_cause
from ....presenters.text import advice as advice_text
from ....presenters.text import primitives as render
from ..app import (
    AsOf,
    Biomass,
    Limit,
    _state,
    integrity_notes,
    mcp,
    shared,
    stale_artifact_notes,
)


@mcp.tool(structured_output=False)
def list_worlds() -> str:
    """List save games grouped by world, newest first.

    Unsupported files are reported separately rather than failing the scan --
    pre-1.0 saves cannot be parsed at all.
    """
    try:
        worlds, unsupported = proj.list_worlds()
    except Exception as exc:
        return f"could not scan saves: {exc}"
    if not worlds and not unsupported:
        return f"no saves found under {config.saves_root()}"
    rows = []
    autosave_newest = False
    for w in worlds:
        newest = w.newest
        autosave_newest = autosave_newest or "autosave" in newest["filename"].lower()
        rows.append(
            (
                w.session_name,
                len(w.saves),
                f"{w.max_play_duration_s / 3600:.0f}h",
                newest["filename"],
                f"{format_local_time(newest.get('mtime_ns'))} ({ago(newest.get('mtime_ns'))})",
                newest["save_version"],
                w.world_id,
            )
        )
    notes = []
    if autosave_newest:
        notes.append(
            "a newest file above is an autosave -- the game writes autosaves "
            "periodically, so disk may lag the live world"
        )
    if unsupported:
        reasons: dict[str, int] = {}
        for u in unsupported:
            reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
        notes.append(
            f"{len(unsupported)} file(s) unreadable: "
            + "; ".join(f"{n}x {r}" for r, n in reasons.items())
        )
    return render.envelope(
        f"# {len(worlds)} world(s), {sum(len(w.saves) for w in worlds)} readable save(s)",
        render.table(
            ("world", "saves", "played", "newest", "written", "saveVer", "world_id"), rows
        ),
        notes,
    )


@mcp.tool(structured_output=False)
def world_summary(
    save: str | None = None, world: str | None = None, as_of: AsOf = None, biomass: Biomass = None
) -> str:
    """Progress, power and problems for one world, and the advisories worth a look (adv: ids)."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    p = st.progression()
    unread = ""
    if biomass is None:
        biomass, unread = shared("biomass")
    pw = st.power_report(biomass=biomass)
    notes = [n for n in [unread, biomass_note(pw)] if n]
    unbuilt = st.unlocked_but_unbuilt()
    if unbuilt:
        notes.append("unlocked but never built: " + ", ".join(g.buildings[c].name for c in unbuilt))
    if st.misconfigured:
        kinds: dict[str, int] = {}
        for m in st.misconfigured:
            kinds[m["cls"]] = kinds.get(m["cls"], 0) + 1
        notes.append(
            "no recipe set: "
            + ", ".join(
                f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in kinds.items()
            )
        )
    if st.paused:
        notes.append(f"{len(st.paused)} building(s) paused by the player")
    notes.extend(integrity_notes(st.projection, g))
    notes.extend(stale_artifact_notes())
    working_on = st.unlocks.last_active_schematic
    last_drive = st.harddrive_desk.last_used_hard_drive_id
    gen_rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    try:
        worth = advice_text.summary_block(advice.current(st, biomass=biomass))
    except NewerSchema as exc:
        worth = f"## worth a look\nunreadable: hidden advisories are schema {exc.found}"
    return render.envelope(
        "\n".join(
            line
            for line in [
                f"# {st.age_note}",
                render.kv(
                    [
                        ("phase", p["game_phase"]),
                        ("target", p["target_phase"]),
                        ("tier_complete", p["highest_complete_tier"]),
                        ("recipes", p["available_recipes"]),
                        ("alternates", len(st.unlocked_alternates)),
                        ("hard_drives_pending", len(st.hard_drive_offers)),
                    ]
                ),
                # Where the player left off, which is the one thing a resuming assistant
                # cannot work out from counts: the HUB's own active pick, and the drive
                # analysed last.
                render.kv(
                    [
                        ("working_on", working_on.name if working_on else ""),
                        ("last_hard_drive_analysed", last_drive),
                    ]
                ),
                render.kv(
                    [
                        ("power_gen_MW", render.num(pw["generation_mw"])),
                        ("draw_MW", render.num(pw["draw_mw"])),
                        ("headroom_MW", render.num(pw["headroom_mw"])),
                    ]
                ),
                "milestones/tier: "
                + " ".join(f"T{t}:{v}" for t, v in p["milestones_by_tier"].items()),
            ]
            if line
        ),
        render.table(("generator", "count", "MW"), gen_rows) + "\n\n" + worth,
        notes,
    )


@mcp.tool(structured_output=False)
def unlocked_recipes(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    only_alternates: bool = True,
    limit: Limit = 25,
    offset: int = 0,
) -> str:
    """Which recipes this world has. Defaults to alternates, never all 872.

    Sorted by name and paged with `offset=`, so the whole list is reachable."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    picks = sorted(picks, key=lambda r: r.name)
    start = max(0, offset)
    n = render.clamp(limit, default=25)
    page = picks[start : start + n]
    rows = [(r.name, st.game.machine(r).name if st.game.machine(r) else "-") for r in page]
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(st.unlocked_alternates)} of {len(st.game.alternates())} alternates unlocked; "
        f"{len(st.unlocked_recipes('part'))} automatable recipes total",
        render.table(("recipe", "building"), rows, total=len(picks), offset=start, limit=n),
    )


@mcp.tool(structured_output=False)
def power_report(
    save: str | None = None, world: str | None = None, as_of: AsOf = None, biomass: Biomass = None
) -> str:
    """Generation capacity vs machine draw, nameplate AND measured.

    Nameplate is what everything built would draw running at once. Measured weights each
    machine by the 300 s productivity monitor the save already carries, which on a factory
    with idle blocks is a very different number -- and it is the one that says what is free
    right now. Both are shown because they answer different questions.

    Generation is capacity on both figures, with one exception the answer names: a generator
    whose fuel or supplemental water has run dry AND whose own monitor read zero is listed as
    starved, because those MW will not arrive when the grid asks for them.
    """
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    unread = ""
    if biomass is None:
        biomass, unread = shared("biomass")
    pw = st.power_report(biomass=biomass)
    rows = [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(pw["by_generator"].values(), key=lambda v: -v["mw"])
    ]
    # This note used to read "nameplate only: fuel supply and uptime are not modelled".
    # The uptime was in the projection the whole time, on 524 of 570 records.
    notes = [
        (
            "nameplate headroom assumes every built machine runs at once -- the SAFE "
            f"bound. Measured weights {pw['monitored']} machine(s) by the last complete "
            "300s window and is what is free right now; utilisation is "
            f"{pw['utilisation']:.0%}"
        ),
        (
            f"{pw['unmonitored']} machine(s) carry no productivity monitor and are charged "
            "in FULL on both figures -- unknown utilisation must not read as idle"
        ),
        (
            "generation is capacity on both, because generators burn to meet demand "
            "rather than at a rate of their own"
        ),
    ]
    notes += [n for n in (unread, biomass_note(pw)) if n]
    if pw["unmodellable"]:
        notes.append(f"not in game data, excluded: {', '.join(pw['unmodellable'])}")
    if pw["unwired_consumers"] or pw["unwired_generators"]:
        notes.append(
            f"on no wire, so left out of every figure above: {pw['unwired_consumers']} "
            f"machine(s) rated {render.num(pw['unwired_draw_mw'])} MW and "
            f"{pw['unwired_generators']} generator(s) rated "
            f"{render.num(pw['unwired_generation_mw'])} MW"
        )

    starved = pw["starved_generators"]
    body = render.table(("generator", "count", "MW"), rows)
    if starved:
        body += "\n\n## starved generators\n" + render.table(
            ("generator", "building", "MW", "why"),
            [
                (s["instance"], s["name"], render.num(s["mw"]), starved_cause(s["missing"]))
                for s in starved
            ],
            total=len(starved),
        )
        notes.append(
            f"{render.num(pw['starved_generation_mw'])} MW of the generation above stands on "
            f"{len(starved)} generator(s) whose input has run dry and which produced nothing "
            "in their own window. Subtract it before planning against headroom -- that "
            "capacity is a pipe or a belt away, not a build away"
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("generation_MW", render.num(pw["generation_mw"])),
                ("generation_MW_starved", render.num(pw["starved_generation_mw"])),
                ("draw_MW_nameplate", render.num(pw["draw_mw"])),
                ("draw_MW_measured", render.num(pw["measured_draw_mw"])),
                ("headroom_MW_nameplate", render.num(pw["headroom_mw"])),
                ("headroom_MW_measured", render.num(pw["measured_headroom_mw"])),
                ("paused", pw["paused_count"]),
            ]
        ),
        body,
        notes,
    )


@mcp.tool(structured_output=False)
def factory_sites(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """Built production buildings clustered into sites, largest first."""
    try:
        st = _state(save, world, as_of)
    except Exception as exc:
        return f"could not read save: {exc}"
    g = st.game
    sites = st.sites()
    rows = []
    start = max(0, offset)
    n = render.clamp(limit)
    for s in sites[start : start + n]:
        top = sorted(s["buildings"].items(), key=lambda kv: -kv[1])[:4]
        x_m, y_m, z_m = (int(v / 100) for v in s["centroid"])
        rows.append(
            (
                s["direction"],
                s["grid"],
                f"{x_m},{y_m},{z_m}",
                s["count"],
                f"{s['diameter_m']}m",
                s["selector"],
                ", ".join(f"{n}x {g.buildings[c].name if c in g.buildings else c}" for c, n in top),
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(sites)} site(s); coords in metres",
        render.table(
            ("dir", "grid", "x,y,z(m)", "buildings", "spread", "selector", "contents"),
            rows,
            total=len(sites),
            offset=start,
            limit=n,
        ),
        [
            (
                "the selector is a circle round the centroid, and is what plan_factory, "
                "factory_query and name_factory take as sources=. A circle is not a cluster: "
                "a sprawling site can leave a straggler outside it, so compare the machine "
                "count the other tool reports against 'buildings' here and widen the @radius "
                "if it comes back short"
            ),
            (
                "z is the centroid's altitude, the mean of the members' -- a site on two "
                "levels has no single one"
            ),
        ],
    )
