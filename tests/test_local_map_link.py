"""``show_on_map``'s local link: this project's own map, on the configured port, in the
fragment ``fragment.ts`` reads (``world``, ``z``, ``c`` in metres)."""

from __future__ import annotations

from urllib.parse import urlsplit

from satisfactory_mcp import config
from satisfactory_mcp.domain.spatial import maplink
from satisfactory_mcp.interfaces.mcp.tools import spatial


def _fragment(url: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in urlsplit(url).fragment.split("&"))


def test_the_default_is_the_web_server_default(monkeypatch):
    monkeypatch.delenv("SATISFACTORY_WEB_PORT", raising=False)
    assert (
        maplink.local_map_url(0.0, 0.0) == f"http://{config.WEB_HOST}:{config.WEB_PORT}/#z=1&c=0,0"
    )


def test_the_link_follows_the_configured_port(monkeypatch):
    monkeypatch.setenv("SATISFACTORY_WEB_PORT", "8799")
    assert maplink.local_map_url(1.5, -2.0, world="W").startswith("http://127.0.0.1:8799/#")
    monkeypatch.setenv("SATISFACTORY_WEB_PORT", "not a port")
    assert config.web_port() == config.WEB_PORT


def test_the_web_server_binds_what_the_links_name(monkeypatch):
    from satisfactory_mcp.interfaces.web import __main__ as web_main

    seen = {}
    monkeypatch.setenv("SATISFACTORY_WEB_PORT", "8798")
    monkeypatch.setattr(
        web_main.uvicorn, "run", lambda app, host, port: seen.update(host=host, port=port)
    )
    web_main.main([])
    assert seen == {"host": "127.0.0.1", "port": 8798}
    assert config.web_url() == "http://127.0.0.1:8798/"


def test_show_on_map_leads_with_the_local_map(monkeypatch):
    monkeypatch.setenv("SATISFACTORY_WEB_PORT", "8797")

    def no_save(*_a, **_k):
        raise RuntimeError("no save in this test")

    monkeypatch.setattr(spatial, "_state", no_save)
    out = spatial.show_on_map("120,-340")
    first = next(line for line in out.splitlines() if "://" in line)
    assert first.startswith("local map: http://127.0.0.1:8797/#")
    frag = _fragment(first.split(": ", 1)[1])
    assert frag["c"] == "120,-340"
    assert "z" in frag


def test_the_echo_rounds_to_a_decimetre_rather_than_truncating(monkeypatch):
    def no_save(*_a, **_k):
        raise RuntimeError("no save in this test")

    monkeypatch.setattr(spatial, "_state", no_save)
    assert "at -7.9,-5.5 (metres)" in spatial.show_on_map("-7.9,-5.5").splitlines()[0]


def test_a_show_selector_rides_in_the_fragment():
    frag = _fragment(maplink.local_map_url(1.0, 2.0, show="label:steel factory"))
    assert frag["show"] == "label:steel%20factory"
    assert "show" not in _fragment(maplink.local_map_url(1.0, 2.0))


def _local(out: str) -> dict[str, str]:
    line = next(line for line in out.splitlines() if line.startswith("local map:"))
    return _fragment(line.split(": ", 1)[1])


def test_a_node_link_rings_that_node(monkeypatch):
    def no_save(*_a, **_k):
        raise RuntimeError("no save in this test")

    monkeypatch.setattr(spatial, "_state", no_save)
    assert _local(spatial.show_on_map("node:BP_FrackingSatellite10"))["show"] == (
        "node:BP_FrackingSatellite10"
    )
    assert "show" not in _local(spatial.show_on_map("120,-340"))


def test_a_run_link_rings_that_run(monkeypatch, state):
    monkeypatch.setattr(spatial, "_state", lambda save=None, world=None, as_of=None: state)
    assert _local(spatial.show_on_map("chain:7"))["show"] == "chain:7"
    assert _local(spatial.show_on_map("PIPE:3"))["show"] == "pipe:3"
    assert "show" not in _local(spatial.show_on_map("me"))


def test_show_ref_is_what_the_fragment_carries():
    assert (
        _fragment(maplink.local_map_url(0, 0, show=maplink.show_ref(run="chain:7")))["show"]
        == "chain:7"
    )
