"""``domain/settings.py`` (docs/shared-settings.md): the store, its version and its refusals."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.schema import NewerSchema
from satisfactory_mcp.domain import settings
from satisfactory_mcp.domain.planning.planlog import Actor

PAGE = Actor("page", "", 1)
CHAT = Actor("chat", "claude-code", 2)


def test_an_absent_file_reads_every_default():
    view = settings.read()
    assert view == {
        "version": 0,
        "values": {
            "stage_headroom": "measured",
            "biomass": False,
            "payback_hours": 0.0,
            "overclock_last": False,
            "advice_box_fed": False,
        },
        "stored": [],
        "updated": None,
        "by": None,
    }


def test_a_write_bumps_the_version_and_names_its_writer():
    view = settings.write({"stage_headroom": "nameplate"}, PAGE)
    assert view["version"] == 1 and view["values"]["stage_headroom"] == "nameplate"
    assert view["stored"] == ["stage_headroom"] and view["by"]["kind"] == "page"
    assert settings.read() == view
    raw = json.loads(config.settings_path().read_text(encoding="utf-8"))
    assert raw["schema"] == settings.SCHEMA and raw["values"] == {"stage_headroom": "nameplate"}


def test_writing_the_same_value_writes_nothing():
    settings.write({"biomass": True}, PAGE)
    assert settings.write({"biomass": True}, CHAT)["version"] == 1
    assert settings.read()["by"]["kind"] == "page"


def test_null_puts_a_setting_back_to_its_default():
    settings.write({"biomass": True}, PAGE)
    view = settings.write({"biomass": None}, PAGE)
    assert view["values"]["biomass"] is False and view["stored"] == [] and view["version"] == 2


def test_a_stale_version_is_refused_and_nothing_is_written():
    settings.write({"biomass": True}, CHAT)
    with pytest.raises(settings.SettingsStale) as caught:
        settings.write({"stage_headroom": "nameplate"}, PAGE, version=0)
    assert caught.value.current["version"] == 1
    assert settings.read()["values"]["stage_headroom"] == "measured"
    assert settings.write({"stage_headroom": "nameplate"}, PAGE, version=1)["version"] == 2


def test_only_unset_adopts_a_value_once_and_the_server_wins_after():
    first = settings.write({"stage_headroom": "nameplate"}, PAGE, only_unset=True)
    assert first["values"]["stage_headroom"] == "nameplate"
    settings.write({"stage_headroom": "measured"}, CHAT)
    again = settings.write({"stage_headroom": "nameplate"}, PAGE, only_unset=True)
    assert again["values"]["stage_headroom"] == "measured" and again["version"] == 2


@pytest.mark.parametrize(
    "change",
    [{"stage_headroom": "guess"}, {"biomass": "yes"}, {"biomass": 1}, {"nope": True}],
)
def test_a_bad_value_or_key_is_refused_before_anything_is_written(change):
    with pytest.raises(settings.SettingsError):
        settings.write(change, PAGE)
    assert not config.settings_path().exists()


def test_a_number_setting_is_checked_against_its_bounds(monkeypatch):
    spec = settings.Spec("number", 0.5, "a weight", low=0.0, high=1.0)
    monkeypatch.setitem(settings.SPECS, "weight", spec)
    assert settings.read()["values"]["weight"] == 0.5
    assert settings.write({"weight": 1}, PAGE)["values"]["weight"] == 1.0
    for bad in (1.5, True, "0.3"):
        with pytest.raises(settings.SettingsError):
            settings.write({"weight": bad}, PAGE)


def test_a_newer_schema_is_refused_for_reads_and_writes():
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": settings.SCHEMA + 1, "values": {}}), encoding="utf-8")
    with pytest.raises(NewerSchema):
        settings.read()
    with pytest.raises(NewerSchema):
        settings.write({"biomass": True}, PAGE)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == settings.SCHEMA + 1


def test_an_unknown_or_invalid_stored_value_reads_as_default_and_survives_a_write():
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    raw = {"schema": 1, "version": 4, "values": {"biomass": "maybe", "later": 3}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    view = settings.read()
    assert view["values"]["biomass"] is False and view["stored"] == []
    settings.write({"stage_headroom": "nameplate"}, PAGE)
    kept = json.loads(path.read_text(encoding="utf-8"))["values"]
    assert kept["later"] == 3 and kept["stage_headroom"] == "nameplate"
