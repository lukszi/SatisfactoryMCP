from __future__ import annotations

import copy

import pytest

from satisfactory_mcp.core.saveio import projection as proj
from satisfactory_mcp.domain.world import timeline as tl
from satisfactory_mcp.domain.world.state import WorldState


@pytest.fixture
def row(state):
    return tl.build_row(state)


def _shift(state, *, play, mtime, machines_dropped=0):
    """The fixture world as it would have been earlier: same identity, fewer machines."""
    d = copy.deepcopy(state.projection)
    d["header"] = dict(d["header"], play_duration_s=play, mtime_ns=mtime)
    if machines_dropped:
        d["machines"] = d["machines"][:-machines_dropped]
    return WorldState(projection=d, game=state.game)


def test_row_is_small(row):
    import json

    assert len(json.dumps(row, separators=(",", ":"))) < 4000


def test_key_separates_two_states_sharing_a_filename(state):
    """The autosave trap: `autosave_0` is a different world every rotation."""
    header = state.projection["header"]
    a = tl.row_key(dict(header, play_duration_s=1000, mtime_ns=1))
    b = tl.row_key(dict(header, play_duration_s=1300, mtime_ns=2))
    assert header["filename"] == header["filename"]
    assert a != b


def test_key_moves_when_either_schema_moves(state, monkeypatch):
    header = state.projection["header"]
    before = tl.row_key(header)
    monkeypatch.setattr(proj, "SCHEMA_VERSION", proj.SCHEMA_VERSION + 1)
    assert tl.row_key(header) != before
    monkeypatch.undo()
    monkeypatch.setattr(tl, "INDEX_SCHEMA", tl.INDEX_SCHEMA + 1)
    assert tl.row_key(header) != before


def test_changed_since_reports_both_axes_and_the_window(state, row):
    older = tl.build_row(_shift(state, play=row["play_duration_s"] - 7200, mtime=1, machines_dropped=3))
    line = tl.Timeline(world_id=state.world_id, rows=[older, row])
    out = line.changed_since(older)
    assert out["played_s"] == 7200
    assert out["counts"]["machines"][2] == 3
    assert "playtime" in out["window"] and "lossy" in out["window"]


def test_a_stale_row_is_dropped_on_read(state, row, tmp_path, monkeypatch):
    monkeypatch.setattr(tl.config, "cache_dir", lambda: tmp_path)
    line = tl.save_row(tl.Timeline(world_id=state.world_id, rows=[]), row)
    assert len(tl.load_timeline(state.world_id).rows) == 1
    monkeypatch.setattr(proj, "SCHEMA_VERSION", proj.SCHEMA_VERSION + 1)
    assert tl.load_timeline(state.world_id).rows == []
    assert line.rows
