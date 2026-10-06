"""Chat's side of the shared settings (docs/shared-settings.md §5): the ``settings`` tool, and
the tools whose defaults it holds."""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain import settings
from satisfactory_mcp.domain.planning.stored.planlog import Actor
from satisfactory_mcp.interfaces.mcp.tools import planning


class _Stop(Exception):
    pass


def test_the_tool_lists_every_setting_with_its_default():
    out = srv.settings()
    assert out.startswith("# shared settings v0")
    assert "stage_headroom\tmeasured (default)\tmeasured|nameplate" in out
    assert "biomass\toff (default)\ttrue|false" in out


def test_the_tool_writes_as_chat_and_says_when_nothing_moved():
    out = srv.settings(change={"stage_headroom": "nameplate"})
    assert out.startswith("# shared settings v1: changed") and "stage_headroom\tnameplate\t" in out
    assert "last changed by chat" in out
    assert settings.read()["by"]["kind"] == "chat"
    assert "nothing written" in srv.settings(change={"stage_headroom": "nameplate"})


@pytest.mark.parametrize("change", [{"stage_headroom": "safe"}, {"weight": 3.0}])
def test_the_tool_refuses_what_the_store_refuses(change):
    assert srv.settings(change=change).startswith("! ")
    assert settings.read()["version"] == 0


def _capture(monkeypatch, use_world, state, name):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        raise _Stop

    use_world(state)
    monkeypatch.setattr(planning, name, fake)
    return seen


@pytest.mark.parametrize(
    "tool, builder",
    [("diff_vs_save", "build_diff_report"), ("commission_plan", "build_commission_report")],
)
def test_staging_tools_default_to_the_shared_settings(monkeypatch, use_world, state, tool, builder):
    seen = _capture(monkeypatch, use_world, state, builder)
    with pytest.raises(_Stop):
        getattr(srv, tool)(objective="max_mw", exports=["MW"])
    assert (seen["default"], seen["biomass"]) == ("measured", False)

    settings.write({"stage_headroom": "nameplate", "biomass": True}, Actor("page", "", 1))
    with pytest.raises(_Stop):
        getattr(srv, tool)(objective="max_mw", exports=["MW"])
    assert (seen["default"], seen["biomass"]) == ("nameplate", True)
    with pytest.raises(_Stop):
        getattr(srv, tool)(objective="max_mw", exports=["MW"], biomass=False)
    assert seen["biomass"] is False


@pytest.mark.parametrize("tool", ["power_report", "world_summary"])
def test_power_tools_take_biomass_from_the_shared_setting(monkeypatch, use_world, state, tool):
    asked = []
    real = state.power_report

    def recorded(biomass=False):
        asked.append(biomass)
        return real(biomass=biomass)

    monkeypatch.setattr(state, "power_report", recorded)
    use_world(state)
    fn = getattr(srv, tool)
    seen = []
    for step in ("shared", "changed", "override"):
        if step == "changed":
            settings.write({"biomass": True}, Actor("page", "", 1))
        fn(biomass=False) if step == "override" else fn()
        seen.append(set(asked))
        asked.clear()
    assert seen == [{False}, {True}, {False}]
