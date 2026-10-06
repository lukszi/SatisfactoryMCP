"""Schema 15's spline curvature: one curve entry per span, and a bulge that never understates."""

from __future__ import annotations

import math

from pioneersav import ObjectReference
from satisfactory_mcp.core.saveio.extract import (
    TANGENT_EPS_CM,
    Drops,
    _belts,
    _bulge,
)
from tests.support.saves import Chain


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
