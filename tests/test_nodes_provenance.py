"""``data/world_resource_nodes.json`` says where it came from. This checks it, claim by claim.

The file it replaced was ``data/world_resource_nodes.mit.json``: 626 node rows vendored from
rockfactory/satisfactory-logistics -- MIT-licensed, Copyright (c) 2024 Leonardo Ascione --
and the last third-party world table this repository still USED. The vendored file is
deleted; the node set now comes out of the installed game's own ``Persistent_Level.umap``,
read by ``tools/gen_world_resource_nodes.py`` through ``core.gameassets``.

Same three-part shape as ``tests/test_regions_provenance.py``, which pinned the identical
move for the region layer's CC BY-SA wiki trace, and for the same reasons.

**The licence claim**, by string scan: nothing in the artifact may credit the third party or
describe its data as the source -- except inside ``_meta.retired_mit_table``, which is
history and has to survive so the change is on the record rather than tidied away. A mention
anywhere else must disown the thing in the same sentence.

**The internal arithmetic**: counts, class census, purity vocabulary and the well link,
because a table that contradicts itself answers questions anyway.

**The projection**: the served ``data/resource_nodes.json`` must be exactly this file
filtered and relabelled -- checked here with no generator in the loop, so the two committed
artifacts cannot drift apart between regenerations.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
TABLE = json.loads((DATA / "world_resource_nodes.json").read_text(encoding="utf-8"))
META = TABLE["_meta"]
NODES = TABLE["nodes"]
SERVED = json.loads((DATA / "resource_nodes.json").read_text(encoding="utf-8"))

#: Words that may appear only inside the block that records what was retired. The
#: repository, the author, the licence and the extraction tool: the four ways a credit --
#: and with it an attribution obligation -- could come back in.
RETIRED_WORDS = (
    "rockfactory",
    "satisfactory-logistics",
    "ascione",
    "fmodel",
    "mit.json",
    "mit-licensed",
)

#: Where naming the retired table is allowed, because the change has to stay on the record.
HISTORY_KEY = "retired_mit_table"

#: A mention outside the history block is allowed only in a sentence that also says the
#: thing is gone. Same rule the retired wiki trace and the deleted GPL parser are held to:
#: the history is worth keeping and the credit is not, and a bare mention is how one turns
#: back into the other.
RETIRED_QUALIFIERS = ("retired", "deleted", "gone", "no third-party")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


# --------------------------------------------------------------------------------------
# The licence claim.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("word", RETIRED_WORDS)
@pytest.mark.parametrize(
    "name, payload",
    [("world_resource_nodes.json", TABLE), ("resource_nodes.json", SERVED)],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_the_mit_table_is_named_only_as_history(word, name, payload):
    """It may be remembered and it may not be credited, in either committed artifact."""
    meta = payload.get("_meta", {})
    for key, block in meta.items():
        if key == HISTORY_KEY:
            continue
        for s in _strings(block):
            if word not in s.casefold():
                continue
            assert any(q in s.casefold() for q in RETIRED_QUALIFIERS), f"{name}/{key}: {s}"
    for key, block in payload.items():
        if key == "_meta":
            continue
        assert not [s for s in _strings(block) if word in s.casefold()], f"{name}/{key}"


def test_the_retirement_is_on_the_record_rather_than_tidied_away():
    """The block that is allowed to name the table must actually name it, and say what moved.

    An empty exemption is worse than no exemption: it would let the history be deleted
    while the scan above kept passing. The parity figures are pinned because they are the
    proof the replacement lost nothing: same 626-row composition, purity equal on every
    shared id, resource equal wherever comparable, and every position difference accounted
    for -- 25 rows the game moved vertically after the third-party extraction, and one row
    it renamed, both already measured against save actors while the old table was current.
    """
    history = META[HISTORY_KEY]
    joined = " ".join(_strings(history)).casefold()
    assert "mit" in joined
    assert "leonardo ascione" in joined
    assert "deleted" in joined
    assert history["rows"] == {
        "mit": 626,
        "this_extraction_plus_the_deposit": 626,
        "shared_ids": 625,
    }
    assert len(history["positions"]["moved_rows_dz_cm"]) == 25
    assert max(abs(v) for v in history["positions"]["moved_rows_dz_cm"].values()) == 80.38
    assert history["renamed"]["mit"] == "BP_ResourceNode11"
    assert history["renamed"]["apart_cm"] == 150.12
    # The renamed row's new name is a row of this very file, so the record and the data
    # cannot describe two different worlds.
    assert any(r["id"] == history["renamed"]["now"] for r in NODES)


def test_the_source_file_is_gone_rather_than_merely_unused():
    """``data/world_resource_nodes.mit.json`` is deleted. A file nothing reads is still a
    file somebody will read, and this one carried the credit."""
    assert not (DATA / "world_resource_nodes.mit.json").exists()


def test_the_licence_line_states_first_party_derivation():
    licence = META["licence"].casefold()
    assert "first-party" in licence
    assert "no third-party table" in licence
    assert "no attribution obligation" in licence


def test_the_build_is_pinned_so_a_consumer_can_tell_it_predates_theirs():
    assert "502094" in META["game_version_pinned"]
    assert META["generated"].startswith("20")
    assert META["source"]["package"].endswith("Persistent_Level.umap")
    assert "gameassets" in META["source"]["read_by"]


# --------------------------------------------------------------------------------------
# The internal arithmetic.
# --------------------------------------------------------------------------------------


def test_the_counts_are_the_files_own_arithmetic():
    assert META["count"] == len(NODES)
    census = {}
    for row in NODES:
        census[row["class"]] = census.get(row["class"], 0) + 1
    assert census == META["by_class"]
    purities = {}
    for row in NODES:
        purities[row["purity"]] = purities.get(row["purity"], 0) + 1
    assert purities == META["by_purity"]
    assert set(purities) <= {"impure", "normal", "pure"}
    assert len({r["id"] for r in NODES}) == len(NODES)


def test_the_coverage_guard_reported_nothing_outside_the_persistent_level():
    """The one-package read is complete only while the streamed cells place none of these
    classes; the generator measures that and this pins what it measured."""
    coverage = META["coverage"]
    assert coverage["emitted_classes_outside_the_persistent_level"] == 0
    assert coverage["packages_swept"] > 4000
    assert any(
        level.get("of_a_counted_class") for level in coverage["other_levels_in_the_container"]
    ), (
        "the developer test map places resource nodes; a sweep that finds none anywhere "
        "else in the container has stopped looking"
    )


def test_the_well_link_is_total_and_resource_consistent():
    cores = {r["id"]: r for r in NODES if r["class"] == "BP_FrackingCore_C"}
    sats = [r for r in NODES if r["class"] == "BP_FrackingSatellite_C"]
    assert sats and cores
    referenced = set()
    for sat in sats:
        assert sat.get("core") in cores, sat["id"]
        assert sat["resource"] == cores[sat["core"]]["resource"], sat["id"]
        referenced.add(sat["core"])
    assert referenced == set(cores)
    for row in NODES:
        if row["class"] == "BP_ResourceNodeGeyser_C":
            assert row["resource"] is None, f"{row['id']}: a geyser with a resource is new"
        elif row["class"] != "BP_FrackingSatellite_C":
            assert row.get("core") is None, row["id"]


# --------------------------------------------------------------------------------------
# The projection: the served table is this file, filtered and relabelled.
# --------------------------------------------------------------------------------------


def test_the_served_table_is_exactly_this_files_projection():
    """Row set, resource, purity, well link and position, with no generator in the loop.

    ``tools/gen_resource_nodes.py`` re-checks this on every run, but the two files are
    committed separately and only a test can promise they were committed TOGETHER.
    """
    prefix = "Persistent_Level:PersistentLevel."
    kinds = {
        "BP_ResourceNode_C": "node",
        "BP_FrackingSatellite_C": "well_sat",
        "BP_ResourceNodeGeyser_C": "geyser",
    }
    source = {r["id"]: r for r in NODES if r["class"] in kinds}
    served = {n["instance"]: n for n in SERVED["nodes"]}
    assert set(served) == {prefix + name for name in source}
    for name, src in source.items():
        row = served[prefix + name]
        assert row["kind"] == kinds[src["class"]]
        assert row["purity"] == src["purity"]
        assert row["resource"] == (src["resource"] or "Desc_Geyser_C")
        expected_core = prefix + src["core"] if src["class"] == "BP_FrackingSatellite_C" else None
        assert row["well_core"] == expected_core
        # The served table rounds to whole hundredths of a centimetre; half of one is the
        # most the two committed files may disagree by, per axis.
        delta = math.dist((row["x"], row["y"], row["z"]), (src["x"], src["y"], src["z"]))
        assert delta <= math.sqrt(3) / 2 * 0.01 + 1e-9, name
