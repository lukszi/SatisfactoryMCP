"""Schemas 12 to 15: the placement yaw, the belt and pipe splines, the splitters and mergers
those belts pass through, the CURVE through the spline points, and the containers.

All of it is data the parser already decoded and the projection threw away, and all of it is
here for the same reason -- a client could only draw the world axis-aligned, beltless,
unplumbed, cornered and empty, so an angled platform came out as a staircase, a factory came out
as a scatter of rectangles, a curved belt came out as a fan of chords, and 151 containers
holding 52 kinds of thing came out as nothing at all.

**What is actually worth testing about a coordinate.** That a number is present says nothing;
the failure modes are all silent and all about convention. A yaw with the wrong sign, or read
off the wrong axis, is still a plausible float on every record. A belt or pipe spline left in
its actor's own frame is still a plausible polyline -- 1,500 m from where the thing is. So the
tests below check the numbers against *other* numbers in the same save: yaw against the
direction neighbouring foundations are actually laid out in, belt and pipe points against where
the buildings are. Each one fails on the mistake it is named for.

Fixture-only, plus the committed trailer bytes, so this runs with no game install.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise

import pytest

from pioneersav import ObjectReference, ParseError, Reader, read_trailer
from pioneersav.trailers import CONVEYOR_CHAIN
from satisfactory_mcp.core.saveio.extract import (
    _ATTACHMENT_HINTS,
    FLUID_BUFFER_CLASSES,
    PIPE_CLASSES,
    STORAGE_CLASSES,
    TANGENT_EPS_CM,
    Drops,
    _belts,
    _bulge,
    _conveyor_class,
    _pipes,
    _storage,
    yaw_of,
)
from tests.support.paths import FIXTURES

#: The 8 m grid every foundation sits on. Two pieces exactly this far apart are neighbours in
#: one row of one platform, which is what makes their bearing a measurement of that platform's
#: rotation rather than of anything else.
TILE_CM = 800.0

#: Records that carry a placed building's own transform.
PLACED = ("machines", "extractors", "generators")


class Chain:
    """A stand-in conveyor chain: ``_belts`` reads nothing off an actor but this attribute.

    The camel case is the parser's spelling, not a slip -- ``ParsedObject`` exposes
    ``actorSpecificInfo``, and a stand-in has to answer to the same name.
    """

    def __init__(self, info) -> None:
        self._info = info

    @property
    def actorSpecificInfo(self):
        return self._info


class Unreadable(Chain):
    """A chain whose trailing bytes raise on access, the way a torn trailer does."""

    @property
    def actorSpecificInfo(self):
        raise ParseError("at body offset 0: conveyor chain left 12 trailing bytes unread")


def _trailer_chains() -> list:
    """The real chain records out of ``fixtures/save_trailers.bin``.

    Container format, as ``tests/pioneersav/test_trailers`` documents it: an int32 count, then per
    entry a length-prefixed class path, an int32 length and that many bytes.
    """
    path = FIXTURES / "save_trailers.bin"
    if not path.is_file():
        pytest.skip("trailer fixture not committed")
    raw = path.read_bytes()
    r = Reader(raw)
    out = []
    for _ in range(r.i32()):
        cls = r.string()
        blob = r.bytes(r.i32())
        if cls == CONVEYOR_CHAIN:
            out.append(read_trailer(CONVEYOR_CHAIN, blob, 0, len(blob)))
    return out


# --------------------------------------------------------------------------------- yaw


def test_yaw_reads_the_axis_and_the_sign_the_world_is_actually_built_on(projection):
    """The convention, held against the geometry instead of against the formula.

    Foundations that share a yaw and sit exactly one 8 m tile apart are two pieces in one row
    of one platform, so the bearing from one to the other IS that platform's rotation, modulo
    the 90 degrees of the square grid. On the reference world 4,631 of 8,347 pieces sit at a
    yaw that is not a multiple of 90 -- the angled platforms this whole field exists for -- so
    there is plenty to check against.

    This is the test that fails if the yaw is negated, taken about X or Y, or measured from the
    wrong axis: every one of those still produces a tidy float on every record, and all of them
    disagree with where the pieces are.
    """
    classes = projection["structures"]["classes"]
    rows = [r for r in projection["structures"]["instances"] if "Foundation" in classes[r[0]]]
    groups: dict[float, list] = defaultdict(list)
    for row in rows:
        groups[row[4]].append(row)

    angled = sorted(
        ((yaw, g) for yaw, g in groups.items() if yaw % 90 and len(g) >= 100),
        key=lambda kv: -len(kv[1]),
    )[:4]
    assert len(angled) == 4, "the fixture no longer has four angled platforms to check against"

    for yaw, group in angled:
        bearings = []
        for i, a in enumerate(group[:300]):
            for b in group[i + 1 : 300]:
                dx, dy = b[1] - a[1], b[2] - a[2]
                if abs(math.hypot(dx, dy) - TILE_CM) < 2.0:
                    bearings.append(math.degrees(math.atan2(dy, dx)) % 90.0)
        assert len(bearings) >= 50, f"yaw {yaw}: only {len(bearings)} tile-spaced pairs"
        worst = max(abs(b - yaw % 90.0) for b in bearings)
        # The bound is the projection's own rounding floor, not a fudge: positions are whole
        # centimetres, so a bearing over an 800 cm baseline can be up to
        # degrees(atan(2/800)) = 0.143 off. Against the raw float positions the same pairs
        # agree to 0.002. The mistakes this catches miss by 20 to 140 degrees.
        assert worst < 0.15, f"yaw {yaw}: a tile-spaced neighbour lies {worst} degrees off it"


def test_every_placed_record_carries_a_yaw(projection):
    """Present on all four record kinds, and always present rather than only when non-zero.

    An absent yaw has to mean "this projection predates schema 12", never "this building is
    unrotated" -- a consumer cannot tell those apart after the fact, and the world is full of
    genuinely unrotated buildings.

    Since schema 16 the KEY is always there and its VALUE may be null, which is the third
    claim: this placement's rotation would not decode. None is here, on this save -- asserted,
    because that is what makes the range checks below statements about every record rather
    than about the ones that happened to read -- and the projection says so in ``warnings``
    when there are, rather than letting an undecodable header pass for a square one.
    """
    for key in PLACED:
        records = projection[key]
        assert records, f"{key} is empty in the fixture"
        assert all("yaw" in r for r in records), f"{key}: a record with no yaw"
        assert all(r["yaw"] is not None for r in records), f"{key}: an unreadable rotation"
        assert all(-180.0 <= r["yaw"] <= 180.0 for r in records), f"{key}: yaw out of range"
        assert any(r["yaw"] for r in records), f"{key}: every yaw is zero, which is not this save"

    rows = projection["structures"]["instances"]
    assert rows and all(len(r) == 5 for r in rows), "a structure row without its yaw column"
    assert all(r[4] is not None and -180.0 <= r[4] <= 180.0 for r in rows)
    assert sum(1 for r in rows if r[4] % 90) == 4631, "the angled pieces of the reference world"
    assert not [w for w in projection["warnings"] if "rotation" in w], (
        "a save with no unreadable rotation must not announce one"
    )


def test_a_half_turn_has_one_spelling(projection):
    """``-180`` and ``180`` are the same facing, and only one of them is emitted.

    Actor headers store the quaternion as float32, so a half turn lands a hair below 180 and
    rounds to ``-180.0``; lightweight buildables store float64 and land on ``180.0``. Without
    the fold the same rotation reads as two different numbers depending on which list a piece
    came from, and anything grouping by yaw sees both.
    """
    assert yaw_of((0.0, 0.0, 1.0, -4.371139e-08)) == 180.0, "the float32 half turn"
    assert yaw_of((0.0, 0.0, 1.0, 0.0)) == 180.0
    yaws = {r["yaw"] for key in PLACED for r in projection[key]}
    yaws |= {r[4] for r in projection["structures"]["instances"]}
    assert 180.0 in yaws and -180.0 not in yaws


def test_yaw_of_a_quaternion_turns_x_towards_y():
    """The unit statement of the convention the fixture test measures."""
    assert yaw_of((0.0, 0.0, 0.0, 1.0)) == 0.0, "identity is unrotated, not unknown"
    assert yaw_of((0.0, 0.0, math.sin(math.radians(45)), math.cos(math.radians(45)))) == 90.0
    assert yaw_of((0.0, 0.0, math.sin(math.radians(-45)), math.cos(math.radians(-45)))) == -90.0
    assert yaw_of((0.0, 0.0, math.sin(math.radians(-10)), math.cos(math.radians(-10)))) == -20.0


def test_a_rotation_that_is_not_one_costs_a_yaw_and_not_the_projection():
    """``yaw_of`` runs on whatever the decoder produced, so it must answer for any shape.

    And the answer is None, not 0.0. Schema 16: zero is a real bearing that most of this
    world genuinely has, so returning it for a quaternion that would not read published a
    measurement nobody made, mixed in with 17,500 that were made and indistinguishable from
    them after the fact. Null is the claim ``serial._yaw`` and the map already handle -- drawn
    axis-aligned, labelled "facing: unknown" -- and it is the difference between the two.
    """
    assert yaw_of(None) is None
    assert yaw_of(()) is None
    assert yaw_of((0.0, 0.0, 1.0)) is None
    assert yaw_of(("x", "y", "z", "w")) is None
    # Still a float where there is one, and 0.0 keeps meaning axis-aligned.
    assert yaw_of((0.0, 0.0, 0.0, 1.0)) == 0.0


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
    chains = _trailer_chains()
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
    chains = _trailer_chains()
    drops = Drops()
    out = _belts(
        [
            ([0.0, 0.0, 0.0], Unreadable(None)),
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


# ------------------------------------------------------------------------------- pipes


def _spline(*points) -> list:
    """An ``mSplineData`` the way the parser hands it over: a list of struct entries, each
    ``[values, propertyTypes]``, with a ``Location`` among the values.

    The tangents are in here because the real property has them and ``_pipes`` has to ignore
    them: a reader that took field 0 positionally rather than by name would pass every test
    written against a Location-only stand-in and draw the world's curvature as its geometry.
    """
    out = []
    for p in points:
        values = [
            ["Location", list(p)],
            ["ArriveTangent", [0.0, 50.0, 0.0]],
            ["LeaveTangent", [0.0, 50.0, 0.0]],
        ]
        types = [
            [n, "StructProperty", 1, "Vector", 1, "/Script/CoreUObject", 0, 8] for n, _ in values
        ]
        out.append([values, types])
    return out


def test_the_pipes_key_is_interned_polylines_in_whole_centimetres(projection):
    """The shape, field by field: ``[networkIndex, classIndex, [[x, y, z], ...], actorIndex]``."""
    pipes = projection["pipes"]
    classes = pipes["classes"]
    networks = pipes["networks"]
    rows = pipes["segments"]
    actors = projection["graph"]["actors"]
    assert set(classes) <= set(PIPE_CLASSES) and classes
    assert rows, "the reference world has 503 pipes"
    assert networks, "and 19 pipe networks"

    for row in rows:
        # Positional with a width guard, like the belts next door: schema 15 puts the tangents
        # in a fifth column on the pipes that bend, and a straight pipe still has four.
        assert 4 <= len(row) <= 5, row[:2]
        net, ci, points, actor = row[0], row[1], row[2], row[3]
        assert 0 <= ci < len(classes)
        assert -1 <= net < len(networks)
        assert len(points) >= 2, "a polyline needs two points"
        for p in points:
            assert len(p) == 3 and all(isinstance(c, int) for c in p), p
        # Schema 14. The whole of it: an index INTO an existing list, never past its end.
        assert -1 <= actor < len(actors)


def test_a_pipes_actor_index_names_that_very_pipe_in_the_connection_graph(projection):
    """The join schema 14 exists for, checked against the class it claims to point at.

    The segment says which ``graph["actors"]`` entry owns it. If that index were off by one --
    or interned after the graph's own list was snapshotted, which is the bug the read-only
    lookup in ``extract`` prevents -- it would still be a valid index and would still resolve,
    to the wrong actor. So it is checked by NAME: the entry it points at has to be a pipeline
    of the very class the row's own ``classIndex`` interns.
    """
    pipes = projection["pipes"]
    classes = pipes["classes"]
    actors = projection["graph"]["actors"]
    unclaimed = 0
    for row in pipes["segments"]:
        ci, actor = row[1], row[3]
        if actor < 0:
            unclaimed += 1  # a pipe connected to nothing at all: legal, and none here
            continue
        assert actors[actor].startswith(classes[ci] + "_"), (actors[actor], classes[ci])
    assert unclaimed == 0, "every pipe on this world is plugged into something"
    # And distinct, which a shared or defaulted index would break.
    claimed = [row[3] for row in pipes["segments"]]
    assert len(set(claimed)) == len(claimed)


def test_a_pipe_is_a_fluid_pipe_and_a_hypertube_is_not(projection):
    """``Build_PipeHyper_C`` carries the identical ``mSplineData`` and belongs to no plumbing.

    This is the test that fails if the class filter becomes a substring match on ``Pipe``:
    the reference world has 60 hypertube segments and 215 pipeline supports, both of which
    would then be drawn as pipes, and one of them even has geometry to draw.
    """
    classes = set(projection["pipes"]["classes"])
    assert "Build_PipeHyper_C" not in classes
    assert "Build_PipelineSupport_C" not in classes
    assert projection["building_counts"]["Build_PipeHyper_C"] == 60, "and they are in the world"
    # Every pipeline class the census found IS drawn, so the filter is not merely narrow.
    built = {c for c in projection["building_counts"] if c in PIPE_CLASSES}
    assert classes == built
    assert sum(projection["building_counts"][c] for c in built) == len(
        projection["pipes"]["segments"]
    ), "every pipe in the census is a row"


def test_pipes_are_placed_in_the_world_and_not_in_the_actors_own_frame(projection):
    """The mistake this field is one line away from, and it looks like nothing downstream.

    Every pipe's spline is stored relative to its own actor -- the first point of all 503 is
    exactly ``(0, 0, 0)`` -- so an untranslated network is 503 perfectly well-formed polylines
    piled on the map origin, out at sea. Checking them against the foundations is what catches
    it, the same way the belts above are checked.
    """
    points = [p for row in projection["pipes"]["segments"] for p in row[2]]
    rows = projection["structures"]["instances"]
    for axis in (0, 1, 2):
        lo = min(r[axis + 1] for r in rows) - 20_000
        hi = max(r[axis + 1] for r in rows) + 20_000
        assert lo <= min(p[axis] for p in points), f"pipes run off axis {axis} at the low end"
        assert max(p[axis] for p in points) <= hi, f"pipes run off axis {axis} at the high end"
    assert min(math.hypot(p[0], p[1]) for p in points) > 20_000


def test_every_pipe_belongs_to_a_network_that_names_a_fluid(projection):
    """What a pipe has and a belt does not: the game's own answer to what is inside it."""
    networks = projection["pipes"]["networks"]
    rows = projection["pipes"]["segments"]
    assert all(n["fluid"] for n in networks), "a network with no fluid on this world"
    assert all(isinstance(n["id"], int) for n in networks)
    assert all(r[0] >= 0 for r in rows), "every pipe here is claimed by a network"
    fluids = {networks[r[0]]["fluid"] for r in rows}
    assert len(fluids) > 1 and all(f.startswith("Desc_") for f in fluids)


def test_no_pipe_is_vertical_so_none_needs_a_glyph(projection):
    """The measurement the client's drawing rests on, and the counterpart of the lifts above.

    A belt network needs a ring for its lifts because a lift's top-down polyline is a single
    point. Pipes have no such piece: the tightest of the 503 still spans 11.6 cm horizontally,
    so every one of them is drawable as a line and the layer needs no second glyph.
    """
    spans = [
        math.dist(
            (min(p[0] for p in pts), min(p[1] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts)),
        )
        for pts in (row[2] for row in projection["pipes"]["segments"])
    ]
    assert min(spans) > 10.0, "a pipe with no horizontal extent would draw as nothing"


def test_pipes_are_translated_by_their_actor_and_not_rotated_by_it():
    """The frame correction itself, isolated: move the actor and every point moves with it.

    All 18,069 pipeline actors across the 66 saves on this disk carry an identity rotation, so
    the correction is a translation and nothing else. This pins that: the same spline read at
    two actor positions differs by exactly the offset, on every axis, with no rounding drift --
    which is what makes the whole-centimetre rounding commutative with the translation.
    """
    spline = _spline((0.0, 0.0, 0.0), (0.0, 100.0, 0.0), (0.0, 100.0, 250.0))
    nets = [(3, "Desc_Water_C", ["Persistent_Level:PersistentLevel.Build_Pipeline_C_1"])]
    base = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (0, 0, 0),
                spline,
            )
        ],
        nets,
        {"Build_Pipeline_C_1": 0},
        Drops(),
    )
    moved = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (1000.5, -2000.5, 3000.0),
                spline,
            )
        ],
        nets,
        {"Build_Pipeline_C_1": 0},
        Drops(),
    )
    assert base["segments"][0][2] == [[0, 0, 0], [0, 100, 0], [0, 100, 250]]
    assert [[p[0] - 1000, p[1] + 2000, p[2] - 3000] for p in moved["segments"][0][2]] == base[
        "segments"
    ][0][2]
    # And the network the member list claims it for, resolved by instance name.
    assert base["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]
    assert base["segments"][0][0] == 0


def test_a_pipe_no_network_claims_is_still_drawn():
    """``-1``, not dropped: an unclaimed pipe is a pipe on real ground whose contents are
    unknown, and a half-built or drained network is exactly how one arises."""
    out = _pipes(
        [
            (
                "Build_PipelineMK2_C",
                "x.Build_PipelineMK2_C_9",
                (0, 0, 0),
                _spline((0, 0, 0), (0, 800, 0)),
            )
        ],
        [(3, "Desc_Water_C", ["x.Build_Pipeline_C_1"])],
        {"Build_PipelineMK2_C_9": 4},
        Drops(),
    )
    assert out["segments"] == [[-1, 0, [[0, 0, 0], [0, 800, 0]], 4]]
    assert out["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]


def test_a_pipe_of_nothing_recognisable_is_dropped_rather_than_raising():
    """Same reasoning as ``_belts``: this runs on whatever the property decoder produced.

    And the same second claim: each drop is one count in ``warnings``, so a save whose
    splines have stopped decoding says so rather than publishing a world with no pipes.
    """
    empty = {"classes": [], "networks": [], "segments": []}
    drops = Drops()
    assert _pipes([], [], {}, drops) == empty
    assert sum(drops.values()) == 0, "nothing in, nothing dropped"
    assert _pipes([("Build_Pipeline_C", "i", (0, 0, 0), None)], [], {}, drops) == empty
    assert (
        _pipes([("Build_Pipeline_C", "i", None, _spline((0, 0, 0), (1, 1, 1)))], [], {}, drops)
        == empty
    )
    assert _pipes([("Build_Pipeline_C", "i", ("x", 0, 0), _spline((0, 0, 0)))], [], {}, drops) == (
        empty
    )
    # One point is not a route, the same bar the belts set.
    assert (
        _pipes([("Build_Pipeline_C", "i", (0, 0, 0), _spline((0, 0, 0)))], [], {}, drops) == empty
    )
    assert sum(drops.values()) == 4, "four unreadable pipes, four counted"
    # A struct with no Location among its fields costs that point, not the pipe.
    point_drops = Drops()
    assert _pipes(
        [
            (
                "Build_Pipeline_C",
                "i",
                (0, 0, 0),
                [[[["ArriveTangent", [1, 2, 3]]], []], *_spline((0, 0, 0), (0, 400, 0))],
            )
        ],
        [],
        {},
        point_drops,
    )["segments"] == [[-1, 0, [[0, 0, 0], [0, 400, 0]], -1]]
    assert sum(point_drops.values()) == 1, "the point, and only the point"
    # A network whose id is not an integer keeps its fluid and loses its id.
    assert _pipes([], [(None, "Desc_Water_C", [])], {}, Drops())["networks"] == [
        {"id": None, "fluid": "Desc_Water_C"}
    ]


# --------------------------------------------------------------------- spline curvature


def _hermite(p0, m0, p1, m1, t):
    """One point on the cubic Hermite span, the way a client is expected to draw it."""
    t2, t3 = t * t, t * t * t
    return [
        (2 * t3 - 3 * t2 + 1) * p0[k]
        + (t3 - 2 * t2 + t) * m0[k]
        + (-2 * t3 + 3 * t2) * p1[k]
        + (t3 - t2) * m1[k]
        for k in range(3)
    ]


def _departure(p0, m0, p1, m1, n=128):
    """How far the span's curve actually gets from the straight line between its ends.

    Sampled, deliberately: this is the quantity ``_bulge`` claims to bound, and bounding it
    with the same arithmetic that computes it would test nothing at all.
    """
    v = [p1[k] - p0[k] for k in range(3)]
    span2 = sum(x * x for x in v)
    worst = 0.0
    for step in range(1, n):
        q = _hermite(p0, m0, p1, m1, step / n)
        if span2 < 1e-12:
            worst = max(worst, math.dist(q, p0))
            continue
        u = min(1.0, max(0.0, sum((q[k] - p0[k]) * v[k] for k in range(3)) / span2))
        worst = max(worst, math.dist(q, [p0[k] + u * v[k] for k in range(3)]))
    return worst


#: Where the schema-15 curve column sits in each table's row. The SAME index in both since
#: schema 20 gave a belt the actor column a pipe has had since 14, which is why this is one
#: constant and not a per-key pair.
_SPANS_AT = 4


def _routes(projection):
    """``(key, points, spans)`` for every belt piece and pipe; ``spans`` is ``[]`` if straight.

    One walk over both keys, because the column means the same thing in both and now sits in
    the same place in both.
    """
    for key in ("belts", "pipes"):
        for row in projection[key]["segments"]:
            yield key, row[2], (row[_SPANS_AT] if len(row) > _SPANS_AT else [])


def test_a_route_carries_one_curve_entry_per_span_and_nothing_more(projection):
    """The shape of the schema-15 column: per SPAN, not per point, and ints throughout.

    Per span is the claim worth pinning. A per-point column would be the obvious shape and is
    the wrong one: a span needs the LEAVE tangent of the point behind it and the ARRIVE
    tangent of the point ahead, so the pairing is what a consumer needs and the pairing is
    what is stored -- which also drops the two vectors nothing can use, the arrive of the
    first point and the leave of the last.
    """
    seen = {"belts": 0, "pipes": 0}
    for key, points, spans in _routes(projection):
        if not spans:
            continue
        seen[key] += 1
        assert len(spans) == len(points) - 1, "one entry per span, not per point"
        assert any(spans), "a curve column with no curve in it is a column nobody needed"
        for entry in spans:
            if entry == 0:
                continue
            assert len(entry) == 6, entry
            assert all(isinstance(c, int) for c in entry), entry
    assert seen == {"belts": 966, "pipes": 296}, "the routes of the reference world that bend"


def test_a_straight_run_is_the_row_without_the_curve_column(projection):
    """The promise that makes the curve column free for everything that was already straight.

    A route with no bend in it is four columns wide in both tables and a bending one is five,
    so a straight run is drawn from the numbers before the column existed, with no client-side
    tolerance deciding so. 2,119 of the world's 3,085 belt pieces and 207 of its 503 pipes are
    in that state.
    """
    plain = {"belts": 0, "pipes": 0}
    for key in ("belts", "pipes"):
        for row in projection[key]["segments"]:
            assert len(row) in (_SPANS_AT, _SPANS_AT + 1), (key, len(row))
            if len(row) == _SPANS_AT:
                plain[key] += 1
    assert plain == {"belts": 2119, "pipes": 207}
    # And a two-point route -- the commonest thing in the world -- is overwhelmingly one of
    # them: a straight belt is where "no tangents at all" has to hold if it holds anywhere.
    two_point = [(p, s) for _k, p, s in _routes(projection) if len(p) == 2]
    assert len(two_point) == 2461
    assert sum(1 for _p, s in two_point if not s) == 2259


def test_a_flat_span_inside_a_bending_route_stores_zero_rather_than_its_tangents(projection):
    """The saving is per SPAN, not per route, which is what keeps an elbow cheap.

    A six-point pipe elbow is one bend and five spans, and most of those spans are straight.
    Storing them as ``0`` rather than as six integers apiece is a large part of the difference
    between the measured +5.5% projection and a +12% one, and it is also what lets a client
    take the plain two-point path through the straight parts of a route that is not straight.
    """
    inner = [s for _k, _p, spans in _routes(projection) for s in spans]
    assert len(inner) == 2998 + 1143, "spans belonging to a route that bends somewhere"
    assert sum(1 for s in inner if s == 0) == 1740 + 721, "and most of them are still straight"


def test_the_bulge_bound_never_understates_how_far_a_curve_leaves_its_chord(projection):
    """The one property ``_bulge`` must have, checked against a 128-point tessellation.

    It is a bound and not a measurement on purpose: overstating costs bytes, understating
    silently flattens a bend the save does record. So the test is one-sided -- the bound has
    to be at least the sampled truth on every span the fixture carries -- plus a looseness
    ceiling, because a bound that simply returned infinity would pass the first half and would
    carry every span in the world.
    """
    ratios = []
    worst = 0.0
    for _key, points, spans in _routes(projection):
        for i, entry in enumerate(spans):
            if entry == 0:
                continue
            leave, arrive = entry[:3], entry[3:]
            truth = _departure(points[i], leave, points[i + 1], arrive)
            bound = _bulge(points[i], leave, points[i + 1], arrive)
            assert bound >= truth - 1e-6, (bound, truth, points[i], points[i + 1])
            worst = max(worst, truth)
            if truth > 0.01:
                ratios.append(bound / truth)
    assert ratios
    assert max(ratios) < 4.0, f"the bound is {max(ratios):.1f}x the truth and carries dead weight"
    # And the kept spans are worth keeping: the biggest departs its chord by ten metres, which
    # is the belt bend that used to be drawn as a straight line ten metres away from itself.
    assert worst > 1000.0, worst


def test_a_zero_length_span_is_bounded_by_its_tangents_alone():
    """Coincident control points: there is no chord, so all of both tangents is sideways.

    116 of the reference save's spans are exactly this -- the zero-length joint where a
    conveyor lift meets the belt it feeds -- and dividing by the chord there is the division
    by zero a bound written only for the general case walks into.
    """
    assert _bulge([0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0]) == 0.0
    quiet = _bulge([5, 5, 5], [0, 1, 0], [5, 5, 5], [0, 1, 0])
    assert 0 < quiet < TANGENT_EPS_CM, quiet
    assert _bulge([5, 5, 5], [0, 500, 0], [5, 5, 5], [0, 500, 0]) > TANGENT_EPS_CM


def test_a_straight_span_carries_nothing_however_long_its_tangents_are():
    """Tangents ALONG the chord do not bend the curve, and the bound has to know that.

    This is the case that decides whether the feature is affordable at all, because it is the
    game's commonest: the save stores half the chord as both tangents on every straight run,
    which a test comparing tangent length against chord length would call curved and carry. It
    puts the curve on the line at a non-uniform speed, and a drawn line has no speed.
    """
    for scale in (0.25, 0.5, 1.0):
        m = [0, int(800 * scale), 0]
        assert _bulge([0, 0, 0], m, [0, 800, 0], m) < TANGENT_EPS_CM, scale
    # Sideways by a hair is still nothing; sideways by two metres is not.
    assert _bulge([0, 0, 0], [1, 400, 0], [0, 800, 0], [1, 400, 0]) < TANGENT_EPS_CM
    assert _bulge([0, 0, 0], [200, 400, 0], [0, 800, 0], [-200, 400, 0]) > TANGENT_EPS_CM
    # And a tangent long enough to overshoot the far end IS a departure, even though it is
    # exactly parallel to the chord: the curve runs past p1 and comes back.
    assert _bulge([0, 0, 0], [0, 4000, 0], [0, 800, 0], [0, 400, 0]) > TANGENT_EPS_CM


def test_tangents_are_rounded_with_the_points_but_never_translated_with_them():
    """A point is a place and a tangent is a displacement, so only one of the two moves.

    Getting this wrong is invisible in a unit test at the origin and catastrophic on the real
    world: the chain origins are kilometres out, so a translated tangent would be kilometres
    long and the curve between two adjacent control points would leave the map entirely.
    Checked by moving the chain, which is the falsifier the points' own frame test uses.
    """
    bend = [
        [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 300.0, 0.0]],
        [[0.0, 400.0, 0.0], [0.0, 300.0, 0.0], [300.0, 0.0, 0.0]],
        [[400.0, 800.0, 0.0], [300.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
    ]
    belt = ObjectReference("Persistent_Level", "x.Build_ConveyorBeltMk3_C_7")
    info = [belt, belt, [[belt, belt, bend, 0.0, 0.0, 900.0, -1, -1, 0]], [900.0, 9, -1, -1], []]

    here = _belts([([0.0, 0.0, 0.0], Chain(info))], {}, Drops())["segments"]
    there = _belts([([120_000.0, -80_000.0, 500.0], Chain(info))], {}, Drops())["segments"]
    assert len(here) == len(there) == 1
    assert len(here[0]) == 5, "this run bends, so it carries tangents past the actor column"
    assert [[p[0] + 120_000, p[1] - 80_000, p[2] + 500] for p in here[0][2]] == there[0][2]
    assert here[0][4] == there[0][4], "a tangent moved with the chain"
    # And the pairing, span by span: the first takes point 0's LEAVE and point 1's ARRIVE,
    # which are both three quarters of the chord between them and so bend nothing; the second
    # takes point 1's leave and point 2's arrive, which turn the corner and are carried.
    assert here[0][4] == [0, [300, 0, 0, 300, 0, 0]]


def test_a_point_that_will_not_decode_takes_its_own_tangents_with_it():
    """The two lists are indexed against each other, so they must not be able to slip.

    A point appended without its tangents -- or the reverse -- would not raise: it would shift
    every span after the fault by one and bend the route around the wrong control point, which
    looks like a curve and is a different curve. So a malformed point costs the whole triple.
    """
    good = [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 300.0, 0.0]]
    far = [[0.0, 400.0, 0.0], [0.0, 300.0, 0.0], [300.0, 0.0, 0.0]]
    end = [[400.0, 800.0, 0.0], [300.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    broken = [[0.0, 200.0, 0.0], "not a vector", [0.0, 300.0, 0.0]]
    belt = ObjectReference("Persistent_Level", "x.Build_ConveyorBeltMk3_C_7")

    def run(points, drops=None):
        info = [
            belt,
            belt,
            [[belt, belt, points, 0.0, 0.0, 900.0, -1, -1, 0]],
            [900.0, 9, -1, -1],
            [],
        ]
        return _belts(
            [([0.0, 0.0, 0.0], Chain(info))], {}, drops if drops is not None else Drops()
        )["segments"]

    drops = Drops()
    assert run([good, broken, far, end], drops) == run([good, far, end])
    # Identical output, and the difference between the two runs is now stated rather than
    # inferable only by having the other one to compare against.
    assert sum(drops.values()) == 1, "the broken triple, counted"


# ------------------------------------------------------------------------------ storage


def test_storage_is_every_container_and_buffer_and_nothing_else(projection):
    """The class list, held against the census the way the attachments' is.

    Every row's class has to be one the projection also counted as built, and the count has to
    match exactly: a container in ``building_counts`` and missing here is a box the map would
    not draw, and the reverse would be a box that is not there.
    """
    counts = projection["building_counts"]
    rows = projection["storage"]
    assert len(rows) == 151, "the reference world's containers and buffers"
    assert {r["cls"] for r in rows} <= set(STORAGE_CLASSES + FLUID_BUFFER_CLASSES)
    for cls in {r["cls"] for r in rows}:
        assert sum(1 for r in rows if r["cls"] == cls) == counts[cls], cls


def test_a_splitter_is_not_storage_even_though_it_owns_a_storage_inventory(projection):
    """The trap this key's class list exists to avoid, stated as a test.

    Every splitter and merger in the world owns a component literally named
    ``StorageInventory``, holding the one to three items physically inside the junction. A key
    built by matching that component name would report 848 more "containers" than exist, draw
    every one of them a second time over the belt layer that already has them, and count items
    in transit as stock.
    """
    attached = {r["instance"] for r in projection["attachments"]}
    stored = {r["instance"] for r in projection["storage"]}
    assert attached and stored
    assert not attached & stored
    assert len(stored) * 5 < len(attached), "the splitters outnumber the containers five to one"


def test_a_container_row_is_a_placement_and_its_contents(projection):
    """What a container IS, is where it stands and what is in it -- and nothing borrowed.

    Same posture as the attachment row next door: no recipe and no clock, because a container
    runs neither, and a null column claiming otherwise would be an invention.
    """
    solids = [r for r in projection["storage"] if "items" in r]
    assert len(solids) == 146
    for r in solids:
        assert set(r) == {"cls", "instance", "pos", "yaw", "items", "slots"}
        assert len(r["pos"]) == 3
        assert -180.0 <= r["yaw"] <= 180.0
        assert r["slots"] > 0, "a container with no slots at all is not a container"
        for item, amount in r["items"]:
            assert item.startswith("Desc_")
            assert isinstance(amount, (int, float)) and amount > 0
        # Biggest first, so a popup showing the top few shows the few worth showing.
        assert [n for _i, n in r["items"]] == sorted((n for _i, n in r["items"]), reverse=True)
    assert sum(1 for r in solids if r["items"]) == 125, "the ones the player has actually filled"


def test_a_containers_contents_are_its_own_and_they_add_up(projection):
    """The join, checked against a total the projection reached a different way.

    ``inventories["storage"]`` has summed these same stacks since schema 11 -- by bucketing
    component NAMES, with no idea which actor owns which -- so it is an independent count of
    the same items, and a mis-joined or double-counted inventory would not match it.

    **It used to match with a remainder, and the remainder was the bug.** Schema 15 recorded
    it as a finding and tolerated it: the bucket rule matched three substrings where the row
    join uses STORAGE_CLASSES, so the 6 Personal Storage Boxes, the HUB's built-in container
    and the Blueprint Designer's were in one and not the other, and 10,667 units over 31 item
    classes were bucketed as machine buffers -- material ``stock()`` will not spend. Schema 16
    made the bucket the same membership test, and there is nothing left over: the two now
    agree item for item, which is the strongest form this cross-check can take.

    A freight wagon would still be a legitimate remainder in ``bucketed`` -- it is stock and it
    is not a container, so it has no row here -- and no save in the reference directory has
    one. Asserted as an exact match rather than as an inequality because that is what this
    world says; a wagon arriving here should be a failure somebody reads, not a silent pass.
    """
    per_row: dict[str, float] = {}
    for r in projection["storage"]:
        for item, amount in r.get("items", ()):
            per_row[item] = per_row.get(item, 0) + amount
    bucketed = projection["inventories"]["storage"]
    assert per_row and bucketed
    assert per_row == bucketed, "the two counts of the same stacks disagree"
    # And the eight containers the old rule could not see are really in there, so that this
    # is a statement about the fix rather than about two empty sums.
    named = ("StorageContainer", "CentralStorage", "FreightWagon")
    outside: dict[str, float] = {}
    for r in projection["storage"]:
        if any(tag in r["cls"] for tag in named):
            continue
        for item, amount in r.get("items", ()):
            outside[item] = outside.get(item, 0) + amount
    assert len(outside) == 31 and sum(outside.values()) == 10667


def test_a_fluid_buffer_takes_its_fluid_from_the_network_that_claims_it(projection):
    """A buffer stores a level and never names the fluid; the plumbing around it does.

    Exactly the join a pipe's ``fluid`` uses, and for the same reason -- it is the game's own
    ``FGPipeNetwork`` answer rather than an inference from what the buffer is plugged into.
    """
    buffers = [r for r in projection["storage"] if "stored_m3" in r]
    assert len(buffers) == 5
    fluids = {r["fluid"] for r in projection["pipe_networks"]}
    for r in buffers:
        assert set(r) == {"cls", "instance", "pos", "yaw", "fluid", "stored_m3"}
        assert r["fluid"] in fluids, r["fluid"]
        assert "items" not in r and "slots" not in r
        # Cubic metres, and inside the capacity the dump states for the class -- 400 on a
        # Fluid Buffer, 2,400 on an Industrial one. A litres reading would be 1000x over.
        cap = 2400.0 if r["cls"] == "Build_IndustrialTank_C" else 400.0
        assert 0.0 <= r["stored_m3"] <= cap, (r["cls"], r["stored_m3"])
    assert any(r["stored_m3"] > 300 for r in buffers), "one of them is nearly full"


def test_storage_is_ordered_so_two_saves_of_one_world_can_be_diffed(projection):
    """Stable between runs, which is the projection's posture wherever it emits a list."""
    rows = projection["storage"]
    assert [(r["cls"], r["instance"]) for r in rows] == sorted(
        (r["cls"], r["instance"]) for r in rows
    )


def test_a_storage_actor_with_no_inventory_component_is_still_a_container():
    """An empty box is a box. The join is a lookup, and a miss has to mean "nothing in it".

    A container the player has never touched may have no ``StorageInventory`` written at all --
    UE omits a SaveGame property still at its default -- and dropping the row would take the
    box off the map for the crime of being empty.
    """
    rows = _storage(
        [("Build_StorageContainerMk1_C", "x.Build_StorageContainerMk1_C_1", [1, 2, 3], 90.0, None)],
        {},
        [],
    )
    assert rows == [
        {
            "cls": "Build_StorageContainerMk1_C",
            "instance": "x.Build_StorageContainerMk1_C_1",
            "pos": [1, 2, 3],
            "yaw": 90.0,
            "items": [],
            "slots": 0,
        }
    ]


def test_a_buffer_no_network_claims_keeps_its_level_and_loses_its_fluid():
    """Drawn with contents unknown beats not drawn -- the refusal ``_pipes`` already makes."""
    rows = _storage(
        [("Build_PipeStorageTank_C", "x.Build_PipeStorageTank_C_1", [0, 0, 0], 0.0, 12.5)], {}, []
    )
    assert rows[0]["fluid"] is None
    assert rows[0]["stored_m3"] == 12.5
    # And a level that will not read as a number is null rather than zero: an unreadable
    # buffer is not an empty one.
    unreadable = _storage([("Build_IndustrialTank_C", "i", [0, 0, 0], 0.0, "brimming")], {}, [])
    assert unreadable[0]["stored_m3"] is None
