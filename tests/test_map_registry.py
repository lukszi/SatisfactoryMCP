"""The map registry: adopting the folders already on disk where they lie, and every edit to it.

``config.data_dir`` points at ``tmp_path``, and the tree is built to the shape of a real
``data/local``: the artwork at the root, ``renders`` as a link to the newest render set, and
older sets beside it. Nothing here reads the developer's own maps.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.domain.maps import registry

PIN = "buildVersion 502094 (engine branch ++FactoryGame+rel-main), the installed build"


def pyramid(directory: Path, sidecar_name: str | None, sidecar: dict | None = None) -> None:
    (directory / "tiles" / "0").mkdir(parents=True, exist_ok=True)
    (directory / "tiles" / "0" / "0_0.png").write_bytes(b"\x89PNG")
    if sidecar_name is not None:
        (directory / sidecar_name).write_text(json.dumps(sidecar or {}), encoding="utf-8")


def render_meta(layer: str, recipe: int, hf: int, nbytes: int = 1000) -> dict:
    return {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": recipe,
            "sources": {"heightfield": {"generator_version": hf, "game_version_pinned": PIN}},
            "tiles": {"bytes": nbytes, "max_z": 7},
        }
    }


def link(target: Path, at: Path) -> None:
    """A directory junction on Windows, a symlink elsewhere: the shape ``renders`` has."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(at))
    else:
        at.symlink_to(target, target_is_directory=True)


@pytest.fixture
def local(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(registry, "game_cl", lambda: 502094)
    root = tmp_path / "local"
    root.mkdir()
    pyramid(
        root,
        "map.json",
        {"_meta": {"generator": "tools/gen_map_image.py",
                   "sources": {"map_slices": {"game_version_raw": {"Changelist": 502094}}},
                   "tiles": {"enhanced": True, "enhancement": {"recipe": 2}, "bytes": 50}}},
    )  # fmt: skip
    for layer in ("terrain", "satellite"):
        pyramid(root / "renders-v1" / layer, "meta.json", render_meta(layer, 3, 3))
        pyramid(root / "renders-v2" / layer, "meta.json", render_meta(layer, 4, 5))
        pyramid(root / "renders-v3" / layer, "meta.json", render_meta(layer, 5, 5))
    link(root / "renders-v3", root / "renders")
    (root / "heightmap").mkdir()
    (root / "heightmap" / "meta.json").write_text(
        json.dumps({"generator_version": 5, "sources": {"game": {"game_version_pinned": PIN}}}),
        encoding="utf-8",
    )
    pyramid(root / "renders-v2.incoming" / "terrain", "meta.json", render_meta("terrain", 4, 5))
    return root


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
    assert rows["terrain-r4-502094"]["freshness"]["rerender"]["label"] == "PCHIP r5"
    assert rows["terrain"]["freshness"]["rerender"] is None
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
    pyramid(local / "renders-v4" / "terrain", "meta.json", render_meta("terrain", 2, 5))
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
    pyramid(local / "maps" / "j1" / "terrain", "meta.json", render_meta("terrain", 5, 5, 7))
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
