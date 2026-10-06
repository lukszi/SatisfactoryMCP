"""The map link a collectibles answer carries, and the three states its dataset can be in.

``maplink.COLLECTIBLES`` was dead code for as long as it was keyed by names -- ``hard_drives``,
``slugs_green`` -- that no other module used. The placement table calls those things
``crashed_drop_pod`` and ``power_slug_blue``, so nothing could ever have joined the two, and
"where are the hard drives?" got a map link from neither surface.

These tests need neither the game install nor a save: the placement table is a payload written
into ``tmp_path``, and the projection is the committed fixture. What they cannot check from
Python is that the page honours the fragment, so the last test holds the key the link writes
against the two places the page reads it.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.collectibles import table as collectibles_table
from satisfactory_mcp.domain.collectibles.service import collect_view
from satisfactory_mcp.domain.spatial import maplink
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.presenters.text.collectibles import render_collectibles
from tests.support.paths import REPO_ROOT

FRONTEND = REPO_ROOT / "src" / "satisfactory_mcp" / "interfaces" / "web" / "frontend" / "src"

#: Two categories and four placements, which is enough for every question these tests ask: one
#: the public map has a layer for and can be collected, one it has none for, and a pedestal to
#: check drops out of an unfiltered link. Coordinates are the table's own centimetres.
TABLE_PAYLOAD = {
    "collectibles": [
        {
            "cell": "c1",
            "instance": "Persistent_Level:PersistentLevel.BP_DropPod_1",
            "category": "crashed_drop_pod",
            "class": "BP_DropPod_C",
            "x": 100000.0,
            "y": 0.0,
            "z": 500.0,
            "state": "present",
        },
        {
            "cell": "c2",
            "instance": "Persistent_Level:PersistentLevel.BP_DropPod_2",
            "category": "crashed_drop_pod",
            "class": "BP_DropPod_C",
            "x": -100000.0,
            "y": 40000.0,
            "z": 500.0,
            "state": "unknown",
        },
        {
            "cell": "c3",
            "instance": "Persistent_Level:PersistentLevel.BP_Mushroom_1",
            "category": "mushroom",
            "class": "BP_Mushroom_C",
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "state": "present",
        },
        {
            "cell": "c4",
            "instance": "Persistent_Level:PersistentLevel.BP_Shrine_1",
            "category": "mercer_shrine",
            "class": "BP_Shrine_C",
            "x": 700000.0,
            "y": 700000.0,
            "z": 0.0,
            "state": "present",
        },
    ],
    "_meta": {
        "totals": {
            "by_category": {
                "crashed_drop_pod": {"rows_any_save_mentions": 2},
                "mushroom": {"rows_any_save_mentions": 1},
                "mercer_shrine": {"rows_any_save_mentions": 1},
            },
            "pedestals": {"mercer_shrine": {"parent_category": {"mercer_sphere": 1}}},
        }
    },
}


def _install(tmp_path, monkeypatch, payload) -> None:
    """Make ``payload`` the placement table this process reads, or ``None`` to remove it."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    collectibles_table._TABLE.clear()
    path = tmp_path / collectibles_table.COLLECTIBLES_FILE
    if payload is None:
        return
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


@pytest.fixture
def world(projection) -> WorldState:
    """The committed projection with no game data behind it.

    ``game`` is only reached to name an item a placement holds, and no placement in
    ``TABLE_PAYLOAD`` holds one -- which is what keeps these tests out of the integration half
    of the suite, where every other collectibles test lives.
    """
    return WorldState(projection=projection, game=None)


def _answer(world, tmp_path, monkeypatch, payload=TABLE_PAYLOAD, **kwargs) -> str:
    _install(tmp_path, monkeypatch, payload)
    view = collect_view(
        world, kwargs.get("group"), kwargs.get("view", "census"), kwargs.get("near")
    )
    return render_collectibles(world, view, limit=25)


def _line(out: str, prefix: str) -> str:
    found = [line for line in out.splitlines() if line.startswith(prefix)]
    assert found, f"no {prefix!r} line in:\n{out}"
    return found[0]


# ------------------------------------------------------- the two vocabularies join


def test_the_site_tokens_are_keyed_by_the_placement_tables_own_category_names():
    """The defect that made the constant dead: its keys were names nothing else used.

    The VALUES are unchanged and must stay so -- they were read off the interactive map page
    itself, and one wrong token opens the map correctly with the overlay silently missing.
    """
    assert set(maplink.COLLECTIBLES.values()) == {
        "greenSlugs",
        "yellowSlugs",
        "purpleSlugs",
        "hardDrives",
        "mercerSpheres",
        "somersloops",
    }
    assert maplink.COLLECTIBLES["crashed_drop_pod"] == "hardDrives"
    assert maplink.COLLECTIBLES["power_slug_blue"] == "greenSlugs"


def test_every_key_is_a_category_the_generated_table_actually_places():
    """The ratchet against the keys drifting off again, run against the real dataset.

    Untracked, so a fresh clone skips -- and the skip is honest: a failed lookup here would
    say the categories had been renamed when in fact the file was never generated.
    """
    real = config.data_dir() / collectibles_table.COLLECTIBLES_FILE
    if not real.is_file():
        pytest.skip("needs data/world_collectibles.json (tools/gen_world_collectibles.py)")
    placed = {
        row["category"] for row in json.loads(real.read_text(encoding="utf-8"))["collectibles"]
    }
    unknown = sorted(set(maplink.COLLECTIBLES) - placed)
    assert not unknown, (
        f"{unknown} name no category the map places, so no answer can ever reach these "
        f"tokens. The table's categories are: {sorted(placed)}"
    )


def test_a_category_the_site_has_no_layer_for_contributes_nothing():
    """Mushrooms, loot caches and both shrines are on our map and not on theirs. A token
    spelt from the name would open the map with the overlay silently missing."""
    assert maplink.collectible_layers(["mushroom", "loot_cache"]) == []
    assert maplink.collectible_layers(["crashed_drop_pod", "mushroom"]) == ["hardDrives"]


def test_the_local_link_carries_the_pickup_rows_and_nothing_carries_them_by_default():
    """Every ``pickup:`` row on the page starts unticked, so a link that omits them opens the
    map with nothing of what it is about drawn on it."""
    url = maplink.local_map_url(1.0, 2.0, pickups=["crashed_drop_pod", "power_slug_blue"])
    assert "pickups=crashed_drop_pod,power_slug_blue" in url
    assert maplink.local_map_url(1.0, 2.0) == "http://127.0.0.1:8712/#z=1&c=1,2"


# --------------------------------------------------------------- the answers carry it


def test_a_hard_drive_answer_carries_a_local_link_with_the_pod_layer_on(
    world, tmp_path, monkeypatch
):
    out = _answer(world, tmp_path, monkeypatch, group="crashed_drop_pod", view="remaining")
    local = _line(out, "local map: ")
    assert maplink.local_base() + "#" in local
    assert "pickups=crashed_drop_pod" in local
    assert f"z={maplink.LOCAL_WORLD_ZOOM}" in local, "a layer link has to open far enough out"
    assert "hardDrives" in _line(out, "public map: ")


def test_the_local_link_leads_because_it_is_the_only_one_that_knows_this_save(
    world, tmp_path, monkeypatch
):
    """Backlog item 2's rule, now applying to the collectibles surface too."""
    out = _answer(world, tmp_path, monkeypatch, group="crashed_drop_pod", view="remaining")
    assert out.index("local map:") < out.index("public map:")


def test_the_census_links_every_category_it_counted_and_drops_the_pedestals(
    world, tmp_path, monkeypatch
):
    """A shrine is the base its artifact stands on, so its layer would draw a second marker a
    metre from the sphere -- the double count the census warns about, in layer form."""
    local = _line(_answer(world, tmp_path, monkeypatch), "local map: ")
    assert "pickups=crashed_drop_pod,mushroom" in local
    assert "mercer_shrine" not in local


def test_asking_by_group_gets_that_one_layer_and_not_the_rest(world, tmp_path, monkeypatch):
    local = _line(_answer(world, tmp_path, monkeypatch, group="mushroom"), "local map: ")
    assert "pickups=mushroom&" in local
    assert "crashed_drop_pod" not in local


def test_the_site_link_is_still_offered_where_it_has_no_layer_and_says_so(
    world, tmp_path, monkeypatch
):
    out = _answer(world, tmp_path, monkeypatch, group="mushroom")
    assert "public map: " in out
    assert "no collectible layer for mushroom" in out


def test_a_collected_listing_says_the_layer_shows_what_is_left_instead(
    world, tmp_path, monkeypatch
):
    """The one mode whose link answers a different question than the rows do. The page's
    pickup layers are drawn from ``show=remaining``, so a collected placement is not on them."""
    out = _answer(world, tmp_path, monkeypatch, group="crashed_drop_pod", view="collected")
    assert "local map: " in out
    assert "the local map's layer is what is LEFT" in out


def test_a_pod_answer_says_which_of_the_two_maps_can_tell_a_looted_pod_from_a_full_one(
    world, tmp_path, monkeypatch
):
    """A looted pod stays standing and stays remaining, so a pod is not a hard drive on either
    map -- but only one of them still has to apologise. The local layer reads ``looted`` and
    draws the three apart; the public one draws the vanilla placement list and cannot."""
    out = _answer(world, tmp_path, monkeypatch, group="crashed_drop_pod", view="remaining")
    assert "hollow ring" in out
    assert "public map" in out and "alike" in out
    assert "carries no" not in out


# ------------------------------------------------------ and the two dataset failures


def test_a_dataset_that_was_never_generated_offers_no_link_at_all(world, tmp_path, monkeypatch):
    """A link would open a layer that is empty because nothing was generated, which on a map
    reads as "you have taken them all"."""
    out = _answer(world, tmp_path, monkeypatch, payload=None)
    assert "local map:" not in out and "public map:" not in out
    assert "gen_world_collectibles.py" in out
    assert "no map link" in out


def test_a_dataset_that_will_not_parse_says_corrupt_and_offers_no_link(
    world, tmp_path, monkeypatch
):
    """The two failures stay tellable apart: "you never ran the generator" and "what it wrote
    is broken" have different remedies, and only the second one starts with deleting a file."""
    out = _answer(world, tmp_path, monkeypatch, payload='{"collectibles": [')
    assert "CORRUPT, not missing" in out
    assert "local map:" not in out and "public map:" not in out


def test_the_two_failures_do_not_say_the_same_thing(world, tmp_path, monkeypatch):
    missing = _answer(world, tmp_path, monkeypatch, payload=None)
    corrupt = _answer(world, tmp_path, monkeypatch, payload='{"collectibles": [')
    assert "CORRUPT" not in missing
    assert "DEGRADED" not in corrupt


# ------------------------------------------------------------- and the page reads it


def test_the_fragment_key_the_link_writes_is_the_one_the_page_reads():
    """The join no compiler and no test on either side of it can see.

    ``local_map_url`` writes ``pickups=`` into a fragment that a TypeScript file parses. Rename
    it on one side and every link this repository emits still opens the map, in the right
    place, with the layer it was about switched off -- and nothing anywhere fails.
    """
    written = maplink.local_map_url(0, 0, pickups=["crashed_drop_pod"])
    key = written.split("#")[1].split("=")[0]
    assert key == "pickups"

    reader = (FRONTEND / "app" / "fragment.ts").read_text(encoding="utf-8")
    writer = (FRONTEND / "map" / "map.ts").read_text(encoding="utf-8")
    assert f"asked.{key}" in reader, f"fragment.ts does not read {key}= out of the fragment"
    assert f'"{key}="' in writer, f"writeHash does not put {key}= back into the address bar"


def test_the_page_tells_the_three_loot_states_apart():
    """The same kind of join, one field along: ``looted`` reaches the page as three values.

    Null is the trap. It means the flag was never read, not that the pod is full, so a page
    that tests truthiness alone draws a pod nobody has ever streamed in exactly like one with
    a drive still in it -- and the category name is the other half, because null on a mushroom
    is not a claim about a mushroom at all.
    """
    drawing = (FRONTEND / "map" / "drawn" / "markers.ts").read_text(encoding="utf-8")
    assert 'POD_CATEGORY = "crashed_drop_pod"' in drawing, "the page spells the category itself"
    assert "r.looted === true" in drawing, "markers.ts does not single out a looted pod"
    assert "r.looted === null" in drawing, "markers.ts draws an unread flag as a full pod"
