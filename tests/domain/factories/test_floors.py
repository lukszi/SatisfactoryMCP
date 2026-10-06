"""The floor decomposition, and the stage-0 physics it is built on.

Every number asserted here was measured before the module existed -- against the reference
world's projection, which is also this suite's committed fixture, so the assertions are
against the same 5,080 foundation pieces the measurement was taken over and need no game
install beyond the docs dump the ``state`` fixture already wants.

The tests are deliberately of two kinds. The **physics** tests pin claims that would still
be true of somebody else's save -- the top-surface arithmetic, the two offsets, the two
exemptions, chain grouping, the riser rule -- and a synthetic world is built for the ones
where the reference world happens to have no counterexample. The **fixture** tests pin this
world's own answer, which is what makes a silent drift in the decomposition visible.
"""

from __future__ import annotations

from collections import Counter
from itertools import pairwise

import pytest

from satisfactory_mcp.domain.factories import floors
from satisfactory_mcp.domain.factories.floors import floor_decomposition
from satisfactory_mcp.domain.world.state import WorldState

# ------------------------------------------------------------------ synthetic worlds


def _tile(class_index: int, cell_x: int, cell_y: int, z_cm: float) -> list:
    """One lightweight row: ``[class, x, y, z, yaw]`` at the centre of an 8 m cell."""
    return [class_index, cell_x * 800.0 + 400.0, cell_y * 800.0 + 400.0, z_cm, 0.0]


def _deck(cells: list[tuple[int, int]], z_cm: float) -> list[list]:
    """A run of tiles of one class at one height -- one deck of a synthetic platform."""
    return [_tile(0, cx, cy, z_cm) for cx, cy in cells]


def _world(game=None, **payload) -> WorldState:
    """A ``WorldState`` over a hand-built projection. No save, no sidecar, no fixture."""
    return WorldState(projection=dict(payload), game=game)


class _FlatField:
    """A terrain field that is one height everywhere. Only ``at`` is ever read."""

    def __init__(self, z_m: float) -> None:
        self._z = z_m

    def at(self, x: float, y: float):
        del x, y
        return type("Reading", (), {"z_m": self._z})()


# ------------------------------------------------------------------ the arithmetic


def test_a_lightweights_z_is_its_centre_so_a_deck_is_half_a_thickness_up():
    """The first stage-0 correction, and the one every later number rides on.

    A foundation's stored Z is the piece's vertical CENTRE, not its walking surface, so the
    deck is ``z + thickness/2`` and the thickness is read out of the class name. Read as the
    surface instead, every band lands half a metre low and every machine standing on it
    looks half a metre airborne.
    """
    assert floors.thickness_cm("Build_Foundation_8x1_01_C") == 100.0
    assert floors.thickness_cm("Build_Foundation_8x2_01_C") == 200.0
    assert floors.thickness_cm("Build_Foundation_8x4_01_C") == 400.0
    # A family with no size token falls back rather than crashing; none exists on this disk.
    assert floors.thickness_cm("Build_Foundation_Unknown_C") == floors.DEFAULT_THICKNESS_CM

    for cls, half in (
        ("Build_Foundation_8x1_01_C", 50.0),
        ("Build_Foundation_8x2_01_C", 100.0),
        ("Build_Foundation_8x4_01_C", 200.0),
    ):
        report = floor_decomposition(
            _world(
                structures={
                    "classes": [cls],
                    "instances": _deck([(0, 0), (1, 0), (2, 0)], 1200.0),
                }
            )
        )
        (band,) = report.platforms[0].bands
        assert band.top_cm == pytest.approx(1200.0 + half), cls


def test_a_platform_is_a_flood_fill_of_cells_and_not_a_slab(state):
    """The unit of decomposition, and why it is not the one ``structure.py`` builds.

    Slabs weld through ramps, stairs and walls -- correctly, for the question they answer --
    and one slab on this world spans 287 m of Z as a result. A floor decomposition over
    slabs would therefore have to un-weld them again, so it starts from a plain 4-connected
    flood fill of occupied 8 m cells instead. The two disagree, and that disagreement is the
    reason both exist.
    """
    report = floor_decomposition(state)
    assert len(report.platforms) == 132
    big = [p for p in report.platforms if p.cells >= 20]
    assert len(big) == 17
    assert sum(p.pieces for p in big) / sum(p.pieces for p in report.platforms) > 0.93

    tallest = max(state.structures.slabs, key=lambda s: s.z_span[1] - s.z_span[0])
    assert (tallest.z_span[1] - tallest.z_span[0]) / 100.0 > 200.0, (
        "a slab still welds a tower to the ground it ramps down to -- that is why the "
        "floors are not decomposed over slabs"
    )
    # No platform spans anything like that: a platform's own bands are its storeys.
    for platform in report.platforms:
        if platform.bands:
            span_m = (platform.bands[-1].top_cm - platform.bands[0].top_cm) / 100.0
            assert span_m < 200.0, platform


def test_two_platforms_that_touch_only_at_a_corner_stay_two_platforms():
    """4-connected, not 8. A shared corner is not a shared floor."""
    cls = "Build_Foundation_8x1_01_C"
    corner = {
        "structures": {
            "classes": [cls],
            "instances": (
                _deck([(0, 0), (1, 0), (0, 1)], 0.0) + _deck([(2, 2), (3, 2), (2, 3)], 0.0)
            ),
        }
    }
    report = floor_decomposition(_world(**corner))
    assert len(report.platforms) == 2
    assert [p.cells for p in report.platforms] == [3, 3]


# ------------------------------------------------------------------ the bands


def test_the_bands_on_real_platforms_are_clean(state):
    """The kill-switch criterion, kept as a test now that it has been passed.

    Stage 0 was run to find out whether floors are recoverable at all, and set the bar at
    95% of foundation pieces landing within epsilon of a detected band. The measurement came
    back at 99.87% on platforms of 20 cells or more. Below the bar the premise is wrong and
    the feature has to stop, so the bar is what is asserted -- and the measured value beside
    it, because a silent slide from 99.9% to 95.1% is exactly the drift worth catching.
    """
    report = floor_decomposition(state)
    big = [p for p in report.platforms if p.cells >= 20]
    banded = sum(p.clean * p.pieces for p in big) / sum(p.pieces for p in big)
    assert banded > 0.95, "the premise of the whole feature"
    assert banded == pytest.approx(0.9987, abs=5e-4)

    every = sum(p.clean * p.pieces for p in report.platforms) / sum(
        p.pieces for p in report.platforms
    )
    assert every == pytest.approx(0.9787, abs=5e-4)


def test_the_band_epsilon_does_not_matter_because_the_bands_are_exact(state, monkeypatch):
    """5 cm and 50 cm give the identical answer, which is the difference between a
    measurement and a tuned threshold. If this ever stops holding, the bands have stopped
    being levels and started being clusters, and every number downstream is a fit."""
    answers = set()
    for eps in (5.0, 10.0, 25.0, 50.0):
        monkeypatch.setattr(floors, "BAND_EPS_CM", eps)
        report = floor_decomposition(state)
        answers.add(
            tuple((p.index, tuple(round(b.top_cm, 2) for b in p.bands)) for p in report.platforms)
        )
    assert len(answers) == 1


def test_the_six_storey_platform_comes_out_as_its_six_storeys(state):
    """The reference world's tallest factory, pinned band for band.

    Six decks 12 m apart -- the second stage-0 correction, because the design assumed 4 m
    wall height would set the pitch and the real module is three wall courses. Nothing in
    the decomposition knows that; the histogram is asked and this is what it says.
    """
    report = floor_decomposition(state)
    (tower,) = [
        p
        for p in report.platforms
        if abs(p.centre_cm[0] + 47_400) < 300 and abs(p.centre_cm[1] + 148_900) < 300
    ]
    assert tower.cells == 335
    assert tower.pieces == 949
    assert tower.clean == 1.0
    assert [round(b.top_cm / 100.0) for b in tower.bands] == [-6, 30, 42, 54, 66, 78]
    assert [b.cells for b in tower.bands] == [111, 175, 175, 117, 218, 132]
    # Ordinals number the storeys from the bottom, which is what a floor picker lists.
    assert [b.ordinal for b in tower.bands] == [0, 1, 2, 3, 4, 5]
    # Every band is a level rather than a smear: its members share one exact top.
    assert all(b.span_cm == 0.0 for b in tower.bands)
    assert sum(len(b.machines) for b in tower.bands) == 113


def test_a_mezzanine_is_a_minor_band_and_is_neither_merged_nor_dropped(state):
    """The half-steps have to survive as their own bands, and read as minor.

    A 12 m storey and a 1 m plinth are both bands, and a decomposition that merged them
    would put machines on a floor they are not on. Cell area is what tells them apart, so
    every band carries it and a band far smaller than its platform's largest is reported
    minor rather than being quietly promoted to a storey.
    """
    report = floor_decomposition(state)
    (plat,) = [p for p in report.platforms if p.index == 2]
    assert [round(b.top_cm / 100.0) for b in plat.bands] == [17, 21]
    ledge, deck = plat.bands
    assert (ledge.cells, deck.cells) == (6, 276)
    assert ledge.minor and not deck.minor
    assert ledge.share == pytest.approx(6 / 276, abs=1e-3)
    # Cell area, not piece count, is the ordering that makes the ledge minor.
    assert ledge.area_m2 == 6 * 64.0

    # And 1-2 m gaps really are what these are: they would vanish under any fixed pitch.
    gaps = Counter()
    for platform in report.platforms:
        tops = [b.top_cm for b in platform.bands]
        for low, high in pairwise(tops):
            gaps[round((high - low) / 100.0)] += 1
    assert gaps[12] == 9, "the storey module"
    assert gaps[1] + gaps[2] == 3, "and the half-steps that must not be merged into it"


# ------------------------------------------------------------------ the assignment


def test_production_buildings_sit_at_zero_and_belt_attachments_at_a_metre(state):
    """The third correction: two constants, and no per-class offset table at all.

    The design expected the offsets to need fitting per building class. They do not -- a
    production building's pivot is its base and its base is the deck, and a splitter sits at
    the belt centre-line. Fitting a table would have produced a per-class number that looked
    measured and was noise.
    """
    report = floor_decomposition(state)
    on_deck = report.group("band")
    production = [p for p in on_deck if p.kind != "attachments"]
    attachments = [p for p in on_deck if p.kind == "attachments"]

    assert len(production) == 441
    tight = sum(1 for p in production if abs(p.offset_cm) <= 5.0)
    assert tight / len(production) == pytest.approx(0.932, abs=0.005)

    assert len(attachments) == 811
    at_belt_height = sum(1 for p in attachments if abs(p.offset_cm - floors.BELT_HEIGHT_CM) <= 5.0)
    assert at_belt_height / len(attachments) > 0.85
    assert floors.BELT_HEIGHT_CM == 100.0


def test_the_two_exemptions_are_by_native_class_and_never_by_a_substring(state):
    """Miners and water extractors are exempted, and exempted for a stated reason.

    Neither stands on a deck: a miner stands on a resource node -- up to 28 m off any floor
    -- and a water extractor stands on water. So they are classified separately instead of
    dragging a floor's offset statistics around. The test that matters is *how* they are
    picked: by the dump's own native class, not by ``"Miner" in cls``, because the same
    substring rule would also have to decide what to do with an oil pump and would decide it
    by spelling.
    """
    report = floor_decomposition(state)
    exempt = report.group("exempt")
    assert exempt, "the reference world mines"
    natives = {state.game.buildings[p.cls].native for p in exempt}
    assert natives <= set(floors.EXEMPT_NATIVES)
    assert floors.EXEMPT_NATIVES == ("FGBuildableResourceExtractor", "FGBuildableWaterPump")

    # Every extractor in the world is in the group, and nothing else is.
    assert {p.cls for p in exempt} == {
        "Build_MinerMk1_C",
        "Build_MinerMk2_C",
        "Build_OilPump_C",
        "Build_WaterPump_C",
    }
    assert all(p.group != "exempt" for p in report.placements if p.kind == "machines")
    # An exempt thing is never a member of a band, which is the point of exempting it.
    banded = {i for p in report.platforms for b in p.bands for i in b.machines}
    assert not banded & {p.instance for p in exempt}


def test_a_class_the_dump_does_not_know_is_not_exempted_on_a_guess(game):
    """The refusal that makes the native lookup worth having."""
    assert floors._is_exempt(game, "Build_MinerMk2_C")
    assert not floors._is_exempt(game, "Build_MinerOfTheFuture_C")
    assert not floors._is_exempt(None, "Build_MinerMk2_C")


def test_orphans_are_called_on_terrain_only_when_a_field_measures_them_there(state):
    """ "On terrain" is a measurement, and without the field it is not made.

    The heightfield is derived from the reader's own game install, so most machines have
    none -- and a domain service that reached for it itself would make this answer depend on
    whether somebody had run a generator. It is passed in, exactly as
    ``spatial.elevation.probe`` takes it, and its absence downgrades the claim rather than
    silently keeping it.
    """
    blind = floor_decomposition(state)
    assert blind.terrain_measured is False
    assert blind.group("terrain") == []
    orphans = blind.group("off-deck")
    assert orphans, "this world builds on bare ground in places"
    assert all(p.above_terrain_m is None for p in orphans)

    # A field that puts the ground exactly under them moves every one into ``terrain``.
    ground = sum(p.pos_cm[2] for p in orphans) / len(orphans) / 100.0
    seen = floor_decomposition(state, terrain_field=_FlatField(ground))
    assert seen.terrain_measured is True
    assert len(seen.group("terrain")) + len(seen.group("off-deck")) == len(orphans)

    # And a field that puts the ground a kilometre below leaves them all off-deck.
    far = floor_decomposition(state, terrain_field=_FlatField(ground - 1000.0))
    assert far.group("terrain") == []
    assert len(far.group("off-deck")) == len(orphans)
    assert all(p.above_terrain_m == pytest.approx(1000.0, abs=200.0) for p in far.group("off-deck"))


# ------------------------------------------------------------------ the runs


def test_belt_pieces_are_grouped_into_chains_before_anything_vertical(state):
    """The fourth correction, and the one a consumer of ``belts`` gets wrong by default.

    A belt piece is a fragment: consecutive pieces of a chain join at a median 0.00 cm, so
    the run is the chain and reasoning about a piece's two ends is reasoning about the
    middle of a belt. Asserted three ways -- the join distance, the grouping itself, and
    that the grouping CHANGES the answer, because a rule that made no difference would not
    be worth stating.
    """
    runs = floors.belt_runs(state.projection, state.game)
    segments = state.projection["belts"]["segments"]
    assert len(runs) == 1909
    assert len(segments) == 3085
    assert sum(pieces for _chain, _lift, pieces, _points in runs) == len(segments)

    # The join is between PIECES -- the last point of one and the first of the next -- and
    # not between the bends inside a piece, which are metres apart by construction.
    by_chain: dict[int, list] = {}
    for segment in segments:
        by_chain.setdefault(segment[0], []).append(segment)
    joins = sorted(
        max(abs(a[2][-1][i] - b[2][0][i]) for i in range(3))
        for pieces in by_chain.values()
        for a, b in pairwise(pieces)
    )
    assert joins, "this world has multi-piece chains"
    assert joins[len(joins) // 2] == pytest.approx(0.0, abs=1.0), "pieces of a chain meet"
    # And in the order the projection stores them, which is why concatenating is legitimate.
    assert sum(1 for d in joins if d <= 1.0) / len(joins) > 0.9

    # Per chain, 5.9% of runs cross decks. Per PIECE the same world looks flatter, because
    # most pieces are the middle of a run and both their ends are on one floor.
    report = floor_decomposition(state)
    by_chain = Counter(r.membership for r in report.runs if r.kind == "belt")
    assert by_chain["connector"] == 113
    per_piece = 0
    index = floors._platforms(floors.foundation_tops(state.projection))[1]
    for segment in segments:
        run = floors._classify(index, segment[2], floors.RUN_SLACK_CM)
        per_piece += run.membership == "connector"
    assert per_piece != by_chain["connector"], (
        "grouping by chain first has to change the answer, or the rule is decoration"
    )


def test_run_membership_is_the_partition_stage_zero_measured(state):
    """Four buckets, and the shares the measurement came back with.

    84.8% of belt runs never leave one deck, which is what makes a floor filter worth
    building at all; pipes are half that and 44.5% on terrain, because plumbing hugs the
    ground rather than riding the decks.
    """
    report = floor_decomposition(state)
    belts = [r for r in report.runs if r.kind == "belt"]
    pipes = [r for r in report.runs if r.kind == "pipe"]
    assert len(belts) == 1909
    assert len(pipes) == 503
    assert set(floors.MEMBERSHIPS) == {"same-deck", "connector", "terrain", "mixed"}

    belt = Counter(r.membership for r in belts)
    assert belt["same-deck"] / len(belts) == pytest.approx(0.848, abs=0.005)
    assert (belt["same-deck"], belt["connector"], belt["terrain"], belt["mixed"]) == (
        1619,
        113,
        148,
        29,
    )

    pipe = Counter(r.membership for r in pipes)
    assert pipe["same-deck"] / len(pipes) == pytest.approx(0.507, abs=0.005)
    assert pipe["terrain"] / len(pipes) == pytest.approx(0.445, abs=0.005)

    # A pipe is its own run -- there is no chain to group it by, and the key is the position
    # ``/api/pipes`` and ``domain.world.flow`` both count in.
    assert sorted(r.key for r in pipes) == list(range(len(pipes)))


def test_a_lift_is_not_a_floor_connector_and_a_six_metre_riser_is(state):
    """The validation check the design wrote down before measuring, and its replacement.

    "A lift's endpoints land on two distinct bands" was going to be free ground truth. It is
    false: a quarter of lift chains are belt-height jogs within one floor. What survives is
    the rise -- no chain climbing six metres lands both ends on one band, 0 of 89 here.
    """
    report = floor_decomposition(state)
    lifts = [r for r in report.runs if r.lift]
    assert len(lifts) == 183
    same_deck_jogs = sum(1 for r in lifts if r.membership == "same-deck")
    assert same_deck_jogs == 44
    assert same_deck_jogs / len(lifts) > 0.2, "the design's check 3 fails on a quarter of them"

    risers = [r for r in report.runs if r.kind == "belt" and r.riser]
    assert len(risers) == 89
    assert floors.RISER_CM == 600.0
    assert all(r.membership != "same-deck" for r in risers)
    assert report.violations == []


def test_a_riser_that_lands_twice_on_one_band_is_reported_rather_than_absorbed():
    """Constructed, because the reference world has no such chain -- which is the point.

    The rule holds empirically on every save tested, so an occurrence is not an unusual base
    but a symptom: the decomposition has merged two floors, or lost the upper one. A report
    that quietly tolerated it would hide exactly the failure it is evidence of.
    """
    cls = "Build_Foundation_8x1_01_C"
    world = _world(
        structures={
            "classes": [cls],
            "instances": _deck([(0, 0), (1, 0), (2, 0), (0, 1)], 0.0),
        },
        belts={
            "classes": ["Build_ConveyorBeltMk1_C"],
            # One chain, both ends over the same and only deck, climbing ten metres.
            "segments": [[7, 0, [[400.0, 400.0, 150.0], [1200.0, 400.0, 1150.0]]]],
        },
    )
    report = floor_decomposition(world)
    (run,) = report.runs
    assert run.rise_cm == 1000.0
    assert run.riser is True
    assert run.membership == "same-deck"
    assert report.violations == [run]
    assert report.counts()["violations"] == 1

    # Under six metres the same shape is an ordinary belt-height jog and is not reported.
    world.projection["belts"]["segments"] = [
        [7, 0, [[400.0, 400.0, 150.0], [1200.0, 400.0, 450.0]]]
    ]
    quiet = floor_decomposition(world)
    assert quiet.runs[0].riser is False
    assert quiet.violations == []


def test_a_pipe_may_loop_over_an_obstacle_and_is_not_held_to_the_chain_rule():
    """The rule is about a belt CHAIN, and a pipe is under no such obligation."""
    cls = "Build_Foundation_8x1_01_C"
    world = _world(
        structures={
            "classes": [cls],
            "instances": _deck([(0, 0), (1, 0), (2, 0), (0, 1)], 0.0),
        },
        pipes={
            "classes": ["Build_Pipeline_C"],
            "segments": [
                [0, 0, [[400.0, 400.0, 50.0], [800.0, 400.0, 1200.0], [1200.0, 400.0, 50.0]]]
            ],
        },
    )
    report = floor_decomposition(world)
    assert report.runs[0].membership == "same-deck"
    assert report.violations == [], "a pipe that goes up and comes back down is plumbing"


# ------------------------------------------------------------------ the refusals


def test_a_save_that_predates_lightweight_buildables_says_exactly_that(game):
    """Not "no floors". The three shapes the projection has actually carried.

    Pre-U8 saves emit no ``FGLightweightBuildableSubsystem`` at all, so the honest answer is
    about the save's age. An empty band list with no note reads as a measurement of a world
    with no storeys, which is a different and false claim about somebody's 2022 base.
    """
    for payload in ({}, {"structures": {}}, {"structures": {"classes": [], "instances": []}}):
        report = floor_decomposition(_world(game=game, **payload))
        assert report.note == floors.TOO_OLD_NOTE
        assert "predates lightweight buildables" in report.note
        assert report.platforms == []
        assert report.runs == []
        assert report.counts()["bands"] == 0


def test_a_world_with_walls_but_no_foundations_gets_its_own_answer(game):
    """A different refusal from the one above, because it is a different world.

    The subsystem is present and the player has built with it; what they have not done is
    pour a deck. Saying "this save is too old" there would be flatly wrong.
    """
    report = floor_decomposition(
        _world(
            game=game,
            structures={
                "classes": ["Build_Wall_8x4_01_C"],
                "instances": [_tile(0, 0, 0, 0.0)],
            },
        )
    )
    assert report.note == floors.NO_FOUNDATIONS_NOTE
    assert report.note != floors.TOO_OLD_NOTE
    assert report.platforms == []


# ------------------------------------------------------------------ narrowing


def test_narrowing_to_one_platform_keeps_everything_over_its_footprint(state):
    """A floor picker asks about one platform, and gets its floors and nothing else."""
    whole = floor_decomposition(state)
    one = floor_decomposition(state, platform=1)
    assert one.selection == "platform 1"
    assert [p.index for p in one.platforms] == [1]
    assert len(one.platforms[0].bands) == 6
    assert len(one.runs) < len(whole.runs)
    assert len(one.placements) < len(whole.placements)
    # Everything kept stands over one of the platform's own cells.
    cells = one.platforms[0].cell_set
    assert all(floors.cell_of(p.pos_cm[0], p.pos_cm[1]) in cells for p in one.placements)
    # And every band member survives the narrowing, since a band is what was asked about.
    assert sum(len(b.machines) for b in one.platforms[0].bands) == 113


def test_asking_for_a_platform_that_is_not_there_says_so(state):
    report = floor_decomposition(state, platform=9999)
    assert report.platforms == []
    assert report.note and "no platform matches" in report.note


def test_platform_indices_are_stable_across_runs(state):
    """``platform=N`` is an argument a caller keeps, so it has to name the same deck twice.

    Ordering is by cell count and then by the platform's lowest cell -- the tie-break
    matters, because 115 of this world's 132 platforms are under 20 cells and plenty of them
    are the same size.
    """
    first = floor_decomposition(state)
    second = floor_decomposition(state)
    assert [(p.index, p.cells, p.centre_cm) for p in first.platforms] == [
        (p.index, p.cells, p.centre_cm) for p in second.platforms
    ]
    assert len({p.cells for p in first.platforms}) < len(first.platforms), "there are ties"


# ------------------------------------------------------------------ a real save


@pytest.mark.skipif(
    not __import__("satisfactory_mcp.config", fromlist=["config"]).saves_root().is_dir(),
    reason="needs a real save directory (set SATISFACTORY_SAVES)",
)
def test_the_criteria_hold_on_whatever_save_this_machine_has(game):
    """The fixture is one world frozen in July 2026; this is whatever is on the disk now.

    Deliberately the stage-0 *criteria* rather than this world's numbers: the fixture pins
    the numbers, and a live save directory grows a floor whenever the player builds one. So
    what is asserted here is what has to be true of any base -- the bands are clean, the
    offsets hold, and no riser lands twice on one deck.
    """
    from satisfactory_mcp.core.saveio import projection as proj

    try:
        worlds, _unsupported = proj.list_worlds()
        if not worlds:
            pytest.skip("no worlds in the save directory")
        state = WorldState(projection=proj.load_projection(world=worlds[0].world_id), game=game)
    except Exception as exc:  # pragma: no cover - a machine without a readable save
        pytest.skip(f"no readable save: {exc}")

    report = floor_decomposition(state)
    if report.note:
        pytest.skip(report.note)

    big = [p for p in report.platforms if p.cells >= 20]
    if not big:
        pytest.skip("no platform large enough to have storeys")
    banded = sum(p.clean * p.pieces for p in big) / sum(p.pieces for p in big)
    assert banded > 0.95, f"band cleanliness fell to {banded:.4f}"

    production = [p for p in report.group("band") if p.kind != "attachments"]
    if production:
        tight = sum(1 for p in production if abs(p.offset_cm) <= 5.0) / len(production)
        assert tight > 0.85, f"production buildings drifted off their decks: {tight:.3f}"

    assert report.violations == [], "a six-metre riser landed both ends on one band"
    for platform in report.platforms:
        assert [b.ordinal for b in platform.bands] == list(range(len(platform.bands)))
        assert platform.bands == sorted(platform.bands, key=lambda b: b.top_cm)
