"""Chat reads the map registry and never writes it: ``settings()`` lists the base map types and
``show_on_map(mode=)`` opens the local link on one (docs/maps_contract.md §7)."""

from __future__ import annotations

from urllib.parse import urlsplit

from test_map_registry import local  # noqa: F401  (the fixture)

from satisfactory_mcp import config
from satisfactory_mcp.domain.maps import registry
from satisfactory_mcp.interfaces.mcp.tools import settings as settings_tool
from satisfactory_mcp.interfaces.mcp.tools import spatial

REAL_DATA_DIR = config.data_dir


def _no_save(*_a, **_k):
    raise RuntimeError("no save in this test")


def _fragment(out: str) -> dict[str, str]:
    line = next(line for line in out.splitlines() if line.startswith("local map:"))
    frag = urlsplit(line.split(": ", 1)[1]).fragment
    return dict(part.split("=", 1) for part in frag.split("&"))


def test_settings_lists_the_base_maps_with_the_default_and_what_is_stale(local):  # noqa: F811
    out = settings_tool.settings()
    line = next(row for row in out.splitlines() if row.startswith("# base maps"))
    assert 'map "Game map ★" (default)' in line and "terrain-r4-502094 " in line
    assert '" -- stale: newer heightfield (v3 → v5)' in line
    assert "terrain-r3-502094 " in line
    assert not registry.manifest_path().exists(), "chat reads the registry and never writes it"


def test_show_on_map_opens_the_local_link_on_a_named_base_map(local, monkeypatch):  # noqa: F811
    monkeypatch.setattr(spatial, "_state", _no_save)
    # The node table is read from the real data dir; only the maps are the scratch tree's.
    monkeypatch.setattr(registry, "local_dir", lambda: local)
    monkeypatch.setattr(config, "data_dir", REAL_DATA_DIR)
    frag = _fragment(spatial.show_on_map("120,-340", mode="terrain-r4-502094"))
    assert frag["mode"] == "terrain-r4-502094"
    assert list(frag).index("mode") < list(frag).index("z"), "writeHash's key order"
    assert _fragment(spatial.show_on_map("120,-340", mode="artwork"))["mode"] == "map"
    assert _fragment(spatial.show_on_map("120,-340", mode="plain"))["mode"] == "plain"
    assert "mode" not in _fragment(spatial.show_on_map("120,-340"))
    refused = spatial.show_on_map("120,-340", mode="terrain-r9-1")
    assert refused.startswith("! no base map") and "terrain-r4-502094" in refused
