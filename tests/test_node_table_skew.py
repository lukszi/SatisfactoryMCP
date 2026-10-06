"""The gate that makes a stale node table loud instead of silently wrong.

Resource nodes move when the map changes in a game update, so a node table pinned to an
older build is a recurring condition, not an anomaly. Two things then go wrong without a
sound: a join by instance name misses after a rename, and a position is off by up to a
metre so "the node nearest to X" answers confidently and incorrectly.

The gate reads what the artifact already measured and says it once, at the point of use.
Two properties make it worth having, and both are tested here rather than assumed:

* it FIRES when the save is past the build the table was cut from -- a gate that cannot
  fire is decoration, so the synthetic cases below drive it off metadata this repo does
  not ship;
* it is SILENT otherwise -- on a matching build, on an older save, on a refreshed
  artifact, and on any answer that happens to contain none of the drifted rows. A warning
  on every response is one nobody reads.

Nothing here restates the current figures. The real artifact's numbers are read out of
its own ``_meta`` and compared against what the gate reports, so a refresh moves both
together; the synthetic metadata uses deliberately unrealistic numbers so that a test
passing on the wrong data is obvious.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satisfactory_mcp.domain.spatial import nodes as nodes_mod

pytestmark = pytest.mark.integration

SRC = Path(__file__).resolve().parents[1] / "src" / "satisfactory_mcp"


# --------------------------------------------------------------- synthetic metadata


def _meta(
    *,
    pin: str = "saveVersion 7",
    against: str = "buildVersion 900",
    moved: tuple[dict, ...] = (
        {"instance": "L:P.NodeA", "delta_cm": 12.4, "dz_cm": -12.4},
        {"instance": "L:P.NodeB", "delta_cm": 3.0, "dz_cm": 3.0},
    ),
    only_here: tuple[str, ...] = (),
    only_there: tuple[str, ...] = (),
    renamed_cm: float | None = None,
) -> dict:
    """A ``_meta`` shaped like the artifact's, with numbers the artifact does not have.

    Two comparison blocks, because one delta cannot be honest: the table matches one build
    exactly and lags another. Which is which is decided by the measurements, not by the key
    names -- so these keys are deliberately NOT the artifact's.
    """
    return {
        "cross_validation": {
            "positions": {
                "vs_its_own_build": {
                    "build": pin,
                    "measured": "2026-01-01",
                    "method": "synthetic",
                    "rounding_floor_cm": 0.5,
                    "max_position_delta_cm": 0.4,
                    "rows_past_the_rounding_floor": [],
                },
                "vs_a_later_build": {
                    "build": against,
                    "measured": "2026-01-02",
                    "method": "synthetic",
                    "rounding_floor_cm": 0.5,
                    "max_position_delta_cm": max((r["delta_cm"] for r in moved), default=0.0),
                    "rows_past_the_rounding_floor": list(moved),
                    "rows_only_in_this_table": list(only_here),
                    "rows_only_in_the_later_build": list(only_there),
                    **({"renamed_row_moved_cm": renamed_cm} if renamed_cm is not None else {}),
                    "purity_mismatches": [],
                    "resource_mismatches": [],
                },
            }
        }
    }


# ------------------------------------------------------- the gate fires when it must


def test_a_save_past_the_pin_warns():
    """The whole point. An older pin plus a newer save must produce a note."""
    skew = nodes_mod.skew_from_meta(_meta(), {"save_version": 8})
    assert skew is not None
    notes = nodes_mod.skew_notes(skew, ["NodeA", "NodeB"])
    assert notes, "the gate did not fire on a save newer than the pin"
    assert "saveVersion 7 -> 8" in notes[0]
    assert "12cm" in notes[0], notes[0]  # 12.4 from the metadata, never a literal in src
    assert "NodeA" in notes[0] and "NodeB" in notes[0]


def test_the_pinned_build_stays_silent():
    """A save from the build the table was cut from has nothing wrong with it."""
    assert nodes_mod.skew_from_meta(_meta(), {"save_version": 7}) is None


def test_an_older_save_stays_silent():
    """The table is AHEAD of this save, so the recorded drift is not its drift."""
    assert nodes_mod.skew_from_meta(_meta(), {"save_version": 6}) is None


def test_a_save_naming_no_version_stays_silent():
    """No comparand, no claim. Guessing would be the invented number this avoids."""
    assert nodes_mod.skew_from_meta(_meta(), {}) is None
    assert nodes_mod.skew_from_meta(_meta(), None) is None


def test_the_build_the_drift_was_measured_against_warns_on_its_own():
    """The second, independent signal.

    A save on exactly the build the drift was measured against carries all of it, whether
    or not the pin block states a marker that can be compared -- which it cannot here,
    since the pin is a saveVersion and the save reports only a buildVersion.
    """
    skew = nodes_mod.skew_from_meta(_meta(), {"build_version": 900})
    assert skew is not None
    assert skew.gap == "before buildVersion 900"


def test_a_refreshed_artifact_reports_nothing_however_new_the_save():
    """The gate must disappear when the underlying problem does.

    An artifact regenerated against the installed build records no row past its rounding
    floor and no row missing on either side. There is then nothing to warn about, and a
    gate that kept talking would train the reader to ignore it.
    """
    refreshed = _meta(moved=())
    assert nodes_mod.skew_from_meta(refreshed, {"save_version": 9_999}) is None


# ----------------------------------------------------------- how much it is allowed to say


def test_notes_are_scoped_to_the_rows_in_the_answer():
    """Proportionality. A query touching no drifted row must say nothing."""
    skew = nodes_mod.skew_from_meta(_meta(), {"save_version": 8})
    assert nodes_mod.skew_notes(skew, ["NodeZ", "NodeY"]) == []
    assert len(nodes_mod.skew_notes(skew, ["NodeA"])) == 1


def test_the_note_counts_only_the_rows_in_scope():
    """ "2 nodes moved" in an answer containing one of them is a different, false claim."""
    skew = nodes_mod.skew_from_meta(_meta(), {"save_version": 8})
    one = nodes_mod.position_notes(skew, ["NodeB"])[0]
    assert one.startswith("1 node(s)")
    assert "NodeA" not in one
    # And the maximum quoted is the in-scope maximum, not the table-wide one.
    assert "3cm stale" in one, one


def test_extra_rows_collapse_instead_of_listing_everything():
    """A wall of text on every answer is a warning nobody reads."""
    many = tuple(
        {"instance": f"L:P.Node{i}", "delta_cm": float(50 - i), "dz_cm": float(-(50 - i))}
        for i in range(12)
    )
    skew = nodes_mod.skew_from_meta(_meta(moved=many), {"save_version": 8})
    note = nodes_mod.position_notes(skew, [r["instance"] for r in many])[0]
    assert "12 node(s)" in note  # the count is honest even though the list is not complete
    assert "+9 more" in note
    assert note.count("Node") == 3, note  # three named, and the tail is a count


def test_the_two_halves_can_be_asked_for_separately():
    """A tool quoting no coordinate has no business warning about z, and the reverse."""
    skew = nodes_mod.skew_from_meta(
        _meta(only_here=("L:P.Gone",), only_there=("L:P.Fresh",), renamed_cm=42.0),
        {"save_version": 8},
    )
    scope = ["NodeA", "Gone"]
    assert len(nodes_mod.position_notes(skew, scope)) == 1
    assert len(nodes_mod.identity_notes(skew, scope)) == 1
    assert len(nodes_mod.skew_notes(skew, scope)) == 2


# ------------------------------------------------------------------ the rename half


def test_a_renamed_row_is_reported_under_either_name():
    """Callers hold the table's names in some places and the save's in others.

    The whole point of a rename is that those differ, so scoping that matched only one of
    them would drop the warning exactly where the join misses.
    """
    skew = nodes_mod.skew_from_meta(
        _meta(only_here=("L:P.Gone",), only_there=("L:P.Fresh",), renamed_cm=42.0),
        {"save_version": 8},
    )
    for name in ("Gone", "Fresh", "L:P.Gone", "L:P.Fresh"):
        notes = nodes_mod.identity_notes(skew, [name])
        assert len(notes) == 1, name
        assert "Gone" in notes[0] and "Fresh" in notes[0]
        assert "42cm" in notes[0]


def test_an_ambiguous_rename_is_not_invented():
    """Two lists of names are not a mapping.

    With one name on each side and a measured distance between them the pairing is forced.
    With two on each side it is a guess, and a guess here attributes a player's miner to
    the wrong node -- so the rows are still reported unjoinable, without a replacement.
    """
    skew = nodes_mod.skew_from_meta(
        _meta(
            only_here=("L:P.GoneA", "L:P.GoneB"),
            only_there=("L:P.FreshA", "L:P.FreshB"),
            renamed_cm=42.0,
        ),
        {"save_version": 8},
    )
    assert skew.renamed_to == {}
    assert len(skew.unjoinable) == 2
    note = nodes_mod.identity_notes(skew, ["GoneA"])[0]
    assert "FreshA" not in note and "FreshB" not in note
    assert "dropped that name" in note


def test_an_unjoinable_node_is_kept_in_the_table_not_dropped():
    """The decision this gate is built around, pinned so it cannot drift.

    A renamed node still exists in game: a player can walk to it and place a miner on it.
    Dropping the row would make the tools claim there is no node there, which is a worse
    answer than a stale z -- and it would silently reduce the advertised capacity of the
    map. Resource and purity are verified correct against the installed build, so all that
    is actually lost is the depletion counter and a metre of position. It stays, flagged.
    """
    table = nodes_mod.load_nodes()
    skew = nodes_mod.skew_for_save({"save_version": 10_000}, table)
    if skew is None or not skew.unjoinable:
        pytest.skip("this table has no unjoinable row, so there is nothing to keep")
    present = {n["instance"] for n in table.nodes}
    for instance in skew.unjoinable:
        assert instance in present, f"{instance} was dropped instead of flagged"


def test_an_extractor_on_a_renamed_node_is_told_why():
    """ "target not a node" is the wrong diagnosis: the target IS a node.

    This is failure mode one at its sharpest -- a miner the player built, on a node the
    table cannot find, reported as if the save were malformed.
    """
    table = nodes_mod.load_nodes()
    skew = nodes_mod.skew_for_save({"save_version": 10_000}, table)
    if not skew or not skew.renamed_to:
        pytest.skip("this table has no renamed row")
    old, new = next(iter(skew.renamed_to.items()))
    projection = {
        "header": {"save_version": 10_000},
        "extractors": [
            {"instance": "L:P.Build_MinerMk1_C_1", "cls": "Build_MinerMk1_C", "node": new}
        ],
    }
    (row,) = nodes_mod.unresolved_extractors(projection)
    assert "renamed" in row["reason"]
    assert old.rsplit(".", 1)[-1] in row["reason"]

    # And on a save from the pinned build the same name is simply not a node, with no
    # speculation about updates attached to it.
    quiet = dict(projection, header={"save_version": 1})
    assert "renamed" not in nodes_mod.unresolved_extractors(quiet)[0]["reason"]


# --------------------------------------------------- the gate against the real artifact


def test_the_shipped_table_reports_exactly_what_its_meta_records(table_and_header):
    """Every figure the gate quotes must come from the artifact, not from this module.

    Asserting a literal here would be the same mistake as hardcoding one in ``src``: the
    next refresh moves the numbers and the assertion becomes a description of a build
    nobody runs. Which is exactly what happened to this test's previous body -- it began
    ``assert skew is not None, "the reference save is newer than the pin and must warn"``,
    true while the table was cut from an older build than the save. The table is now
    generated from the installed build's own map package, its one positions block records
    zero drift and no missing row, and what the artifact states, the gate must say -- and
    a refreshed artifact states there is nothing to say. Silence, on the reference save
    and on any imaginably newer one, until a re-measure against some future build records
    real drift and this test must flip again. The firing half of the gate stays tested by
    the synthetic cases above, which this repo's data can no longer drive.
    """
    table, header = table_and_header
    recorded = table.meta["cross_validation"]["positions"]["against_the_installed_build"]
    assert recorded["max_position_delta_cm"] <= recorded["rounding_floor_cm"]
    assert recorded["rows_past_the_rounding_floor"] == []
    assert recorded["rows_only_in_this_table"] == []
    assert recorded["rows_only_in_the_installed_build"] == []

    assert nodes_mod.skew_for_save(header, table) is None
    assert nodes_mod.skew_for_save({"save_version": 10_000}, table) is None
    assert nodes_mod.skew_notes(None, ["anything"]) == []


def test_src_does_not_restate_the_current_figures():
    """A figure copied into ``src`` becomes a lie the day the artifact is refreshed.

    So the two numbers and the one instance name that describe today's skew must appear in
    ``data/`` and nowhere under ``src/``. This is the constraint the gate was written
    under, and it is cheap enough to enforce rather than remember.
    """
    recorded = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "resource_nodes.json").read_text(
            encoding="utf-8"
        )
    )["_meta"]["cross_validation"]["positions"]["against_the_installed_build"]

    # A refreshed artifact records no drifted row, no rename and no orphan name, so most
    # of these sets are empty and the scan is a no-op that RE-ARMS the day real drift is
    # recorded again. The worst-delta figure is only forbidden while there is drift: a
    # sub-rounding value like 0.008 is not a skew figure, it is arithmetic noise that
    # legitimately appears in source as constants.
    rows = recorded["rows_past_the_rounding_floor"]
    forbidden = {
        *((str(recorded["max_position_delta_cm"]),) if rows else ()),
        *((str(len(rows)) + " node",) if rows else ()),
        *(
            (str(recorded["renamed_row_moved_cm"]),)
            if recorded.get("renamed_row_moved_cm") is not None
            else ()
        ),
        *(i.rsplit(".", 1)[-1] for i in recorded["rows_only_in_this_table"]),
        *(i.rsplit(".", 1)[-1] for i in recorded["rows_only_in_the_installed_build"]),
    }
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        offenders += [
            f"{path.relative_to(SRC)}: {needle}" for needle in forbidden if needle in text
        ]
    assert offenders == [], offenders


@pytest.fixture(scope="module")
def table_and_header(projection):
    return nodes_mod.load_nodes(), projection["header"]


# ------------------------------------------------------------- reaching the answer


@pytest.fixture
def tool(state, use_world):
    """``tools.spatial`` reading the committed projection instead of this machine's saves."""
    from satisfactory_mcp.interfaces.mcp.tools import spatial as mod

    use_world(state)
    return mod


def _leaf(instance: str) -> str:
    return instance.rsplit(".", 1)[-1]


def test_a_drifted_row_is_named_in_the_tool_output(tool, state):
    """The gate has to reach the response, not just exist in a module.

    Driven off whichever row the artifact currently names, so a refresh that changes the
    set does not leave this test pinned to a node nobody is looking at.
    """
    skew = nodes_mod.skew_for_save(state.header)
    if not skew or not skew.moved_cm:
        pytest.skip("this table records no drifted row against the reference save")
    worst = max(skew.moved_cm, key=lambda i: skew.moved_cm[i])
    out = tool.search_resource_nodes(sources=[f"node:{_leaf(worst)}"], show="nodes")
    assert "moved in a game update" in out, out
    assert _leaf(worst) in out


def test_an_answer_with_no_drifted_row_says_nothing_about_drift(tool, state):
    """Otherwise it is a banner, and a banner on every answer is not a warning."""
    skew = nodes_mod.skew_for_save(state.header)
    drifted = set(skew.moved_cm) | set(skew.unjoinable) if skew else set()
    clean = next(
        n
        for n in nodes_mod.load_nodes().nodes
        if n["kind"] == "node" and n["instance"] not in drifted
    )
    out = tool.search_resource_nodes(sources=[f"node:{_leaf(clean['instance'])}"], show="nodes")
    assert "game update" not in out, out


def test_the_unjoinable_node_is_still_returned_by_the_tool(tool, state):
    """The keep-and-flag decision, at the surface a player actually reads.

    The row is listed with its rate and its purity, and the note next to it says what
    cannot be read. Dropping it would have made this query answer "no nodes".
    """
    skew = nodes_mod.skew_for_save(state.header)
    if not skew or not skew.unjoinable:
        pytest.skip("this table has no unjoinable row")
    orphan = _leaf(skew.unjoinable[0])
    out = tool.search_resource_nodes(sources=[f"node:{orphan}"], show="nodes")
    assert "1 node(s)" in out
    assert orphan in out
    assert "not in this save under that name" in out, out
