"""``stock``, ``storage`` and ``crates``: what you own and where it is.

The projection has carried the container and crate tables since schemas 15 and 18, the map
read both, and no MCP tool read either -- so a client could be told "short 500 Quartz" with
no way to ask what was in the boxes. These pin the answer and, more importantly, the three
sentences it must never blur: spendable stock, material in machine buffers, and crates.

Fixture-backed rather than live: ``_state`` is replaced on the tool module, so every number
below is the committed projection's and moves only when that is re-cut.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.interfaces.mcp.tools import inventory as tool


@pytest.fixture
def tools(monkeypatch, state):
    """The tool module answering about the fixture world instead of the newest save."""
    monkeypatch.setattr(tool, "_state", lambda save=None, world=None, as_of=None: state)
    return tool


def _rows(out: str) -> list[list[str]]:
    """The data rows of the one table in a response: no ``#`` header, no ``!`` note."""
    lines = [ln for ln in out.splitlines() if not ln.startswith(("#", "!"))]
    return [ln.split("\t") for ln in lines[1:]]


# ------------------------------------------------------------------ stock


def test_stock_reports_each_pile_apart_and_adds_up_only_the_spendable_ones(tools, state):
    """The whole point of the tool. Concrete on the reference world is 1 carried + 59,440 in
    containers + 2,500 in the Depot = 61,941 spendable, with another 9,551 inside machines
    and 7 in a crate that are reported and NOT added -- which is what the affordability
    check has always done and never printed."""
    out = tools.stock(item="Concrete")
    row = _rows(out)[0]
    assert row[:6] == ["Concrete", "61941", "1", "59440", "2500", "9551"]
    assert row[6] == "7", "the crate column"
    assert state.stock()["Desc_Cement_C"] == pytest.approx(61941)


def test_machine_buffers_reach_a_reader_at_all(tools):
    """``machine_buffers()`` had no consumer anywhere in the tree."""
    buffers = [r for r in _rows(tools.stock(limit=25)) if r[5]]
    assert buffers, "no row printed anything in a machine buffer"
    assert "buffers" in tools.stock(limit=1), "and the column is named"


def test_what_is_spendable_is_stated_rather_than_left_to_be_inferred(tools):
    out = tools.stock(limit=1)
    assert "spendable = carried + storage + Dimensional Depot" in out
    assert "neither is spendable" in out


def test_an_unknown_item_is_refused_by_name(tools):
    assert tools.stock(item="Nonsuch") == "no item matches 'Nonsuch'"


def test_an_item_held_nowhere_says_so_in_all_five_places(tools):
    """A silent empty table would read as "the tool found nothing", which is a different
    claim from "you have none of this anywhere"."""
    out = tools.stock(item="Ficsonium")
    assert "you hold no Ficsonium anywhere" in out
    assert "machine buffer" in out and "crate" in out


def test_where_names_the_place_and_the_region_it_stands_in(tools):
    """The join item 20 asks for -- "where is my concrete": a container row, a region name
    from the same table the map paints with, and a coordinate to walk to."""
    rows = _rows(tools.stock(item="Concrete", where=True, limit=4))
    assert rows[0][:4] == ["24000", "Industrial Storage Container", "Rocky Desert", "-1089,-1245"]
    assert {r[4] for r in rows} <= {"storage", "crate(death)", "crate(dismantle)", "crate(none)"}


def test_where_says_out_loud_that_carried_and_depot_stock_has_no_row(tools):
    """2,500 Concrete in the Depot is real and stands nowhere; a place list that quietly
    omitted it would not add up to the total on the line above."""
    out = tools.stock(item="Concrete", where=True, limit=3)
    assert "spendable=61941" in out and "depot=2500" in out
    assert "stands nowhere on the map" in out


def test_stock_pages_rather_than_promising_an_offset_it_has_not_got(tools):
    """Fifteen tools on this surface print "call again with offset=N" and take no offset."""
    first = _rows(tools.stock(limit=5))
    second = _rows(tools.stock(limit=5, offset=5))
    assert len(first) == len(second) == 5
    assert not {r[0] for r in first} & {r[0] for r in second}
    assert "showing 5 from offset 5" in tools.stock(limit=5, offset=5)


# ---------------------------------------------------------------- storage


def test_storage_is_every_container_and_buffer_with_what_is_in_it(tools):
    """151 rows: 146 solid containers, 125 of them with something in, and 5 fluid buffers.
    The same three numbers ``/api/storage`` publishes, which is the point -- one projection
    key, two interfaces, one answer."""
    out = tools.storage(limit=4)
    assert "151 container(s): 146 solid (125 with something in), 5 fluid buffer(s)" in out
    assert "21 empty container(s) hidden" in out


def test_the_census_counts_the_world_even_when_the_rows_are_filtered(tools):
    """A header that moved with ``item=`` would answer "how many containers have I got"
    with the number holding concrete."""
    out = tools.storage(item="Concrete", limit=3)
    assert "151 container(s)" in out
    assert "8 shown, holding Concrete" in out, "crates are not containers"


def test_how_full_a_container_is_gets_measured_rather_than_left_to_the_reader(tools):
    """The web payload ships slots and a total; the fraction between them is the answer to
    "is this box backing up". A full Industrial Storage Container is 48 of 48 slots at its
    items' own stack sizes."""
    row = _rows(tools.storage(limit=1))[0]
    assert row[0] == "Industrial Storage Container"
    assert row[3] == "100%" and row[4] == "48/48"


def test_a_fluid_buffer_reports_m3_against_what_its_class_holds(tools):
    """1,730.6 is not a reading until it is put against the 2,400 an Industrial Fluid
    Buffer holds -- and its contents are named by the pipe network, not by the tank."""
    rows = _rows(tools.storage(container_kind="fluid", limit=5))
    assert rows[0][3] == "72%" and rows[0][4] == "1731/2400m3"
    assert "Crude Oil" in rows[0][5]


def test_an_unknown_container_kind_is_refused_with_the_choices(tools):
    assert "Choose from: solid, fluid" in tools.storage(container_kind="gas")


def test_empty_containers_are_hidden_by_default_and_reachable_on_request(tools):
    shown = len(_rows(tools.storage(limit=25)))
    assert "21 empty container(s) hidden -- pass empty=True" in tools.storage(limit=1)
    out = tools.storage(empty=True, limit=25)
    assert "151 shown" in out
    assert shown == 25, "the page is full either way; the census line carries the totals"


def test_near_narrows_to_one_base_and_says_what_it_narrowed_to(tools):
    """The question is "what is in the boxes HERE", and 151 containers spread over 4 km is
    not an answer to it."""
    out = tools.storage(near="-611,-1586", radius_m=100, limit=10)
    rows = _rows(out)
    assert rows and all(r[2].startswith("-6") for r in rows)
    assert "within 100m of -611,-1586" in out


def test_a_filter_that_matches_nothing_does_not_read_as_an_empty_world(tools):
    out = tools.storage(near="3000,3000", radius_m=50)
    assert not _rows(out)
    assert "widen radius_m" in out


# ----------------------------------------------------------------- crates


def test_crates_name_what_is_in_them_and_where_to_walk(tools):
    """The question schema 18 exists to answer. The reference world holds two, one of which
    knows what kind it is and one of which predates the game recording that."""
    out = tools.crates()
    assert "2 crate(s) on the ground, 0 from a death; 48 item(s) in them" in out
    rows = _rows(out)
    assert [r[0] for r in rows] == ["dismantle", "none"]
    assert rows[0][1:4] == ["No Man's Land", "-508,-2595", "-17"]
    assert "15 Iron Plate" in rows[0][6]


def test_a_crate_is_never_offered_as_spendable_stock(tools, state):
    """Schema 19 took crates OUT of spendable stock deliberately: a crate deletes itself
    when emptied, so a plan that spent it would depend on somebody walking back there."""
    assert "never spendable" in tools.crates()
    crated = state.inventory.breakdown()["Desc_Cement_C"]
    assert crated["crate"] == 7
    assert crated["spendable"] == pytest.approx(state.stock()["Desc_Cement_C"])
    assert crated["crate"] not in (crated["spendable"],)


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.stock(),
        lambda t: t.stock(where=True),
        lambda t: t.stock(item="Concrete", where=True),
        lambda t: t.storage(),
        lambda t: t.storage(container_kind="fluid"),
        lambda t: t.crates(),
    ],
)
def test_a_full_page_stays_inside_the_context_budget(tools, call):
    """Every response is read by a model, and 130 containers is exactly the sort of table
    that grows past what a caller can afford. The ceiling is ``test_surface``'s."""
    out = call(tools)
    assert out.strip()
    assert len(out) < 4000, len(out)


def test_the_unrecorded_kind_is_explained_rather_than_printed_bare(tools):
    """``none`` is the game's own CT_None: ``mCrateType`` arrived in build 433351, so a
    crate made before it carries no type and never will."""
    assert "predates the game's death/dismantle distinction" in tools.crates()
