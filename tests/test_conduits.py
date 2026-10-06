"""Belt and pipe runs as queryable things -- the projection's geometry, finally readable.

The failure this guards against was live and expensive: the projection has carried every
conveyor and pipeline polyline since schemas 12/13, the web map drew them, and no text
tool could see them -- so the assistant twice told the player a build did not exist when
the tools simply could not look. These tests pin the two halves of the fix: the domain
grouping in ``domain.world.conduits``, and the honesty rules of what the surface says --
mid-span crossings count, zeroes are printed, and geometry guesses are labelled as such.
"""

from __future__ import annotations

import itertools

import pytest

from satisfactory_mcp.core.saveio import rows as saverows
from satisfactory_mcp.domain.spatial import geo
from satisfactory_mcp.domain.world import conduits

# ------------------------------------------------------------ synthetic worlds
#
# Hand-built projections, because every rule here is about SHAPE -- what joins, what
# counts as near, which end is which -- and a shape is easiest to reason about when the
# test states it in full. Real-world coverage rides on the fixture tests below.

#: Two belt pieces in one chain, end to start, then a second chain 500 m away whose one
#: piece is a LONG straight line -- the mid-span case that point-distance misses.
BELT_PROJECTION = {
    "belts": {
        "classes": ["Build_ConveyorBeltMk1_C", "Build_ConveyorBeltMk3_C"],
        "segments": [
            [7, 0, [[0, 0, 100], [1000, 0, 100]], -1, None],
            [7, 1, [[1000, 0, 100], [2000, 0, 150]], -1, None],
            [8, 0, [[0, 50000, 0], [50000, 50000, 0]], -1, None],
        ],
    },
    "machines": [],
    "extractors": [],
    "generators": [],
}

#: Two pipe pieces meeting at a joint, claimed by one network. The second network is
#: empty of fluid, which is what a drained or half-built system gives.
PIPE_PROJECTION = {
    "pipes": {
        "classes": ["Build_Pipeline_C"],
        "networks": [{"id": 33, "fluid": "Desc_Water_C"}],
        "segments": [
            [0, 0, [[0, 0, 0], [3000, 0, 0]], -1, None],
            [0, 0, [[3000, 0, 0], [3000, 4000, 0]], -1, None],
        ],
    },
    "machines": [],
    "extractors": [],
    "generators": [],
}


def test_belt_pieces_group_by_chain_and_the_open_ends_win(game):
    runs = conduits.build_runs(BELT_PROJECTION, game)
    assert len(runs) == 2
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.ident == "chain:7"
    # 10 m flat, then 10 m across with a 0.5 m rise -- 3D length, not the plan view.
    assert two_piece.length_m == pytest.approx(10.0 + (10.0**2 + 0.5**2) ** 0.5)
    # The joint at (1000, 0) is internal; the extremities are the run's ends, oriented
    # input -> output because every piece's point order says so.
    assert (two_piece.a.x, two_piece.a.y) == (0, 0)
    assert (two_piece.b.x, two_piece.b.y) == (2000, 0)
    assert two_piece.directed


def test_a_mixed_tier_chain_shows_its_span_and_quotes_the_slowest_cap(game):
    runs = conduits.build_runs(BELT_PROJECTION, game)
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.label == "belt mk1-mk3"
    assert two_piece.rate == game.buildings["Build_ConveyorBeltMk1_C"].items_per_min


def test_distance_is_to_the_drawn_line_not_the_corner_points(game):
    """A 500 m straight belt has exactly two stored points. Measuring to the points
    alone calls its middle 250 m away -- the precise blindness that had describe_location
    deny a build that was there."""
    runs = conduits.build_runs(BELT_PROJECTION, game)
    long_run = next(r for r in runs if r.ident == "chain:8")
    assert long_run.dist_m(25000, 50000) == pytest.approx(0.0)
    counted = conduits.near_counts(runs, 25000, 50500, radius_m=10.0)
    assert counted == {"belt": 1, "pipe": 0}


def test_near_counts_always_answers_both_kinds(game):
    """The zero is the deliverable: 'no pipes here' must be a statement, not a missing
    key, because absence in the output is now read as absence in the world."""
    counted = conduits.near_counts(conduits.build_runs(BELT_PROJECTION, game), 0, 0, 5.0)
    assert counted == {"belt": 1, "pipe": 0}
    assert conduits.near_counts([], 0, 0, 5.0) == {"belt": 0, "pipe": 0}


def test_pipes_stay_one_run_per_piece_and_carry_their_network(game):
    runs = conduits.build_runs(PIPE_PROJECTION, game)
    assert [r.kind for r in runs] == ["pipe", "pipe"]
    assert all(r.fluid == "Desc_Water_C" and r.network == 33 for r in runs)
    assert runs[0].length_m == pytest.approx(30.0)


def test_an_unplugged_end_names_the_run_it_continues_into(game):
    """A mid-network joint is the ordinary case for pipes, not an unknown: the end
    names its neighbour's ident so a route can be followed piece to piece."""
    runs = conduits.build_runs(PIPE_PROJECTION, game)
    first, second = runs
    assert first.b.plugs == second.ident
    assert second.a.plugs == first.ident
    # The far ends touch nothing and say so.
    assert first.a.plugs is None
    assert second.b.plugs is None


def test_an_end_inside_a_machines_footprint_names_the_machine(game):
    projection = {
        **BELT_PROJECTION,
        "machines": [
            {
                "instance": "L:P.Build_ConstructorMk1_C_1",
                "cls": "Build_ConstructorMk1_C",
                "pos": [2100.0, 0.0, 100.0],
            }
        ],
    }
    runs = conduits.build_runs(projection, game)
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.b.plugs == game.buildings["Build_ConstructorMk1_C"].name
    assert two_piece.a.plugs is None


# ------------------------------------------------------------ length is the drawn line

#: A 90-degree elbow with the tangents the game gives one: 20 m across, 20 m up the y
#: axis, each tangent along its own axis at three times the quarter-circle control offset.
_ELBOW_P0 = [0.0, 0.0, 0.0]
_ELBOW_P1 = [2000.0, 2000.0, 0.0]
_ELBOW_SPAN = [0.0, 3313.7, 0.0, 3313.7, 0.0, 0.0]


def _tessellated_m(p0, p1, span, steps: int = 8192) -> float:
    """The same span walked in tiny straight steps -- the tessellation ``/api/belts``
    hands the page. An independent answer, so the quadrature is checked against the curve
    rather than against itself."""

    def at(t: float) -> list[float]:
        t2, t3 = t * t, t * t * t
        h00, h10 = 2 * t3 - 3 * t2 + 1, t3 - 2 * t2 + t
        h01, h11 = -2 * t3 + 3 * t2, t3 - t2
        return [h00 * p0[i] + h10 * span[i] + h01 * p1[i] + h11 * span[i + 3] for i in range(3)]

    points = [at(k / steps) for k in range(steps + 1)]
    return sum(geo.distance_3d_m(a, b) for a, b in itertools.pairwise(points))


def test_a_bend_is_integrated_along_its_spline_not_cut_across_it():
    """The two halves disagreed about the same belt: the map drew the Hermite the save
    records and this module summed the chords between its corners, out by up to 16.4 m on
    one piece. Both now measure the curve."""
    arc = conduits._length_m([_ELBOW_P0, _ELBOW_P1], [_ELBOW_SPAN])
    assert arc == pytest.approx(_tessellated_m(_ELBOW_P0, _ELBOW_P1, _ELBOW_SPAN), abs=1e-4)
    assert arc > geo.distance_3d_m(_ELBOW_P0, _ELBOW_P1) + 3.0


def test_a_span_with_no_recorded_tangents_stays_its_chord():
    """Schema 15 emits the column only where a chord is out by a centimetre or more, and
    stores ``0`` for a flat span inside a bent route. Both are the straight case, and a
    projection older than 15 has no column at all."""
    line = [[0.0, 0.0, 0.0], [1000.0, 0.0, 0.0], [2000.0, 0.0, 0.0]]
    assert conduits._length_m(line) == pytest.approx(20.0)
    assert conduits._length_m(line, [0, 0]) == pytest.approx(20.0)
    # A torn row costs its curve, never the run.
    assert conduits._length_m(line, ["nonsense", [1, 2]]) == pytest.approx(20.0)


def test_the_fixture_world_gains_length_where_it_bends(game, projection):
    """Measured on the committed world: the arc is longer than the chord everywhere the
    tangents exist and nowhere else, so a change that quietly stopped reading the column
    shows up as equality rather than as a plausible smaller number."""
    curved = straight = 0
    for seg in saverows.iter_belt_segments(projection):
        chord = sum(geo.distance_3d_m(p, q) for p, q in itertools.pairwise(seg.points))
        arc = conduits._length_m(seg.points, seg.spans)
        assert arc >= chord - 1e-9
        if seg.spans:
            curved += 1
            assert arc > chord
        else:
            straight += 1
            assert arc == pytest.approx(chord)
    assert curved and straight


def test_a_projection_without_conduit_tables_yields_no_runs(game):
    """Schema 11 and earlier. One empty answer, never a raise -- the same degradation
    every other reader of these tables promises."""
    assert conduits.build_runs({"machines": []}, game) == []


# ------------------------------------------------------------ the fixture world


def test_every_belt_chain_and_every_pipe_piece_becomes_a_run(game, projection):
    """The committed world, whole: nothing the map draws may be missing here, because
    'the tools could not see it' is the exact failure this module retires."""
    runs = conduits.build_runs(projection, game)
    chains = {seg.chain for seg in saverows.iter_belt_segments(projection)}
    pipes = sum(1 for _ in saverows.iter_pipe_segments(projection))
    belts = [r for r in runs if r.kind != "pipe"]
    assert len(belts) == len(chains)
    assert sum(1 for r in runs if r.kind == "pipe") == pipes
    assert all(r.length_m > 0 for r in runs)


def test_most_ends_on_the_reference_world_resolve_to_something(game, projection):
    """A guess rate is worth pinning loosely: on the reference save the nearest-port
    join plus the continuation join resolve the large majority of ends. If this drops
    sharply, the geometry join broke -- if it hits 100%, somebody started inventing."""
    runs = conduits.build_runs(projection, game)
    ends = [e for r in runs for e in (r.a, r.b)]
    plugged = sum(1 for e in ends if e.plugs is not None)
    assert 0.7 < plugged / len(ends) < 1.0


def test_the_tool_pages_the_offset_its_truncation_line_promises(game, use_world):
    """The truncation envelope ends 'call again with offset=N', so the tool has to
    HAVE an offset -- a next step the caller cannot take is worse than no next step,
    and this surface already pages this way in list_buildings and commission_plan."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    # Five chains through one point, each a different length, so the longest-first
    # order is unambiguous and a page boundary is checkable by name.
    projection = {
        "header": {"save_identifier": "TEST-conduit-paging", "session_name": "t"},
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                [i, 0, [[0, i * 100, 0], [(i + 1) * 1000, i * 100, 0]], -1, None] for i in range(5)
            ],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    use_world(st)

    first = stools.search_conduits(near="0,0", radius_m=500, limit=2)
    assert "5 match(es), showing 2 from offset 0. 3 more: call again with offset=2" in first
    assert "chain:4" in first and "chain:2" not in first

    second = stools.search_conduits(near="0,0", radius_m=500, limit=2, offset=2)
    assert "5 match(es), showing 2 from offset 2. 1 more: call again with offset=4" in second
    assert "chain:2" in second and "chain:4" not in second


def test_a_pipes_arrow_carries_the_evidence_behind_it(game, projection):
    """``flow.py`` labels every inferred direction with the evidence for it and
    ``/api/pipes`` has published that all along; the text side printed -> or -- and could
    never say on what grounds. A belt carries none: its order is the pieces' own."""
    from satisfactory_mcp.domain.world import flow as world_flow

    runs = conduits.build_runs(projection, game, world_flow.pipe_flow(projection))
    pipes = [r for r in runs if r.kind == "pipe"]
    assert pipes
    assert all(r.basis is None for r in runs if r.kind != "pipe")
    assert {r.basis for r in pipes} <= {
        world_flow.BASIS_PORT,
        world_flow.BASIS_DEVICE,
        world_flow.BASIS_NETWORK,
        world_flow.BASIS_NONE,
    }
    # The two halves of one fact: an arrow exists exactly where the basis is not the refusal.
    assert all(r.directed is (r.basis != world_flow.BASIS_NONE) for r in pipes)
    # And with no flow passed at all, nothing is claimed rather than something assumed.
    bare = conduits.build_runs(projection, game)
    assert {r.basis for r in bare if r.kind == "pipe"} == {world_flow.BASIS_NONE}


def test_a_run_ident_centres_on_that_runs_midpoint(game):
    """The tool printed 'connects: pipe:333 -> pipe:335' and told the reader to follow
    it, while taking no run id -- so following a 20-piece route meant 20 coordinate
    round trips. The MIDPOINT, because a radius around one end sees half the run."""
    from satisfactory_mcp.domain.spatial.places import resolve_place
    from satisfactory_mcp.domain.world.state import WorldState

    # An L: 20 m east, then 20 m south. Half the drawn line is the corner.
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                [3, 0, [[0, 0, 0], [2000, 0, 0]], -1, None],
                [3, 0, [[2000, 0, 0], [2000, 2000, 0]], -1, None],
            ],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    (x, y), where = resolve_place(st, "chain:3")
    assert (x, y) == pytest.approx((2000.0, 0.0))
    assert where.startswith("chain:3 (midpoint of a 40m belt mk1")


def test_an_unknown_run_ident_names_the_tool_that_lists_them(game):
    from satisfactory_mcp.domain.spatial.places import resolve_place
    from satisfactory_mcp.domain.world.state import WorldState

    st = WorldState(projection={"machines": []}, game=game)
    with pytest.raises(ValueError, match="search_conduits lists the ids"):
        resolve_place(st, "chain:9999")
    with pytest.raises(ValueError, match="needs a readable save"):
        resolve_place(None, "pipe:1")


def test_the_tool_takes_back_the_ids_it_prints(game, use_world):
    """One spelling, end to end: the id in the `id` and `connects` columns is the id
    `near=` reads."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    projection = {
        "header": {"save_identifier": "TEST-conduit-ident", "session_name": "t"},
        **PIPE_PROJECTION,
    }
    st = WorldState(projection=projection, game=game)
    use_world(st)

    out = stools.search_conduits(near="pipe:0", radius_m=25)
    assert "midpoint of a 30m pipe" in out
    assert "pipe:1" in out


def test_the_network_view_summarises_the_plumbing_systems(game, use_world):
    """503 pipe rows are not navigable; 19 plumbing systems are. The top-level
    pipe_networks table had no reader outside one test, and this is the view it is for."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    projection = {
        "header": {"save_identifier": "TEST-conduit-networks", "session_name": "t"},
        "pipe_networks": [{"instance": "L:P.FGPipeNetwork_33", "fluid": "Desc_Water_C"}],
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            # One named network of two pieces, one nameless network of one.
            "networks": [{"id": 33, "fluid": "Desc_Water_C"}, {"id": 34, "fluid": None}],
            "segments": [
                [0, 0, [[0, 0, 0], [3000, 0, 0]], -1, None],
                [0, 0, [[3000, 0, 0], [3000, 4000, 0]], -1, None],
                [1, 0, [[0, 90000, 0], [1000, 90000, 0]], -1, None],
            ],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    use_world(st)

    out = stools.search_conduits(near="0,0", show="networks")
    assert "2 fluid network(s)" in out
    # Most pipe first, and the whole world regardless of radius: the far network is here.
    rows = [line.split("\t") for line in out.splitlines() if "\t" in line][1:]
    assert [r[0] for r in rows] == ["33", "34"]
    assert rows[0][1] == "Water" and rows[1][1] == "?"
    assert rows[0][2] == "2" and rows[0][3] == "70m"
    assert "the save names a fluid for 1 of its networks" in out

    assert "! unknown show" in stools.search_conduits(near="0,0", show="bogus")
    assert "belt chain belongs to none" in stools.search_conduits(
        near="0,0", show="networks", conduit_kind="belt"
    )


# ------------------------------------------------------------ the tool, live


@pytest.mark.integration
def test_the_tool_lists_runs_where_the_map_draws_them(game, live):
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="239,-1928", radius_m=200)
    assert "conduit run(s) within 200m" in out
    # The Spire Coast oil site: developed for 300+ hours, so SOMETHING runs there --
    # and if the player one day tears it all out, the zero form is still the contract.
    assert ("id\tkind\tlen" in out) or ("nothing runs there" in out)


@pytest.mark.integration
def test_open_ocean_answers_zero_not_silence(game, live):
    """The deliverable: 'no pipe here' as a statement the reader can trust, in both
    tools, because absence in the output was twice read as the tools being unable to
    see rather than the world being empty -- and it was the tools."""
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="-3500,3500", radius_m=200)
    assert "0 conduit run(s)" in out
    assert "nothing runs there" in out

    described = srv.describe_location("-3500,3500")
    radius = f"{conduits.NEAR_RADIUS_M:g}m"
    assert f"conduits=0 belt run(s), 0 pipe run(s) within {radius}" in described


@pytest.mark.integration
def test_describe_location_counts_what_runs_through_a_built_site(game, live):
    from satisfactory_mcp import server as srv

    out = srv.describe_location("239,-1928")
    assert "conduits=" in out
    assert "belt run(s)" in out and "pipe run(s)" in out


@pytest.mark.integration
def test_between_mode_requires_both_areas_and_says_so_in_the_scope(game, live):
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="-3500,3500", to="-3400,3400", radius_m=50)
    assert "AND 50m of -3400,3400" in out


def test_the_tool_lists_one_fluid_network_by_id(game, projection, use_world):
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    st = WorldState(projection=projection, game=game)
    use_world(st)
    net = next(r.network for r in st.conduit_runs if r.kind == "pipe" and r.network is not None)
    pieces = [r for r in st.conduit_runs if r.kind == "pipe" and r.network == net]
    out = stools.search_conduits(near="0,0", network=net, limit=100)
    assert f"# {len(pieces)} pipe run(s) on fluid network {net}" in out
    longest = sorted(pieces, key=lambda r: -r.length_m)[:10]
    listed = [line.split("\t")[0] for line in out.splitlines()]
    assert all(r.ident in listed for r in longest)
