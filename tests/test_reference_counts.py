"""Every number a comment in ``src`` quotes off the committed projection, asserted here.

**The whole job of this file is to make the next fixture regeneration fail loudly**, and
it is the only file in the suite whose failure is not a bug report. A dozen modules
justify a design decision by citing a count measured on ``fixtures/save_projection.json``
-- "11,664 material edges against 1,297 power edges, so the two graphs answer different
questions", "6 of 570 machines are wired to nothing, which is why the actor list cannot
come from the edges alone". Those sentences are the reasoning, not decoration: a reader
deciding whether to keep the power graph separate is deciding on the strength of the
ratio, and a reader who re-cuts the fixture from a newer save silently turns every one of
them into a number that used to be true.

That is not hypothetical. This file exists because a review found seven such counts stale
at once -- material edges quoted at 11,554 where the fixture holds 11,664, the machine
census at 566 where it is 570, the object count at 44,307 where it is 44,634 -- all of
them fossils of an older save, all of them still reading as measurements.

So: when this fails, nothing is broken. Re-measure, and update the comment the failing
assertion names as well as the number here. Both, or the next reviewer finds the same
thing again. The assertion messages carry the file and line to go and edit.

Fixture only -- no game install, no save.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

#: The three record kinds that carry a placed building's own transform, and which every
#: "N machines" count in the tree means when it says machines.
PLACED = ("machines", "extractors", "generators")


@pytest.fixture(scope="module")
def proj() -> dict:
    return json.loads((FIXTURES / "save_projection.json").read_text(encoding="utf-8"))


def _records(projection: dict) -> list[dict]:
    return [r for key in PLACED for r in projection[key]]


def test_the_two_edge_counts_domain_factories_model_cites(proj):
    """``domain/factories/model.py`` bullets 1 and 2.

    The docstring's claim is not "there are edges" but that material over-fragments where
    power does not, and the ratio is the evidence: nine times as many material edges as
    power ones.
    """
    graph = proj["graph"]
    assert len(graph["material"]) == 11_664, "domain/factories/model.py:9 quotes this"
    assert len(graph["power"]) == 1_297, (
        "domain/factories/model.py:12 and domain/planning/commission.py:63 both quote this"
    )


def test_the_power_geometry_extract_and_the_map_cite(proj):
    """Schema 17's counts, and the invariant the whole key is built around.

    ``power["wires"]`` carries no connectivity of its own on purpose -- ``graph["power"]``
    is that, and has been since schema 11 -- so the two lists are joined by POSITION and
    nothing enforces it but the single pass in ``extract._power`` that writes both. A
    regeneration that dropped one wire from either list and not the other would leave every
    span after it drawn between the wrong two actors, and would break no other test.
    """
    power = proj["power"]
    assert len(power["wires"]) == len(proj["graph"]["power"]) == 1_297, (
        "core/saveio/extract.py's `_power` states this equality as the key's one promise"
    )
    assert sum(1 for w in power["wires"] if w is None) == 0, (
        "every wire on this world publishes its span; a null here means mWireInstances "
        "stopped reading and core/saveio/rows.py:iter_wires quotes the zero"
    )
    poles = power["poles"]["instances"]
    assert len(poles) == 701, "core/saveio/extract.py's POWER_POLE_CLASSES quotes this"
    assert sum(1 for r in poles if r[5] < 0) == 2, (
        "core/saveio/extract.py's `_power` quotes the two unstrung tower platforms"
    )
    assert sum(1 for r in poles if r[4] is None) == 0, (
        "every pole's rotation reads, which is what makes the warning in `extract` silent"
    )


def test_the_machine_census_five_modules_cite(proj):
    """570, spelled in ``commission.py``, ``build.py``, ``elevation.py`` and this file.

    One number in four docstrings is exactly the failure mode this file is for: when the
    fixture moves, three of the four get updated and the fourth reads as a measurement
    for another year.
    """
    assert len(_records(proj)) == 570, (
        "domain/planning/commission.py:53, domain/factories/build.py:29 and "
        "domain/spatial/elevation.py:29,193 all quote this"
    )
    assert proj["n_objects"] == 44_634, "domain/planning/commission.py:67 quotes this"


def test_the_six_machines_wired_to_nothing(proj):
    """``domain/factories/build.py``: why the actor list cannot come from the edges alone.

    The interned actor list is derived from edges, so a machine on no belt and no wire is
    absent from it -- and an isolated machine is precisely what a coverage report exists
    to surface. If this ever reaches zero the loop it justifies looks like dead code.
    """
    wired = set(proj["graph"]["actors"])
    orphans = [r for r in _records(proj) if r["instance"].rsplit(".", 1)[-1] not in wired]
    assert len(orphans) == 6, "domain/factories/build.py:29 quotes this"


def test_the_seven_machines_no_wire_reaches(proj):
    """What ``factory_health`` reports as wired to nothing, which is a LOOSER set than the
    six above: those are on no belt and no wire, these are merely on no wire.

    Seven of 570, and none of them stalled -- four half-built assemblers, a constructor and
    two oil pumps. That is why "wired to nothing" is reported alongside every state rather
    than as one: the machines it finds are unfinished, not stopped.
    """
    from satisfactory_mcp.domain.factories.build import build_graph

    graph = build_graph(proj)
    names = [r["instance"].rsplit(".", 1)[-1] for r in _records(proj)]
    assert len(names) == 570
    assert sum(1 for n in names if not graph.neighbours(n, "power")) == 7


def test_no_machine_on_this_fixture_is_on_a_generator_less_circuit(proj):
    """Zero, and the zero is the point -- ``domain/factories/health.py`` and §6.1a.

    The wider half of the same claim: a machine can be wired and still have no generator
    anywhere on the circuit it is wired to. On this world every such component is a bare
    pole chain, so the honest answer for the reference save is "every wired machine is on a
    circuit a generator stands on". It is not vacuous code -- 34 of the 98 saves on the
    author's machine carry one, up to 32 machines at a time, and ten Oil Refineries in two
    rows of five on HL_BUFFER_A..D are what it was measured against. If this ever stops
    being zero the fixture has been re-cut from a save where the check fires, and the
    reference numbers quoted for the empty case go with it.
    """
    from satisfactory_mcp.domain.factories.build import build_graph

    graph = build_graph(proj)
    sources = {r["instance"].rsplit(".", 1)[-1] for r in proj["generators"]}
    assert len(sources) == 62
    reached: set[str] = set()
    stack = list(sources)
    while stack:
        node = stack.pop()
        if node not in reached:
            reached.add(node)
            stack.extend(graph.neighbours(node, "power"))
    stranded = [
        r["instance"].rsplit(".", 1)[-1]
        for r in _records(proj)
        if graph.neighbours(r["instance"].rsplit(".", 1)[-1], "power")
    ]
    assert [n for n in stranded if n not in reached] == []


def test_the_lightweight_piece_count_four_modules_cite(proj):
    """8,347, and the 4,631 of them that sit at a yaw off the 90-degree grid.

    The second is the whole justification for the yaw column: a client without it draws
    every one of those 4,631 pieces axis-aligned, which is how an angled platform came
    out as a staircase.
    """
    rows = proj["structures"]["instances"]
    assert len(rows) == 8_347, (
        "core/saveio/extract.py:287,814,1071, core/saveio/rows.py:4, "
        "domain/spatial/elevation.py:27,193 and "
        "docs/web-wire.md (Placements) quote this"
    )
    assert sum(1 for r in rows if r[4] % 90) == 4_631, "core/saveio/extract.py:1071 quotes this"


def test_the_productivity_window_is_not_the_constant_it_looks_like(proj):
    """``domain/factories/health.py``, and the one entry here that guards against a BUG.

    The docstring used to say the window is 300.00 s on every carrier and so "needs no
    normalisation", which is an invitation to divide by a literal 300. It is not constant
    -- three distinct values across 524 carriers -- and ``health.build`` correctly divides
    by each record's own ``window_s``. This asserts the fixture still disproves the
    tempting version, so that nobody re-derives it from a sample that happens to agree.
    """
    live = [r["uptime"] for r in _records(proj) if r.get("uptime")]
    assert len(live) == 524, "domain/planning/commission.py:53 and health.py:12 quote this"
    windows = {u["window_s"] for u in live}
    assert windows == {300.0, 300.01, 300.02}, (
        "domain/factories/health.py:9 says this field is NOT a constant -- if the fixture "
        "ever makes it one, say so there rather than deleting the per-record divisor"
    )
    assert sum(1 for u in live if not u["produce_s"]) == 231, (
        "domain/factories/health.py:16 quotes this"
    )


def test_the_crate_census_extract_and_the_endpoint_cite(proj):
    """``core/saveio/extract._crates`` and ``routers/layers/crates.py``, schema 18.

    Two crates, and the pair is the whole argument for the ``kind`` field existing: one
    says what it is and one cannot. The dismantle crate was made under a build that has
    ``mCrateType``; the other predates the property entirely -- it first appears in this
    world's saves under build 201717 and still reads ``none`` under 495413, which is why
    ``CRATE_KINDS`` argues that ``none`` is an answer rather than a failure.

    Also the join, asserted rather than assumed: a crate's contents come off a component
    named ``inventory`` on THIS save and ``Inventory`` on others, and a case-sensitive test
    would leave both of these rows holding nothing while still reporting two crates.
    """
    crates = proj["crates"]
    assert len(crates) == 2, (
        "core/saveio/extract.py:_crates and routers/layers/crates.py quote this"
    )
    assert [c["kind"] for c in crates] == ["dismantle", "none"], "sorted by kind, then instance"
    assert all(c["cls"] == "BP_Crate_C" for c in crates), "CRATE_CLASSES is a list of one"
    assert all(c["yaw"] is not None and c["pos"] for c in crates)
    dismantle, unknown = crates
    assert dismantle["items"] == [
        ["Desc_IronPlate_C", 15],
        ["Desc_SteelPlateReinforced_C", 4],
        ["Desc_SAM_C", 3],
        ["Desc_Rotor_C", 2],
    ], "biggest first, ties by class -- and the join found them at all"
    assert dismantle["slots"] == 4
    assert unknown["items"] == [["Desc_Coal_C", 17], ["Desc_Cement_C", 7]]
    assert unknown["slots"] == 2


def test_a_crate_is_not_counted_as_a_container_or_as_a_building(proj):
    """Two keys schema 18 did not touch, and the bucket schema 19 finally moved.

    A crate is not in ``storage`` (schema 15 joins a written-down list of container classes
    and ``BP_Crate_C`` is not one) and not in ``building_counts`` (it is not a ``Build_``
    actor and the extractor ``continue``s past it before the tally). Its contents sat in
    ``inventories["machine"]`` through schema 18 -- this test used to pin them there,
    precisely so that moving them would be a visible decision -- and schema 19 made the
    decision: a dead pioneer's pockets are recoverable stock, not a machine buffer, so they
    sum into ``inventories["crate"]``, and the parity bank compares the old shape through
    ``_unfix_19``. Pinned as an EXACT match: the bucket is the crates' own contents summed,
    nothing more, so a splitter or a drop pod leaking into it fails here rather than
    inflating a number quietly.
    """
    assert not any("Crate" in row["cls"] for row in proj["storage"])
    assert not any("Crate" in cls for cls in proj["building_counts"])
    assert set(proj["inventories"]) == {"player", "storage", "machine", "crate"}
    summed: dict[str, float] = {}
    for crate in proj["crates"]:
        for item, amount in crate["items"]:
            summed[item] = summed.get(item, 0) + amount
    assert proj["inventories"]["crate"] == summed, (
        "inventories['crate'] must be exactly the crates' own stacks -- more means another "
        "owner leaked into the bucket, less means a crate the bucket rule cannot see"
    )


def test_the_reference_projection_reports_no_losses(proj):
    """``warnings`` empty, which is what makes every count above a count of the world.

    A projection that dropped records is a projection whose numbers are about what read,
    not about what is built -- so a fixture with warnings in it invalidates this whole
    file rather than just one line of it. See ``core/saveio/extract._drop_notes``.
    """
    assert proj["warnings"] == []
    assert proj["schema_version"] == 22
