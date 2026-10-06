"""``/api/settings`` and the ``settings`` event (docs/shared-settings.md §3 and §4)."""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp import config
from satisfactory_mcp.domain import settings
from satisfactory_mcp.domain.planning.planlog import Actor
from satisfactory_mcp.interfaces.web.watch import KIND_SETTINGS, KINDS, SaveWatcher
from tests.support.web import PAGE_ORIGIN

EVIL = {"origin": "http://evil.example"}


def _patch(stateless_client, values, **extra):
    return stateless_client.patch(
        "/api/settings", json={"values": values, **extra}, headers=PAGE_ORIGIN
    )


def test_get_sends_every_default_before_any_write(stateless_client):
    assert stateless_client.get("/api/settings").json() == {
        "version": 0,
        "values": {
            "stage_headroom": "measured",
            "biomass": False,
            "payback_hours": 0.0,
            "overclock_last": False,
            "site_snap": "fine",
            "advice_box_fed": False,
        },
        "stored": [],
        "updated": None,
        "by": None,
    }


def test_a_write_is_the_new_state_stamped_as_the_page(stateless_client):
    reply = _patch(stateless_client, {"stage_headroom": "nameplate"}, version=0)
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["version"] == 1 and body["values"]["stage_headroom"] == "nameplate"
    assert body["by"]["kind"] == "page" and body["by"]["display"] == "page"
    assert settings.value("stage_headroom") == "nameplate"
    assert stateless_client.get("/api/settings").json() == body


def test_a_write_from_another_origin_is_refused(stateless_client):
    reply = stateless_client.patch(
        "/api/settings", json={"values": {"biomass": True}}, headers=EVIL
    )
    assert reply.status_code == 403
    assert not config.settings_path().exists()


def test_a_stale_version_is_a_typed_409_with_the_state_as_it_stands(stateless_client):
    settings.write({"biomass": True}, Actor("chat", "claude-code", 9))
    reply = _patch(stateless_client, {"stage_headroom": "nameplate"}, version=0)
    assert reply.status_code == 409
    body = reply.json()
    assert body["stale"] is True and body["settings"]["version"] == 1
    assert body["settings"]["values"]["biomass"] is True
    assert body["settings"]["by"]["display"] == "Claude Code"
    assert settings.value("stage_headroom") == "measured"


def test_only_unset_is_the_one_time_adoption(stateless_client):
    assert (
        _patch(stateless_client, {"biomass": True}, only_unset=True).json()["values"]["biomass"]
        is True
    )
    _patch(stateless_client, {"biomass": False})
    reply = _patch(stateless_client, {"biomass": True}, only_unset=True).json()
    assert reply["values"]["biomass"] is False and reply["version"] == 2


def test_null_clears_one_setting(stateless_client):
    _patch(stateless_client, {"stage_headroom": "nameplate"})
    body = _patch(stateless_client, {"stage_headroom": None}).json()
    assert body["values"]["stage_headroom"] == "measured" and body["stored"] == []


def test_a_value_outside_the_schema_is_refused(stateless_client):
    assert _patch(stateless_client, {"stage_headroom": "guess"}).status_code == 422
    assert not config.settings_path().exists()


def test_a_newer_settings_file_is_a_503_naming_the_settings(stateless_client):
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": settings.SCHEMA + 1}), encoding="utf-8")
    for reply in (
        stateless_client.get("/api/settings"),
        _patch(stateless_client, {"biomass": True}),
    ):
        assert reply.status_code == 503
        assert reply.json()["newer_schema"] is True and "settings" in reply.json()["error"]
        assert str(path) not in reply.json()["error"]


def test_the_watcher_announces_a_settings_write_with_its_state():
    watcher = SaveWatcher()
    assert watcher.settings_scan() == []
    settings.write({"stage_headroom": "nameplate"}, Actor("chat", "claude-code", 3))
    [event] = watcher.settings_scan()
    assert event.kind == KIND_SETTINGS and KINDS[-2] == KIND_SETTINGS
    data = event.as_dict()
    assert data["version"] == 1 and data["values"]["stage_headroom"] == "nameplate"
    assert data["by"]["kind"] == "chat"
    assert watcher.settings_scan() == []


def test_the_watchers_first_look_announces_nothing_old():
    settings.write({"biomass": True}, Actor("page", "", 1))
    watcher = SaveWatcher()
    assert watcher.settings_scan() == []
    settings.write({"biomass": False}, Actor("page", "", 1))
    assert [e.as_dict()["values"]["biomass"] for e in watcher.settings_scan()] == [False]
