"""``client_over`` lends one app to many tests; each use sees its own world and nothing else."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from tests.support.web import client_over


def test_a_reused_app_keeps_nothing_of_its_last_use(state, game):
    with client_over(state, game) as first:
        first.app.state.left_behind = True
        first_watcher = first.app.state.watcher
        first_jobs = first.app.state.mapjobs
    with client_over(None, None) as second:
        assert second.app is first.app
        assert not hasattr(second.app.state, "left_behind")
        assert second.app.state.watcher is not first_watcher
        assert second.app.state.mapjobs is not first_jobs
        assert second.app.state.load_state() is None
        assert second.app.state.game() is None


def test_a_client_opened_inside_another_gets_its_own_app(state, game):
    with client_over(state, game) as outer, client_over(None, None) as inner:
        assert inner.app is not outer.app
        assert outer.app.state.load_state() is state
        assert inner.app.state.load_state() is None
        assert outer.get("/api/summary").status_code == 200
