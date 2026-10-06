"""Factory labels: matching by recall, the on-disk store, and the documented file shape."""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.labels import Label, LabelStore
from tests.support.synthetic_factories import (
    BASE_CONCRETE,
    IRON,
    STEEL,
    STEEL_CONCRETE,
)

pytestmark = pytest.mark.integration


def test_recall_survives_growth_where_jaccard_would_not():
    """The reason for recall. A factory that doubles in size is still that factory."""
    label = Label(id="steel", name="steel", anchors=list(STEEL))
    grown = set(STEEL) | set(STEEL_CONCRETE) | set(IRON)
    assert label.recall(grown) == 1.0
    jaccard = len(set(STEEL) & grown) / len(set(STEEL) | grown)
    assert jaccard < 0.5, "Jaccard would have dropped this below the match threshold"


def test_recall_degrades_gracefully_as_machines_are_removed():
    label = Label(id="steel", name="steel", anchors=list(STEEL))
    assert label.recall(set(STEEL[:3])) == 0.75
    assert label.recall(set(STEEL[:1])) == 0.25
    assert label.recall(set()) == 0.0


def test_one_predicate_decides_whether_a_cluster_is_already_named():
    """The map, factory_map and propose_factories each had their own rule -- majority,
    all, any -- so the same cluster was a proposal on one surface and not on the other.
    A majority is what survives both edits the other two get wrong: a new cluster that
    swallowed one named neighbour, and a factory the player named all but one machine of.
    """
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    assert store.covers(STEEL)
    assert store.covers([*STEEL, *BASE_CONCRETE]), "named all but one is still named"
    assert not store.covers([STEEL[0], *IRON]), "one named neighbour does not claim a cluster"
    assert not store.covers([*STEEL, *IRON]), "exactly half is not a majority"
    assert not store.covers([])


def test_review_reports_shrinkage_without_acting_on_it():
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel", list(STEEL))
    store.put("iron", list(IRON))
    issues = {d["name"]: d for d in store.review(set(STEEL[:1]) | set(IRON))}
    assert "iron" not in issues, "intact labels must not be reported"
    assert issues["steel"]["status"] == "needs confirmation"
    assert issues["steel"]["missing"] == 3
    # The label is still there: reporting is not deleting.
    assert store.find("steel") is not None


def test_put_re_anchors_an_existing_label_rather_than_duplicating_it():
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    store.put("Steel Factory", STEEL + STEEL_CONCRETE)
    assert len(store.labels) == 1
    assert len(store.find("steel").anchors) == len(STEEL) + len(STEEL_CONCRETE)


def test_labels_round_trip_through_disk():
    store = LabelStore(world_id="TEST_WORLD/1", session_name="Test")
    label = store.put("steel factory", STEEL, notes="ingots")
    label.centroid = (-100_000.0, -120_000.0)
    with LabelStore.editing(store.world_id) as fresh:
        fresh.labels = store.labels

    again = LabelStore.load("TEST_WORLD/1")
    assert [x.name for x in again.labels] == ["steel factory"]
    assert again.labels[0].anchors == sorted(STEEL)
    assert again.labels[0].notes == "ingots"
    assert again.labels[0].centroid == (-100_000.0, -120_000.0)


def test_label_files_are_not_written_into_the_cache():
    """cache_prune deletes the whole cache tree; a name the player typed is not
    regenerable and must not live there."""
    from satisfactory_mcp import config

    assert config.cache_dir() not in config.labels_dir().parents
    assert config.labels_dir() != config.cache_dir()


def test_the_label_file_is_a_stable_documented_shape():
    """Labels are the one thing here a player authored by hand, so the file is an
    interface, not a private cache. A consumer joins `anchors` against its own read of
    the same save; nothing else from this server is needed."""
    from satisfactory_mcp.domain.factories.labels import SCHEMA, Label, LabelStore

    store = LabelStore(world_id="TESTWORLD", session_name="Test")
    store.put("steel factory", ["Build_FoundryMk1_C_1"], notes="ingots")
    raw = store.labels[0].to_json()
    assert set(raw) == {
        "id",
        "name",
        "anchors",
        "notes",
        "centroid",
        "signature",
        "created",
        "last_matched",
    }
    assert SCHEMA == 1
    # Round-trips through its own serialisation, which is what a version bump must keep.
    assert Label.from_json(raw).to_json() == raw


def test_the_labels_resource_is_registered_and_self_locating():
    """Finding the file must not require reverse-engineering platformdirs."""
    import asyncio
    import json

    from satisfactory_mcp import server as srv

    uris = {str(r.uri) for r in asyncio.run(srv.mcp.list_resources())}
    assert "satisfactory://factories/labels" in uris

    payload = json.loads(srv.factory_labels())
    assert "error" in payload or {"schema", "world_id", "path", "labels"} <= set(payload)
