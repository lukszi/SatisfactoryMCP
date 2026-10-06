"""Save-state reporting: worlds, a world summary, unlocks, power, sites.

Everything here answers 'what does this save contain', with no planning."""

from __future__ import annotations

from collections import Counter

from .... import config
from ....core.saveio import projection as proj
from ....core.schema import NewerSchema
from ....core.text import ago, format_local_time
from ....domain import advice
from ....domain.power.report import biomass_note, starved_cause
from ....presenters.text import advice as advice_text
from ....presenters.text import primitives as render
from .. import app
from ..params import AsOf, Biomass, Limit


def _generator_rows(power: dict) -> list[tuple]:
    """One row per generator kind, biggest output first."""
    return [
        (v["name"], v["count"], render.num(v["mw"]))
        for v in sorted(power["by_generator"].values(), key=lambda v: -v["mw"])
    ]


@app.tool()
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
        reasons = Counter(u["reason"] for u in unsupported)
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


@app.tool()
def world_summary(
    save: str | None = None, world: str | None = None, as_of: AsOf = None, biomass: Biomass = None
) -> str:
    """Progress, power and problems for one world, and the advisories worth a look (adv: ids)."""
    st = app.load_world(save, world, as_of)
    g = st.game
    progression = st.progression()
    biomass, unread = app.biomass_setting(biomass)
    power = st.power_report(biomass=biomass)
    notes = [n for n in [unread, biomass_note(power)] if n]
    unbuilt = st.unlocked_but_unbuilt()
    if unbuilt:
        notes.append("unlocked but never built: " + ", ".join(g.buildings[c].name for c in unbuilt))
    if st.misconfigured:
        kinds = Counter(m["cls"] for m in st.misconfigured)
        notes.append(
            "no recipe set: "
            + ", ".join(f"{n}x {g.building_name(c) or c}" for c, n in kinds.items())
        )
    if st.paused:
        notes.append(f"{len(st.paused)} building(s) paused by the player")
    notes.extend(app.integrity_notes(st.projection, g))
    notes.extend(app.stale_artifact_notes())
    working_on = st.unlocks.last_active_schematic
    last_drive = st.harddrive_desk.last_used_hard_drive_id
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
                        ("phase", progression["game_phase"]),
                        ("target", progression["target_phase"]),
                        ("tier_complete", progression["highest_complete_tier"]),
                        ("recipes", progression["available_recipes"]),
                        ("alternates", len(st.unlocked_alternates)),
                        ("hard_drives_pending", len(st.hard_drive_offers)),
                    ]
                ),
                # Where the player left off: the HUB's active pick and the drive analysed last.
                render.kv(
                    [
                        ("working_on", working_on.name if working_on else ""),
                        ("last_hard_drive_analysed", last_drive),
                    ]
                ),
                render.kv(
                    [
                        ("power_gen_MW", render.num(power["generation_mw"])),
                        ("draw_MW", render.num(power["draw_mw"])),
                        ("headroom_MW", render.num(power["headroom_mw"])),
                    ]
                ),
                "milestones/tier: "
                + " ".join(f"T{t}:{v}" for t, v in progression["milestones_by_tier"].items()),
            ]
            if line
        ),
        render.table(("generator", "count", "MW"), _generator_rows(power)) + "\n\n" + worth,
        notes,
    )


@app.tool()
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
    st = app.load_world(save, world, as_of)
    picks = st.unlocked_alternates if only_alternates else st.unlocked_recipes("part")
    picks = sorted(picks, key=lambda r: r.name)
    window = render.page(limit, offset, default=25)
    rows = [
        (r.name, st.game.machine(r).name if st.game.machine(r) else "-") for r in window.of(picks)
    ]
    return render.envelope(
        f"# {st.age_note}\n"
        f"# {len(st.unlocked_alternates)} of {len(st.game.alternates())} alternates unlocked; "
        f"{len(st.unlocked_recipes('part'))} automatable recipes total",
        render.table(
            ("recipe", "building"),
            rows,
            total=len(picks),
            offset=window.start,
            limit=window.size,
        ),
    )


@app.tool()
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
    st = app.load_world(save, world, as_of)
    biomass, unread = app.biomass_setting(biomass)
    power = st.power_report(biomass=biomass)
    notes = [
        (
            "nameplate headroom assumes every built machine runs at once -- the SAFE "
            f"bound. Measured weights {power['monitored']} machine(s) by the last complete "
            "300s window and is what is free right now; utilisation is "
            f"{power['utilisation']:.0%}"
        ),
        (
            f"{power['unmonitored']} machine(s) carry no productivity monitor and are charged "
            "in FULL on both figures -- unknown utilisation must not read as idle"
        ),
        (
            "generation is capacity on both, because generators burn to meet demand "
            "rather than at a rate of their own"
        ),
    ]
    notes += [n for n in (unread, biomass_note(power)) if n]
    if power["unmodellable"]:
        notes.append(f"not in game data, excluded: {', '.join(power['unmodellable'])}")
    if power["unwired_consumers"] or power["unwired_generators"]:
        notes.append(
            f"on no wire, so left out of every figure above: {power['unwired_consumers']} "
            f"machine(s) rated {render.num(power['unwired_draw_mw'])} MW and "
            f"{power['unwired_generators']} generator(s) rated "
            f"{render.num(power['unwired_generation_mw'])} MW"
        )

    starved = power["starved_generators"]
    body = render.table(("generator", "count", "MW"), _generator_rows(power))
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
            f"{render.num(power['starved_generation_mw'])} MW of the generation above stands on "
            f"{len(starved)} generator(s) whose input has run dry and which produced nothing "
            "in their own window. Subtract it before planning against headroom -- that "
            "capacity is a pipe or a belt away, not a build away"
        )
    return render.envelope(
        f"# {st.age_note}\n"
        + render.kv(
            [
                ("generation_MW", render.num(power["generation_mw"])),
                ("generation_MW_starved", render.num(power["starved_generation_mw"])),
                ("draw_MW_nameplate", render.num(power["draw_mw"])),
                ("draw_MW_measured", render.num(power["measured_draw_mw"])),
                ("headroom_MW_nameplate", render.num(power["headroom_mw"])),
                ("headroom_MW_measured", render.num(power["measured_headroom_mw"])),
                ("paused", power["paused_count"]),
            ]
        ),
        body,
        notes,
    )


@app.tool()
def factory_sites(
    save: str | None = None,
    world: str | None = None,
    as_of: AsOf = None,
    limit: Limit = 10,
    offset: int = 0,
) -> str:
    """Built production buildings clustered into sites, largest first."""
    st = app.load_world(save, world, as_of)
    g = st.game
    sites = st.sites()
    window = render.page(limit, offset)
    rows = []
    for site in window.of(sites):
        top = sorted(site["buildings"].items(), key=lambda kv: -kv[1])[:4]
        x_m, y_m, z_m = (int(v / 100) for v in site["centroid"])
        rows.append(
            (
                site["direction"],
                site["grid"],
                f"{x_m},{y_m},{z_m}",
                site["count"],
                f"{site['diameter_m']}m",
                site["selector"],
                ", ".join(f"{n}x {g.building_name(c) or c}" for c, n in top),
            )
        )
    return render.envelope(
        f"# {st.age_note}\n# {len(sites)} site(s); coords in metres",
        render.table(
            ("dir", "grid", "x,y,z(m)", "buildings", "spread", "selector", "contents"),
            rows,
            total=len(sites),
            offset=window.start,
            limit=window.size,
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
