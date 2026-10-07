"""The map registry: adopting the folders already on disk where they lie, and every edit to it.

The ``local`` fixture (``tests.support.map_jobs``) builds a real ``data/local``'s shape under
``tmp_path``. Nothing here reads the developer's own maps.
"""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp.domain.maps import registry
from tests.support.map_jobs import render_meta, write_pyramid


def test_the_folders_on_disk_are_adopted_in_place_with_the_old_ids_kept(local):
    data = registry.read()
    dirs = {ident: entry["dir"] for ident, entry in data["types"].items()}
    assert dirs == {
        "map": ".",
        "terrain": "renders/terrain",
        "satellite": "renders/satellite",
        "terrain-r3-502094": "renders-v1/terrain",
        "satellite-r3-502094": "renders-v1/satellite",
        "terrain-r4-502094": "renders-v2/terrain",
        "satellite-r4-502094": "renders-v2/satellite",
    }
    # renders-v3 is what the renders link points at: one folder, one type, under its old id.
    assert data["default"] == "map"
    assert all(entry["origin"] == "adopted" for entry in data["types"].values())
    assert all(entry["in_switcher"] for entry in data["types"].values())
    # Reading never writes: the manifest appears when the server starts, or on a change.
    assert not registry.manifest_path().exists()
    assert registry.ensure() and registry.manifest_path().exists()
    assert registry.read()["version"] == 1
    assert registry.ensure() == []
    # Nothing moved: every adopted folder is still where it was.
    for rel in ("tiles", "renders/terrain/tiles", "renders-v1/terrain/tiles"):
        assert (local / rel).is_dir()


def test_the_view_names_sorts_and_judges_each_type(local):
    rows = {row["id"]: row for row in registry.view()["types"]}
    assert rows["terrain"]["name"] == "terrain · PCHIP r5 · data 502094/hf v5"
    assert rows["terrain-r3-502094"]["freshness"]["stale"] == [
        {"axis": "heightfield", "text": "newer heightfield (v3 → v5)"}
    ]
    assert rows["terrain-r4-502094"]["freshness"]["stale"] == []
    assert rows["terrain-r4-502094"]["freshness"]["rerender"]["label"] == "river splines r7"
    assert rows["terrain"]["freshness"]["rerender"]["label"] == "river splines r7"
    order = [row["id"] for row in registry.view()["types"]]
    assert (
        order.index("terrain") < order.index("terrain-r4-502094") < order.index("terrain-r3-502094")
    )


def test_a_tile_lookup_never_leaves_data_local(local):
    registry.ensure()
    path = registry.manifest_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["types"]["escape"] = {**data["types"]["terrain"], "dir": "../../.."}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert registry.directory("escape") is None
    assert registry.directory("terrain") == local / "renders" / "terrain"
    assert registry.directory("nonsense") is None


def test_labels_switcher_and_default_are_edits_with_a_version(local):
    registry.ensure()
    data = registry.update("terrain-r4-502094", label="old two-regime", version=1)
    assert data["types"]["terrain-r4-502094"]["label"] == "old two-regime"
    with pytest.raises(registry.MapsStale):
        registry.update("terrain-r4-502094", in_switcher=False, version=1)
    registry.update("terrain-r4-502094", in_switcher=False)
    registry.set_default("terrain")
    assert registry.read()["default"] == "terrain"
    registry.set_default("plain")
    assert registry.read()["default"] == "plain"
    with pytest.raises(registry.MapsUnknown):
        registry.set_default("nonsense")
    registry.update("terrain-r4-502094", label="")
    assert registry.read()["types"]["terrain-r4-502094"]["label"] is None


def test_delete_refuses_the_default_and_a_busy_type_and_moves_the_rest_to_the_trash(local):
    registry.ensure()
    with pytest.raises(registry.MapsRefused, match="pick another default first"):
        registry.delete("map")
    with pytest.raises(registry.MapsRefused, match="cancel it first"):
        registry.delete("terrain-r3-502094", busy=frozenset({"terrain-r3-502094"}))
    freed = registry.delete("terrain-r3-502094")
    assert freed == 1000
    assert not (local / "renders-v1" / "terrain").exists()
    assert (local / "renders-v1" / "satellite").is_dir()
    trashed = list((local / "maps" / "_trash").iterdir())
    assert len(trashed) == 1 and (trashed[0] / "tiles").is_dir()
    assert "terrain-r3-502094" not in registry.read()["types"]
    registry.purge_trash()
    assert not any((local / "maps" / "_trash").iterdir())


def test_adopt_finds_a_pyramid_that_appeared_after_the_manifest(local):
    registry.ensure()
    write_pyramid(local / "renders-v4" / "terrain", "meta.json", render_meta("terrain", 2, 5))
    assert registry.unregistered() == ["renders-v4/terrain"]
    assert registry.adopt_existing() == ["terrain-r2-502094"]
    assert registry.unregistered() == []


def test_a_job_registers_building_types_that_are_not_served_until_finished(local):
    registry.ensure()
    entry = registry.new_entry("terrain-r5-502094-2", "render", "terrain", "maps/j1/terrain",
                               "meta.json", "generated")  # fmt: skip
    entry["status"] = "building"
    registry.register({"terrain-r5-502094-2": entry})
    assert registry.directory("terrain-r5-502094-2") is None
    assert registry.finish("terrain-r5-502094-2", "j1") is False
    write_pyramid(local / "maps" / "j1" / "terrain", "meta.json", render_meta("terrain", 5, 5, 7))
    assert registry.finish("terrain-r5-502094-2", "j1") is True
    assert registry.directory("terrain-r5-502094-2") == local / "maps" / "j1" / "terrain"
    assert registry.read()["types"]["terrain-r5-502094-2"]["bytes"] == 7
    registry.discard("terrain-r5-502094-2")
    assert not (local / "maps" / "j1" / "terrain").exists()


def test_a_newer_manifest_is_refused_and_left_alone(local):
    registry.manifest_path().parent.mkdir(parents=True)
    registry.manifest_path().write_text(json.dumps({"schema": 99}), encoding="utf-8")
    from satisfactory_mcp.core.schema import NewerSchema

    with pytest.raises(NewerSchema):
        registry.read()


def test_an_emptied_set_folder_goes_and_a_linked_one_stays(local):
    registry.ensure()
    registry.set_default("plain")
    for ident in ("terrain-r3-502094", "satellite-r3-502094", "terrain", "satellite"):
        registry.delete(ident)
    assert not (local / "renders-v1").exists()
    assert (local / "renders").exists(), "the link to the newest set is never removed"
    assert (local / "renders-v2" / "terrain").is_dir()


def test_the_cache_counts_and_frees_only_the_bytes_it_holds_alone(local):
    """A kept light's tiles are hard links to its map's: the map holds them, not the cache."""
    kept = registry.maps_dir() / registry.CACHE_DIR_NAME / "2048" / "light.kept"
    (kept / "tiles").mkdir(parents=True)
    (kept / "terms.npy").write_bytes(b"t" * 300)
    tile = local / "maps" / "j1" / "light" / "tiles" / "0.webp"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"w" * 50)
    (kept / "tiles" / "0.webp").hardlink_to(tile)
    assert registry.cache_bytes() == 300
    assert registry.clear_cache() == 300
    assert not kept.exists() and tile.read_bytes() == b"w" * 50
