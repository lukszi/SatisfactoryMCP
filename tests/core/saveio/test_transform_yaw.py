"""Schema 12's placement yaw, checked against the direction the world is built in.

A yaw with the wrong sign or off the wrong axis is still a plausible float on every
record, so each test measures it against other numbers in the same save: the bearing
between neighbouring foundations. Fixture-only; no game install.
"""

from __future__ import annotations

import math
from collections import defaultdict

from satisfactory_mcp.core.saveio.extract.readers import yaw_of
from tests.support.saves import PLACED

#: The 8 m grid every foundation sits on. Two pieces exactly this far apart are neighbours in
#: one row of one platform, which is what makes their bearing a measurement of that platform's
#: rotation rather than of anything else.
TILE_CM = 800.0


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
    them after the fact. Null is the claim ``serial.yaw_deg`` and the map already handle -- drawn
    axis-aligned, labelled "facing: unknown" -- and it is the difference between the two.
    """
    assert yaw_of(None) is None
    assert yaw_of(()) is None
    assert yaw_of((0.0, 0.0, 1.0)) is None
    assert yaw_of(("x", "y", "z", "w")) is None
    # Still a float where there is one, and 0.0 keeps meaning axis-aligned.
    assert yaw_of((0.0, 0.0, 0.0, 1.0)) == 0.0
