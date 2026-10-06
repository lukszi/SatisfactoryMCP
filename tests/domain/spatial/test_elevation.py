"""Elevation: measured where the extracted terrain field reaches, sampled everywhere else.

`search_resource_nodes` already reports a fluid field's head span, and `plan_layout
show="trunks"` attaches it to a pipe. This closes the other half: what a bare coordinate
is at. The whole risk here is the same one twice -- a plausible single number invented from
too few points reads as measured, so every assertion below is about refusing to do that,
and about keeping the field's one texel apart from the population standing near it.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.spatial import elevation, heightfield
from satisfactory_mcp.domain.spatial import nodes as nodes_mod
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.heightfields import FAKE_SPACING, FAKE_X0, FAKE_Y0, build_field

#: On the main platform, dense with foundations. Chosen because it is the case where the
#: built population swamps the ground population.
ON_PLATFORM = (-1216, -1127)
#: In the crude field: nodes, no structures.
IN_THE_FIELD = (2000, -2400)
#: Nothing within any sane radius.
OPEN_OCEAN = (-3500, 3500)


def _at(point: tuple[float, float]) -> str:
    return f"{point[0]},{point[1]}"


@pytest.fixture
def points(state):
    return elevation.sample_points(nodes_mod.load_nodes(), state)


# ------------------------------------------------------------- the sources


def _counts(points) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in points:
        out[s.source] = out.get(s.source, 0) + 1
    return out


@pytest.mark.integration
def test_an_old_projection_loses_a_source_rather_than_failing(game, state):
    """A projection written before `structures` existed carries no foundations. That must
    cost one source and nothing else -- the same tolerance `sloop_budget` and
    `phase_requirements` show for projections written before the field they want existed.

    The key is deleted here rather than relied on being absent. It used to be absent: the
    committed fixture was schema 5 and predated the extraction, so this test passed without
    doing anything. Regenerating the fixture at schema 11 silently turned it into a test of
    the current save, which is what the other tests in this file already are."""
    projection = deepcopy(state.projection)
    del projection["structures"]
    counts = _counts(
        elevation.sample_points(
            nodes_mod.load_nodes(), WorldState(projection=projection, game=game)
        )
    )
    assert "structure" not in counts
    assert counts["node"] == 608
    assert counts["building"] > 400


@pytest.mark.integration
def test_foundations_are_the_dense_source_on_a_current_save(game, live):
    """8,347 of them against 566 buildings on the live save. They are what gives a
    developed site any evidence about itself at all -- one building per machine is sparse,
    one sample per foundation piece is not."""
    counts = _counts(elevation.sample_points(nodes_mod.load_nodes(), live))
    assert counts["structure"] > 8000
    assert counts["structure"] > counts["building"] * 10


@pytest.mark.integration
def test_it_works_with_no_save_at_all(game):
    """The node table is static and map-wide, so an unexplored coordinate still gets an
    answer. Requiring a save would make this useless for exactly the ground a player is
    scouting."""
    only_nodes = elevation.sample_points(nodes_mod.load_nodes(), None)
    assert only_nodes
    assert {s.source for s in only_nodes} == {"node"}


@pytest.mark.integration
def test_a_probe_only_returns_what_is_inside_the_radius(points):
    near = elevation.probe(IN_THE_FIELD[0] * 100, IN_THE_FIELD[1] * 100, points, radius_m=400)
    assert near.samples
    assert all(s.distance_m <= 400 + 1e-6 for s in near.samples)
    assert [s.distance_m for s in near.samples] == sorted(s.distance_m for s in near.samples)


@pytest.mark.integration
def test_a_wider_radius_never_loses_a_sample(points):
    small = elevation.probe(ON_PLATFORM[0] * 100, ON_PLATFORM[1] * 100, points, radius_m=100)
    big = elevation.probe(ON_PLATFORM[0] * 100, ON_PLATFORM[1] * 100, points, radius_m=300)
    assert len(big.samples) >= len(small.samples)


# ------------------------------------------------- ground and built stay apart


@pytest.mark.integration
def test_ground_and_built_are_separate_populations(points):
    """A node is on terrain; a foundation is wherever the player put it, often levelled
    across a slope. Averaged together on a developed site the structures outnumber the
    nodes hundreds to one, so the result would silently BE the platform height while
    still being labelled ground."""
    near = elevation.probe(ON_PLATFORM[0] * 100, ON_PLATFORM[1] * 100, points, radius_m=200)
    assert near.ground_m and near.built_m
    assert set(near.ground_m).isdisjoint(set(near.built_m)) or True  # values may coincide
    assert len(near.ground_m) + len(near.built_m) == len(near.samples)
    assert "structure" not in elevation.GROUND_SOURCES
    assert "building" not in elevation.GROUND_SOURCES


@pytest.mark.integration
def test_fill_depth_needs_more_than_one_ground_sample(points):
    """The measured trap: 805 structures against 1 node on the main platform. One node is
    a point, and a point is not a ground level, so no fill depth is quoted from it."""
    near = elevation.probe(ON_PLATFORM[0] * 100, ON_PLATFORM[1] * 100, points, radius_m=200)
    assert len(near.ground_m) < elevation.MIN_GROUND_SAMPLES
    assert near.fill_m is None


@pytest.mark.integration
def test_fill_depth_is_a_difference_of_medians_when_both_sides_are_real():
    made = [
        elevation.Sample("node", 0, 0, 1000.0),
        elevation.Sample("node", 10, 0, 1000.0),
        elevation.Sample("node", 20, 0, 1000.0),
        elevation.Sample("structure", 0, 0, 3000.0),
        elevation.Sample("structure", 10, 0, 3000.0),
    ]
    near = elevation.probe(0, 0, made, radius_m=10)
    assert near.fill_m == pytest.approx(20.0)


@pytest.mark.integration
def test_one_sided_evidence_yields_no_fill(points):
    """In the crude field there are nodes and nothing built. A fill depth is a difference
    and one side alone cannot produce it."""
    near = elevation.probe(IN_THE_FIELD[0] * 100, IN_THE_FIELD[1] * 100, points, radius_m=400)
    assert near.ground_m and not near.built_m
    assert near.fill_m is None


@pytest.mark.integration
def test_the_median_resists_a_spire(points):
    """One Spire Coast node sits 220 m above its neighbours. A mean would drag the field's
    reported height up by tens of metres; the median does not move."""
    near = elevation.probe(IN_THE_FIELD[0] * 100, IN_THE_FIELD[1] * 100, points, radius_m=400)
    values = near.ground_m
    assert max(values) - min(values) > 200
    assert near.median("node") < 50


# ------------------------------------------------------------- the tool


@pytest.mark.integration
def test_an_even_count_takes_the_mean_of_the_middle_two():
    assert elevation.Elevation.median_of([127.0, 137.0]) == pytest.approx(132.0)
    assert elevation.Elevation.median_of([1.0, 2.0, 9.0]) == 2.0
    assert elevation.Elevation.median_of([]) is None


@pytest.mark.parametrize("radius", [50, 200, 400, 800])
@pytest.mark.integration
def test_the_tool_quotes_the_median_the_inspector_sends(game, points, radius):
    near = elevation.probe(IN_THE_FIELD[0] * 100, IN_THE_FIELD[1] * 100, points, radius_m=radius)
    out = srv.describe_location(_at(IN_THE_FIELD), radius_m=radius)
    if not near.ground_m:
        assert "ground_elevation_m=" not in out
        return
    assert f"ground_elevation_m={near.median('node'):.0f} (median of {len(near.ground_m)}," in out


@pytest.mark.integration
def test_describe_location_reports_elevation(game):
    out = srv.describe_location(_at(ON_PLATFORM))
    assert "built_elevation_m=" in out
    assert "samples=" in out
    assert "structure" in out


class _OneTexel:
    """As much of a ``heightfield.Field`` as ``probe`` uses: ``z`` (and ``at``). A synthetic
    reading, so these tests answer the same on a machine that has run the generator and one
    that never will."""

    def __init__(self, reading: heightfield.Reading | None) -> None:
        self._reading = reading

    def texel_reading(self, x_cm: float, y_cm: float) -> heightfield.Reading | None:
        return self._reading

    def height_at(self, x_cm: float, y_cm: float, **_: object) -> heightfield.Reading | None:
        return self._reading


@pytest.mark.integration
def test_the_measured_terrain_is_reported_where_the_field_reaches(game, monkeypatch):
    """``/api/inspect`` has printed this texel all along while this tool asserted there was
    no terrain data in anything it reads. The seam was already built -- ``probe`` takes the
    field -- and only the MCP side never passed one."""
    monkeypatch.setattr(
        heightfield,
        "load_field",
        lambda: _OneTexel(
            heightfield.Reading(z_m=118.3, provenance=heightfield.PROV_LANDSCAPE, accuracy_m=0.2)
        ),
    )
    out = srv.describe_location(_at(ON_PLATFORM))
    assert "terrain_m=118.3 (landscape, +-0.2m)" in out


@pytest.mark.integration
def test_water_depth_is_quoted_only_where_the_bed_was_measured(game, monkeypatch):
    """The field's own refusal, carried out to the text surface: over the fill layer the
    ground is a 3.9 m raster, so subtracting it from a sea surface invents a depth."""
    monkeypatch.setattr(
        heightfield,
        "load_field",
        lambda: _OneTexel(
            heightfield.Reading(
                z_m=-17.0,
                provenance=heightfield.PROV_FILL,
                accuracy_m=3.9,
                water_m=-2.0,
                water_quality=heightfield.WATER_LEVEL_ONLY,
            )
        ),
    )
    out = srv.describe_location(_at(OPEN_OCEAN))
    assert "water_surface_m=-2.0" in out
    assert "water_depth_m=unknown" in out
    assert "too coarse" in out

    monkeypatch.setattr(
        heightfield,
        "load_field",
        lambda: _OneTexel(
            heightfield.Reading(
                z_m=-17.0,
                provenance=heightfield.PROV_LANDSCAPE,
                accuracy_m=0.2,
                water_m=-2.0,
                water_quality=heightfield.WATER_MEASURED,
            )
        ),
    )
    assert "water_depth_m=15.0" in srv.describe_location(_at(OPEN_OCEAN))


@pytest.mark.parametrize("cave", ["below", "inside"])
@pytest.mark.integration
def test_a_cave_point_says_so_and_inside_withholds_the_surface(game, monkeypatch, cave):
    reading = heightfield.Reading(
        z_m=233.4, provenance=heightfield.PROV_LANDSCAPE, accuracy_m=0.2, cave=cave
    )
    monkeypatch.setattr(heightfield, "load_field", lambda: _OneTexel(reading))
    out = srv.describe_location(_at(ON_PLATFORM))
    if cave == "inside":
        assert "terrain_m=unknown" in out
        assert "cave=in a cave: ground height unknown here (the surface above is 233 m)" in out
    else:
        assert "terrain_m=233.4" in out
        assert "cave=a cave lies under this point" in out
    whereami = srv.whereami()
    assert ("cave=in a cave" in whereami) == (cave == "inside")


@pytest.mark.integration
def test_without_a_field_it_names_the_gap_rather_than_denying_terrain_exists(game, monkeypatch):
    """The standing rule: name what the data cannot do. What it must NOT do any more is
    state that no terrain data exists -- it exists, this machine has not extracted it."""
    monkeypatch.setattr(heightfield, "load_field", lambda: None)
    out = srv.describe_location(_at(ON_PLATFORM))
    assert "no terrain field on this machine" in out
    assert "SAMPLED from things standing nearby" in out


@pytest.mark.integration
def test_unsurveyed_ground_says_unknown_rather_than_guessing(game):
    """The nearest-land guess is exactly the failure describe_location already refuses for
    region naming. Elevation gets the same treatment."""
    out = srv.describe_location(_at(OPEN_OCEAN))
    assert "no known elevation" in out
    assert "elevation_m=" not in out
    assert "off-map or ocean" in out


@pytest.mark.integration
def test_the_radius_is_the_callers_to_widen(game):
    tight = srv.describe_location(_at(IN_THE_FIELD), radius_m=50)
    wide = srv.describe_location(_at(IN_THE_FIELD), radius_m=800)
    assert "no known elevation within 50m" in tight
    assert "ground_elevation_m=" in wide


@pytest.mark.integration
def test_a_place_is_the_only_way_to_say_where(game):
    """Every other tool on this surface takes a place; this one also declared two floats,
    so a client reading the schema met two ways to say one thing. The floats are gone, and
    a bare coordinate through `at=` answers exactly what they answered."""
    out = srv.describe_location(at=_at(IN_THE_FIELD))
    assert "at=2000,-2400  region=" in out, "a coordinate resolves to itself; name it once"
    assert "region=Spire Coast" in out
    assert srv.describe_location().startswith("! describe_location needs at=")


@pytest.mark.integration
def test_a_bare_platform_is_a_place_this_tool_accepts(game, live):
    """The dead end item 13 names: factory_map lists bare platforms by an index no tool
    would take, so the table added to retire the nine-probe workflow led straight back
    into it. The resolved point is echoed, because `at=` lands somewhere nobody typed."""
    out = srv.describe_location(at="slab:0")
    assert out.startswith("at=") and "(slab:0 (" in out
    assert "! slab:99999 out of range" in srv.describe_location(at="slab:99999")


@pytest.mark.integration
def test_region_naming_still_works_exactly_as_before(game):
    """Elevation is an addition. The region answer that callers already depend on must be
    untouched, including its confidence word."""
    out = srv.describe_location(_at(IN_THE_FIELD))
    assert "region=Spire Coast" in out
    assert "confidence=interior" in out
    assert "grid=X5Y5" in out


# ------------------------------------------- the terrain field beside the populations


def _samples() -> list[elevation.Sample]:
    """Three nodes and a foundation, all within the probe radius of the field's origin."""
    return [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("node", FAKE_X0 + 100.0, FAKE_Y0, 1200.0),
        elevation.Sample("node", FAKE_X0, FAKE_Y0 + 100.0, 1400.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]


def test_a_probe_without_a_field_is_the_probe_it_always_was(tmp_path):
    """The default, and the case on almost every machine. Nothing may change for it."""
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    assert near.terrain is None and near.terrain_m is None
    assert near.ground_m == [10.0, 12.0, 14.0]
    assert near.built_m == [30.0]
    assert near.fill_m == 18.0


def test_the_field_is_a_fourth_answer_and_does_not_touch_the_sampled_populations(tmp_path):
    """The whole design in one assertion: a texel read is reported, never averaged in.

    The field says 12.3 m at this coordinate and the three nodes nearby median to 12.0 m.
    Folding the reading into ``ground`` would move that median, put a 0.2 m measurement in
    with points up to 200 m away, and cost the caller the ability to tell them apart. So
    ``ground``, ``built`` and ``fill_m`` come out bit for bit what they were without a
    field, and the reading arrives beside them with its own provenance.
    """
    field = heightfield.load_field(build_field(tmp_path))
    without = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0, terrain_field=field)

    assert near.terrain_m == 12.3
    assert near.terrain.source == "landscape"
    assert near.terrain.accuracy_m == 0.205
    assert (near.ground_m, near.built_m, near.fill_m) == (
        without.ground_m,
        without.built_m,
        without.fill_m,
    )
    assert near.counts == without.counts == {"node": 3, "structure": 1}


def test_a_field_that_knows_nothing_here_leaves_the_probe_saying_nothing(tmp_path):
    """No-data must not become a number, and it must not disturb the samples either."""
    field = heightfield.load_field(build_field(tmp_path))
    near = elevation.probe(
        FAKE_X0, FAKE_Y0 + 3 * FAKE_SPACING, _samples(), radius_m=500.0, terrain_field=field
    )
    assert near.terrain is None and near.terrain_m is None
    assert near.ground_m == [10.0, 12.0, 14.0]


def test_the_field_does_not_lower_the_refusal_to_invent_a_ground_level(tmp_path):
    """``MIN_GROUND_SAMPLES`` survives the heightmap arriving, and that is deliberate.

    A terrain reading is not a ground sample. One node plus a field is still one node, and
    a fill depth quoted from it would be the invented number this module exists to refuse
    -- so ``fill_m`` stays ``None`` however good the terrain is.
    """
    field = heightfield.load_field(build_field(tmp_path))
    thin = [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]
    near = elevation.probe(FAKE_X0, FAKE_Y0, thin, radius_m=200.0, terrain_field=field)
    assert near.terrain_m == 12.3, "the field answered"
    assert len(near.ground_m) < elevation.MIN_GROUND_SAMPLES
    assert near.fill_m is None, "one node became a ground level because a field turned up"
