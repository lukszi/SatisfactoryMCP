"""Schema 13's belts and the splitters and mergers they pass through.

A spline left in its chain's own frame is still a plausible polyline, far from where the
belt is, so the points are checked against where the buildings stand. Fixture-only, plus
the committed trailer bytes, so this runs with no game install.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise

from satisfactory_mcp.core.saveio.extract import (
    _ATTACHMENT_HINTS,
    Drops,
    _belts,
    _conveyor_class,
)
from tests.support.saves import PLACED, Chain, UnreadableChain, trailer_chains

# ------------------------------------------------------------------------------- belts


def test_the_belts_key_is_interned_polylines_in_whole_centimetres(projection):
    """The shape, field by field: ``[chainIndex, classIndex, [[x, y, z], ...], actorIndex]``.

    Read POSITIONALLY, with a width guard, rather than destructured -- which is the posture
    every consumer of these rows takes, and it is what let schema 15 add a curve column
    without touching one of them. A row that bends carries its tangents past the actor; a
    straight one stops at the actor.
    """
    belts = projection["belts"]
    classes = belts["classes"]
    rows = belts["segments"]
    actors = projection["graph"]["actors"]
    assert classes and all(c.startswith("Build_Conveyor") and c.endswith("_C") for c in classes)
    assert rows, "the reference world has 3,085 belt pieces"

    seen_chains = set()
    for row in rows:
        assert 4 <= len(row) <= 5, row[:2]
        chain, ci, points, actor = row[0], row[1], row[2], row[3]
        seen_chains.add(chain)
        assert 0 <= ci < len(classes)
        assert len(points) >= 2, "a polyline needs two points"
        for p in points:
            assert len(p) == 3 and all(isinstance(c, int) for c in p), p
        # Schema 20. -1 is the only permitted miss, and where there IS an index it points at a
        # CONVEYOR: an actor index that resolved to a smelter would be a well-formed join to
        # the wrong thing, which is exactly what naming a run by geometry used to risk.
        assert actor == -1 or 0 <= actor < len(actors), actor
        if actor >= 0:
            assert actors[actor].startswith("Build_Conveyor"), actors[actor]
    assert seen_chains == set(range(len(seen_chains))), "chain indices are dense and start at 0"
    assert [r[0] for r in rows] == sorted(r[0] for r in rows), "a chain's rows are contiguous"


def test_the_belt_actor_join_is_the_saves_own_identity_and_not_a_nearest_match(projection):
    """Schema 20's column, held to the standard the geometric match it replaced could not meet.

    Matching a chain to a run by geometry was measured at 75% unique and rejected for it: an
    id that is right three times in four, printed as a fact, sends a reader to the wrong belt.
    The actor index is not a match at all -- the chain names its pieces by INSTANCE and the
    graph interns that same instance -- so the property to pin is not accuracy but INJECTIVITY
    and agreement: no two pieces may claim one actor, and every claim must land on a conveyor.

    3,083 of the 3,085 pieces resolve. The 2 that do not are a pair of parallel Mk3 belts the
    save records no coupling for at either end, so they are in ``graph["actors"]`` nowhere --
    absent from the graph, not mismatched in it.
    """
    rows = projection["belts"]["segments"]
    actors = projection["graph"]["actors"]
    # The table's OWN class list, not a ``Build_Conveyor`` prefix: that prefix also catches
    # the splitters and mergers, which are nodes a run ends at rather than pieces of one.
    belt_classes = set(projection["belts"]["classes"])

    def cls_of(actor):
        head, _, tail = actor.rpartition("_")
        return head if tail.isdigit() else actor

    claimed = [row[3] for row in rows if row[3] >= 0]
    assert len(claimed) == 3083
    assert len(set(claimed)) == len(claimed), "two belt pieces claiming one actor"
    assert all(cls_of(actors[i]) in belt_classes for i in claimed)

    # And the other direction. 11 conveyors the graph names have no row here: the FICSMAS
    # gift-tree belts, which the game builds without an ``FGConveyorChainActor``, so there is
    # no drawn line for them to be joined to. ``tests/domain/world/test_logistics`` pins what they are attached
    # to; what matters here is that the shortfall is one-sided -- the rows do not name a
    # conveyor the graph has never heard of.
    conveyors = {i for i, a in enumerate(actors) if cls_of(a) in belt_classes}
    assert len(conveyors) == 3094
    assert set(claimed) <= conveyors
    assert len(conveyors - set(claimed)) == 11


def test_belts_are_placed_in_the_world_and_not_in_the_chains_own_frame(projection):
    """The mistake this field is one line away from, and it looks like nothing downstream.

    A chain's spline is stored relative to the chain actor, so the points arrive within a few
    tens of metres of the origin -- a perfectly well-formed polyline drawn on empty ocean, a
    kilometre or more from the factory. Checking them against the foundations is what catches
    it: belts run over floor.
    """
    points = [p for row in projection["belts"]["segments"] for p in row[2]]
    rows = projection["structures"]["instances"]
    for axis in (0, 1, 2):
        lo = min(r[axis + 1] for r in rows) - 20_000
        hi = max(r[axis + 1] for r in rows) + 20_000
        assert lo <= min(p[axis] for p in points), f"belts run off axis {axis} at the low end"
        assert max(p[axis] for p in points) <= hi, f"belts run off axis {axis} at the high end"
    # And they are nowhere near the origin, which is where the un-translated points would be.
    assert min(math.hypot(p[0], p[1]) for p in points) > 20_000


def test_belts_come_out_in_travel_order(projection):
    """Segments are stored output-first and are emitted reversed, so a chain reads as a line.

    The falsifier is in the test: put the rows of a chain back in file order and the joins
    that were coincident become thousands of centimetres apart. The 80 joins that are not
    exactly coincident are the conveyor-lift junctions ``pioneersav.trailers`` measures at
    200/300/400 cm of offset range with no spline behind it.
    """
    by_chain: dict[int, list] = defaultdict(list)
    for row in projection["belts"]["segments"]:
        by_chain[row[0]].append(row[2])

    joins = [math.dist(a[-1], b[0]) for segs in by_chain.values() for a, b in pairwise(segs)]
    assert len(joins) > 1000
    assert max(joins) <= 401.0, "a chain breaks apart in the middle"
    assert sum(1 for g in joins if g == 0.0) / len(joins) > 0.9

    backwards = [
        math.dist(a[-1], b[0]) for segs in by_chain.values() for a, b in pairwise(segs[::-1])
    ]
    assert sorted(backwards)[len(backwards) // 2] > 1000.0, "file order is not travel order"


def test_lifts_are_carried_as_belts_are(projection):
    """A conveyor lift is the vertical connector between two floors, and §16b needs it.

    It is not a separate record: a lift is a segment of an ordinary chain, told apart by its
    class alone, which is why the class is interned per segment and not per chain. §16b is in
    ``docs/parked.md``.
    """
    classes = projection["belts"]["classes"]
    lifts = [r for r in projection["belts"]["segments"] if "Lift" in classes[r[1]]]
    assert len(lifts) == 302, "the reference world's conveyor lifts"
    rises = [abs(r[2][-1][2] - r[2][0][2]) for r in lifts]
    assert sum(1 for r in rises if r > 100) > len(rises) // 2, "a lift should mostly go up"


def test_belts_out_of_real_trailing_bytes(projection):
    """``_belts`` against the committed chain records, not against a mock of them.

    ``fixtures/save_trailers.bin`` holds two real ``FGConveyorChainActor`` trailers, which is
    what makes this a test of the decoder-to-projection seam rather than of a hand-built list.
    """
    chains = trailer_chains()
    assert chains, "no chain records in the trailer fixture"
    out = _belts([([1000.0, 2000.0, 3000.0], Chain(info)) for info in chains], {}, Drops())
    assert out["classes"] and out["segments"]
    assert {r[0] for r in out["segments"]} == set(range(len(chains)))
    for row in out["segments"]:
        assert out["classes"][row[1]].startswith("Build_Conveyor")
        assert len(row[2]) >= 2
        assert row[3] == -1, "an empty actor index names nothing, rather than naming actor 0"

    # The same records with the actor at the origin: every point moves by exactly the offset,
    # which is the whole of what the frame correction does.
    at_origin = _belts([([0.0, 0.0, 0.0], Chain(info)) for info in chains], {}, Drops())
    for moved, base in zip(out["segments"], at_origin["segments"]):
        assert [[p[0] - 1000, p[1] - 2000, p[2] - 3000] for p in moved[2]] == base[2]


def test_a_chain_that_will_not_decode_costs_that_chain_and_not_the_save():
    """Belts are new, so a save that projected yesterday must still project today.

    A trailer decodes lazily, so a malformed one raises here rather than at the save boundary.
    Letting it out would turn one unreadable belt into a world the server cannot open at all.
    """
    chains = trailer_chains()
    drops = Drops()
    out = _belts(
        [
            ([0.0, 0.0, 0.0], UnreadableChain(None)),
            *(([0.0, 0.0, 0.0], Chain(info)) for info in chains),
        ],
        {},
        drops,
    )
    assert {r[0] for r in out["segments"]} == set(range(len(chains))), "indices stay dense"
    # And it SAYS it cost that chain. A projection quietly one belt short is the failure
    # this channel exists for: the payload alone cannot be told from a world with one belt
    # fewer in it.
    assert sum(drops.values()) == 1
    assert "trailing bytes" in next(iter(drops))


def test_a_chain_of_nothing_recognisable_is_dropped_rather_than_raising():
    """Same reasoning as ``_placed``: this runs on whatever the decoder produced.

    Each of these is also one count in ``warnings`` -- dropping is right, dropping in
    silence is what made a torn save look like a smaller world.
    """
    empty = {"classes": [], "segments": []}
    drops = Drops()
    assert _belts([], {}, drops) == empty
    assert sum(drops.values()) == 0, "nothing in, nothing dropped"
    assert _belts([(None, Chain(None))], {}, drops) == empty
    assert _belts([([0.0, 0.0, 0.0], Chain([1, 2]))], {}, drops) == empty
    assert _belts([(["x", 0.0, 0.0], Chain([1, 2, []]))], {}, drops) == empty
    assert sum(drops.values()) == 3, "three unreadable chains, three counted"


def test_a_belts_class_comes_off_the_instance_name_with_its_C_intact():
    """The class names have to match the ones ``structures`` and ``building_counts`` use."""
    assert (
        _conveyor_class("Persistent_Level:PersistentLevel.Build_ConveyorBeltMk3_C_1264")
        == "Build_ConveyorBeltMk3_C"
    )
    assert _conveyor_class("Build_ConveyorLiftMk4_C_2147") == "Build_ConveyorLiftMk4_C"
    assert _conveyor_class("") == ""


# ------------------------------------------------------------------- belt attachments


def test_the_splitters_and_mergers_are_kept_and_the_ceiling_mounts_are_not(projection):
    """The class filter, held against the census rather than against itself.

    A splitter is an ordinary ``Build_`` actor and the projection used to build its record and
    drop it on the floor, so a belt-only map had a hole at every junction. It is kept now -- and
    the interesting half is what is NOT: ``Build_ConveyorCeilingAttachment_C`` shares the word
    and is a pole a belt hangs from, not a piece the items pass through. A filter that matched
    ``ConveyorAttachment`` would swallow all 95 of them, and they would draw the same square
    while meaning something else.
    """
    counts = projection["building_counts"]
    rows = projection["attachments"]
    kept = {c for c in counts if any(h in c for h in _ATTACHMENT_HINTS)}
    assert kept == {r["cls"] for r in rows}
    assert sum(counts[c] for c in kept) == len(rows), "every one in the census is a row"
    assert len(rows) > 800, "the reference world splits and merges a great deal"
    assert "Build_ConveyorCeilingAttachment_C" not in {r["cls"] for r in rows}
    assert counts["Build_ConveyorCeilingAttachment_C"] == 95, "and they ARE in the world"


def test_an_attachment_is_a_placement_and_carries_one(projection):
    """What a splitter is, is where it stands and which way it faces.

    Same record shape as a machine's, minus the fields a splitter has no business having: it
    runs no recipe and holds no clock, and a row claiming either would be inventing one.
    """
    rows = projection["attachments"]
    for r in rows:
        assert set(r) >= {"cls", "instance", "pos", "yaw"}
        assert not {"recipe", "clock", "node", "fuel"} & set(r)
        assert len(r["pos"]) == 3
        assert r["yaw"] is None or -180 <= r["yaw"] <= 180
    # In the world with everything else, not at the origin: the record is the actor header's
    # own transform, and a splitter drawn at (0, 0) would be out at sea with the untranslated
    # pipes above.
    xs = [r["pos"][0] for r in rows]
    ys = [r["pos"][1] for r in rows]
    structures = projection["structures"]["instances"]
    assert min(s[1] for s in structures) - 20_000 <= min(xs)
    assert max(xs) <= max(s[1] for s in structures) + 20_000
    assert min(s[2] for s in structures) - 20_000 <= min(ys)
    assert max(ys) <= max(s[2] for s in structures) + 20_000


def test_an_attachment_is_in_exactly_one_of_the_projections_placement_lists(projection):
    """The belts layer draws these; the machines layer must not draw them too.

    Checked at the projection rather than at the endpoint, because this is where a class ends
    up in a list: the ``elif`` chain is what makes the two claims -- "the map has them" and "the
    map has them once" -- the same claim.
    """
    attached = {r["instance"] for r in projection["attachments"]}
    assert attached
    for key in PLACED:
        assert not attached & {r["instance"] for r in projection[key]}
