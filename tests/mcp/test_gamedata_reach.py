"""Everything in the dump reachable, and a recipe findable by the name it prints under.

Three small refusals that each cost a caller a round trip or a wrong answer:
`list_buildings(building_kind="all")` matched nothing; the AWESOME Sink and both Pipeline Pumps
fell through every `kind`, so sink draw and pump head could not be checked against data
and got answered from general knowledge instead -- exactly the failure the rest of this
surface works to prevent; and `recipe_detail` refused a display name it could resolve.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from tests.support import tables

pytestmark = pytest.mark.integration


def test_all_reaches_every_building(game):
    """Paged now, so reachability means the header counts every building and walking
    the offsets visits that many rows -- not that one response carries them all."""
    first = srv.list_buildings(building_kind="all")
    total = int(first.split()[1])
    assert total > 400
    seen, offset = 0, 0
    while True:
        page = tables.data_lines(srv.list_buildings(building_kind="all", offset=offset))
        if not page:
            break
        seen += len(page)
        offset += len(page)
    assert seen == total


def test_the_first_call_is_the_same_answer_as_the_second(game):
    """Field report: a first call answered '0 all building(s)' and a later identical
    call answered 60k characters. The cause (building_kind='all' matching no filter branch) is
    long fixed; this pins the property that made it a report -- the answer must not
    depend on when it is asked."""
    assert srv.list_buildings(building_kind="all") == srv.list_buildings(building_kind="all")
    assert tables.data_lines(srv.list_buildings(building_kind="all"))


def test_all_is_paged_rather_than_a_context_eviction(game):
    """The 540-building table unpaged was ~60k characters -- past client token limits,
    so the honest full answer was still a failure. The page says what it is showing and
    how to get the rest, in the same envelope every other list tool uses."""
    out = srv.list_buildings(building_kind="all")
    assert len(out) < 5000
    assert len(tables.data_lines(out)) == 25
    assert "540 match(es), showing 25 from offset 0" in out
    assert "call again with offset=25" in out
    # And the offset genuinely advances: no shared rows between page one and page two.
    page_two = srv.list_buildings(building_kind="all", offset=25)
    assert not set(tables.data_lines(out)) & set(tables.data_lines(page_two))


def test_the_build_piece_families_are_kinds(game):
    """'foundation' was refused while 'all' was accepted, which made a foundation's
    size reachable only by paging the whole table. Each family is a kind now, and the
    rows carry the sizes item 3 was filed about."""
    for kind in ("foundation", "ramp", "wall", "pillar", "beam", "architecture"):
        out = srv.list_buildings(building_kind=kind)
        assert not out.startswith("! unknown kind"), kind
        assert tables.data_lines(out), kind
    # The exact piece the player had to measure in-game: Big Pillar Support, 8x8x4 m.
    pillars = srv.list_buildings(building_kind="pillar")
    row = next(line for line in tables.data_lines(pillars) if "Big Pillar Support" in line)
    assert "8x8x4m" in row


def test_an_unknown_building_kind_lists_the_kinds(game):
    out = srv.list_buildings(building_kind="bogus")
    assert out.startswith("! unknown building_kind")
    assert "logistics" in out and "all" in out


def test_the_sink_and_the_pumps_are_reachable(game):
    """The sink's 30 MW is the AWESOME_SINK_MW constant the optimizer charges, and pump
    head is what a fluid plan sizes risers with. Neither was listable."""
    everything = srv.list_buildings(building_kind="all")
    assert "AWESOME Sink" in everything
    logistics = srv.list_buildings(building_kind="logistics")
    assert "Pipeline Pump Mk.2" in logistics
    assert "lifts 50m head" in logistics


def test_recipe_detail_takes_a_display_name(game):
    out = srv.recipe_detail("Alternate: Heavy Oil Residue")
    assert "unknown recipe" not in out
    assert "Refinery" in out


def test_recipe_detail_takes_the_reported_name_in_any_case(game):
    """The exact string from the field report, and lower-cased: 'Recycled Plastic' is a
    substring of exactly one display name, so it resolves to that recipe without
    listing candidates or costing the round trip the report was about."""
    for spelling in ("Recycled Plastic", "recycled plastic"):
        out = srv.recipe_detail(spelling)
        assert "unknown recipe" not in out, spelling
        assert "matches" not in out.splitlines()[0], spelling
        assert "Refinery" in out


def test_an_ambiguous_name_lists_the_candidates(game):
    """Ambiguous is not unknown, and listing what matched beats sending the caller back
    to search."""
    out = srv.recipe_detail("Residual")
    assert "matches 3 recipes" in out
    assert "Residual Plastic" in out


def test_a_class_id_still_works(game):
    assert "unknown recipe" not in srv.recipe_detail("Recipe_Alternate_HeavyOilResidue_C")
