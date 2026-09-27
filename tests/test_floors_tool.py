"""``factory_floors``: the storeys of a factory, in text.

``domain.factories.floors`` is 773 lines recovering real floors from foundation geometry,
and until this tool the only way to see one was to open the map. What is pinned here is the
tool's contract rather than the decomposition's -- ``tests/test_floors.py`` owns that -- so
these check the counts it prints against the domain call behind them, the two views it
switches between, and the sentences a reader would otherwise get wrong.

Fixture-backed: ``_state`` is replaced on the tool module, so the numbers are the committed
projection's.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories import floors as ffloors
from satisfactory_mcp.interfaces.mcp.tools import floors as tool


@pytest.fixture
def tools(monkeypatch, labelled):
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: labelled)
    return tool


def _rows(out: str) -> list[list[str]]:
    lines = [ln for ln in out.splitlines() if not ln.startswith(("#", "!"))]
    return [ln.split("\t") for ln in lines[1:]]


def test_the_world_view_lists_platforms_largest_first_and_names_them(tools):
    """A platform carries the player's own factory label where one of its machines has one,
    which is what makes the index worth handing back."""
    rows = _rows(tools.factory_floors(limit=5))
    assert [int(r[4].replace(",", "")) for r in rows] == sorted(
        (int(r[4].replace(",", "")) for r in rows), reverse=True
    )
    assert rows[1][:3] == ["1", "tier 1&2", "6"], "six storeys on the main build"


def test_a_pour_with_no_floor_is_counted_rather_than_listed_or_dropped(tools):
    """62 of the 132 platforms are a tile under a power pole. Listing them would push the
    real factories off the page; dropping them silently would make the total a lie."""
    out = tools.factory_floors(limit=10)
    assert "132 platform(s), 93 floor(s)" in out
    assert "62 pour(s) carry no floor at all" in out
    assert "70 match(es)" in out, "the rows page over the platforms that have a floor"


def test_one_platform_is_answered_floor_by_floor(tools):
    """The question is "how many decks and what is on each", and the answer is the 12 m
    module this world is built on: smelters, then constructors, then assemblers."""
    out = tools.factory_floors(platform=1)
    rows = _rows(out)
    assert [r[0] for r in rows] == ["0", "1", "2", "3", "4", "5"]
    assert rows[1][1] == "30.2" and rows[2][1] == "42.2"
    assert rows[1][7] == "32x Smelter"
    assert "platform 1 (tier 1&2)" in out


def test_the_machines_it_prints_are_the_ones_the_domain_put_on_that_deck(tools, state):
    """The counts come off the bands, not off the placement list: a machine on the very
    edge of a deck is assigned to it and yet has its own 8 m cell on the platform next
    door, so the two differ by three on this world."""
    report = ffloors.floor_decomposition(state, platform=1)
    band = report.platforms[0]
    rows = _rows(tools.factory_floors(platform=1))
    assert [int(r[5]) for r in rows] == [len(b.machines) for b in band.bands]
    assert [int(r[6]) for r in rows] == [len(b.attachments) for b in band.bands]


def test_a_factory_name_narrows_to_the_platform_it_stands_on(tools):
    out = tools.factory_floors(factory="steel factory")
    assert "platform 6 (steel factory)" in out
    assert "factory 'steel factory'" in out
    assert next(r[7] for r in _rows(out)) == "32x Smelter"


def test_it_says_which_measurement_this_is_where_another_tool_disagrees(tools):
    """``factory_map show=slabs`` counts storeys as a slab's z span over 4 m, and its slabs
    weld through ramps -- a different number from a different method, and a reader holding
    both deserves to be told which is which rather than to discover it."""
    out = tools.factory_floors(limit=3)
    assert "z span divided by 4 m" in out
    assert "These bands are the measured decomposition" in out


def test_a_platform_that_does_not_exist_is_not_answered_with_an_empty_table(tools):
    assert "no platform matches platform 9999" in tools.factory_floors(platform=9999)


def test_a_bad_selector_gets_the_selector_grammar(tools):
    out = tools.factory_floors(factory="nonsuch")
    assert out.startswith("!")
    assert "product:<item>" in out


@pytest.mark.parametrize(
    "call", [lambda t: t.factory_floors(), lambda t: t.factory_floors(platform=1)]
)
def test_a_full_page_stays_inside_the_context_budget(tools, call):
    """132 platforms and 93 bands is exactly the table that grows past what a caller can
    afford. The ceiling is ``test_surface``'s."""
    out = call(tools)
    assert out.strip()
    assert len(out) < 4000, len(out)


def test_it_pages_on_the_offset_its_envelope_promises(tools):
    first = _rows(tools.factory_floors(limit=5))
    second = _rows(tools.factory_floors(limit=5, offset=5))
    assert len(first) == len(second) == 5
    assert not {r[0] for r in first} & {r[0] for r in second}
