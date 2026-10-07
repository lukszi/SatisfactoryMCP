"""The cached rows of ``advice.current`` (docs/advisors_contract.md §9): another set of factory
names, or another projection under the same save token, is another answer."""

from __future__ import annotations

import json

import pytest

from satisfactory_mcp import config
from satisfactory_mcp.core.singleflight import Singleflight
from satisfactory_mcp.domain import advice
from satisfactory_mcp.domain.advice import rules
from satisfactory_mcp.domain.world.state import WorldState
from tests.support.paths import FIXTURES
from tests.support.reference_world import FIXTURE_WORLD


@pytest.fixture(autouse=True)
def _empty_caches(monkeypatch):
    monkeypatch.setattr(advice, "_ROWS", Singleflight(maxsize=8))
    monkeypatch.setattr(rules, "_PLANS", {})


def _factories(st: WorldState) -> set[str]:
    return {r.subject for r in advice.current(st).items if r.subject_kind == "factory"}


def test_label_stores_sharing_a_version_each_get_their_own_names(projection, game):
    path = config.labels_dir() / f"{FIXTURE_WORLD}.json"
    assert _factories(WorldState(projection=projection, game=game)) == set()

    path.write_bytes((FIXTURES / "labels_reference.json").read_bytes())
    named = WorldState(projection=projection, game=game)
    assert named.labels.version == 0 and "speedwire factory" in _factories(named)

    doc = json.loads(path.read_text(encoding="utf-8"))
    for label in doc["labels"]:
        if label["name"] == "speedwire factory":
            label["name"], label["id"] = "wire works", "wire-works"
    path.write_text(json.dumps(doc), encoding="utf-8")
    renamed = WorldState(projection=projection, game=game)
    assert renamed.labels.version == 0
    assert "wire works" in _factories(renamed) and "speedwire factory" not in _factories(renamed)


def test_a_copy_under_the_same_token_is_not_served_the_originals_rows(projection, game):
    original = WorldState(projection=projection, game=game)
    copy = WorldState(projection={**projection, "players": []}, game=game)
    assert copy.token == original.token
    assert any(r.kind == "pickups" for r in advice.current(original).items)
    assert not any(r.kind == "pickups" for r in advice.current(copy).items)
