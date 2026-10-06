"""``/api/worlds`` and ``/api/summary``: the save picker, and the header it opens onto."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")


from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.interfaces.web.routers import world as web_world

#: A save header as the sidecar's ``header_info`` builds one, all thirteen keys in emission
#: order, so the test below shows which eight the response model deletes.
_HEADER = {
    "path": "C:/saves/a.sav",
    "filename": "a.sav",
    "session_name": "Han Solo",
    "save_identifier": "X2faPVKjX06VaRzClNv5KQ",
    "save_header_version": 13,
    "save_version": 46,
    "build_version": 372858,
    "play_duration_s": 42,
    "save_datetime_ticks": 638_000_000_000_000_000,
    "is_modded": False,
    "is_creative": False,
    "mtime_ns": 1_700_000_000_000_000_000,
    "size": 4_242_424,
}


def test_worlds_lists_the_save_picker_rows(client, monkeypatch):
    """The picker's rows, ``list_worlds`` stubbed. Whole dicts with ``==``: the response model
    is a filter, and what it drops (eight header keys, three of an unsupported file's five) and
    the declaration order it keeps are the contract."""
    world = World(
        world_id="X2faPVKjX06VaRzClNv5KQ",
        session_name="Han Solo",
        saves=[dict(_HEADER)],
    )
    unsupported = [
        {
            "path": "C:/saves/old.sav",
            "filename": "old.sav",
            "reason": "saveHeaderType 8 is pre-1.0",
            "mtime_ns": 1_500_000_000_000_000_000,
            "size": 999,
        }
    ]
    monkeypatch.setattr(web_world.proj, "list_worlds", lambda: ([world], unsupported))
    body = client.get("/api/worlds").json()
    assert body["worlds"] == [
        {
            "world_id": "X2faPVKjX06VaRzClNv5KQ",
            "session_name": "Han Solo",
            "saves": [
                {
                    "path": "C:/saves/a.sav",
                    "filename": "a.sav",
                    "session_name": "Han Solo",
                    "play_duration_s": 42,
                    "mtime_ns": 1_700_000_000_000_000_000,
                }
            ],
            "mtime": pytest.approx(1.7e9),
            "newest_filename": "a.sav",
            "play_duration_s": 42,
        }
    ]
    assert body["unsupported"] == [{"filename": "old.sav", "reason": "saveHeaderType 8 is pre-1.0"}]
    row = body["worlds"][0]
    assert list(row) == [
        "world_id",
        "session_name",
        "saves",
        "mtime",
        "newest_filename",
        "play_duration_s",
    ]
    assert list(row["saves"][0]) == [
        "path",
        "filename",
        "session_name",
        "play_duration_s",
        "mtime_ns",
    ]


def test_summary_carries_the_same_save_token_the_tools_print(client, state):
    """One token names a world state for the page and the tools (docs/mcp-surface.md 10.1i)."""
    body = client.get("/api/summary").json()
    assert body["save_token"] == state.token
    assert body["save_token"] in body["age_note"]


def test_summary_reports_the_header_power_and_progression(client, state):
    body = client.get("/api/summary").json()
    assert body["header"]["session_name"] == state.header["session_name"]
    assert body["age_note"] == state.age_note
    assert body["power"]["generation_mw"] == pytest.approx(state.power_report()["generation_mw"])
    assert body["progression"]["game_phase"] == state.progression()["game_phase"]
    # The you-are-here marker's only source. Metres, like everything else here.
    pos = state.player_position()
    if pos is None:
        assert body["player"] == {"x_m": None, "y_m": None, "z_m": None}
    else:
        assert body["player"]["x_m"] == pytest.approx(round(pos[0] / 100.0, 1))
        assert body["player"]["y_m"] == pytest.approx(round(pos[1] / 100.0, 1))
