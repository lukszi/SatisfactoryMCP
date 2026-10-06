"""Centroid and spread — one implementation, previously four.

`graph/identity.py` and `graph/query.py` each carried the same four lines; `diff` had a
named `_centroid`; `app`, `select` and `trunks` inlined theirs again. `geo.Cluster` had
had both all along, just shaped for node dicts rather than for (x, y) tuples, which is
how the copies got started.

The two behaviours worth pinning are the ones the copies disagreed about: what an empty
set answers, and whether spread double-counts.
"""

from __future__ import annotations

import math

import pytest

from satisfactory_mcp.domain.spatial import geo
from tests.support.paths import REPO_ROOT

# ------------------------------------------------------------- centroid


def test_the_mean_is_the_mean():
    assert geo.centroid([(0.0, 0.0), (10.0, 20.0)]) == (5.0, 10.0)
    assert geo.centroid([(3.0, 4.0)]) == (3.0, 4.0)


def test_an_empty_set_is_None_and_not_the_origin():
    """(0, 0) is a real and important place here -- the world centre every compass
    direction is measured from -- so answering it for "no points" would be a plausible
    wrong location rather than an obvious one."""
    assert geo.centroid([]) is None
    assert geo.centroid(()) is None


def test_units_pass_through_untouched():
    """Centroid does no cm/m conversion. Callers hold centimetres, and a helper that
    silently converted would be the 100x bug this project already documents."""
    assert geo.centroid([(100_000.0, -200_000.0)]) == (100_000.0, -200_000.0)


# ------------------------------------------------------------- extent


def test_the_bounding_box_is_the_extent_and_not_the_first_and_last_point():
    """Order must not matter, and both axes are independent -- a box read off the first
    and last point of a list is the classic wrong version of this."""
    points = [(10.0, -5.0), (-3.0, 20.0), (7.0, 2.0)]
    assert geo.bbox(points) == (-3.0, -5.0, 10.0, 20.0)
    assert geo.bbox(list(reversed(points))) == (-3.0, -5.0, 10.0, 20.0)


def test_one_point_gives_a_degenerate_box_and_no_points_give_None():
    """A one-machine factory really does have zero extent; the caller pads it. Empty is
    None for centroid's reason -- a zero box at the origin would frame the world centre
    rather than say there was nothing to frame."""
    assert geo.bbox([(4.0, 9.0)]) == (4.0, 9.0, 4.0, 9.0)
    assert geo.bbox([]) is None


# ------------------------------------------------------------- spread


def test_diameter_is_the_largest_pairwise_distance_in_metres():
    # 300 cm and 400 cm legs -> 500 cm -> 5 m.
    assert geo.diameter_m([(0.0, 0.0), (300.0, 400.0)]) == pytest.approx(5.0)


def test_fewer_than_two_points_have_no_spread():
    assert geo.diameter_m([]) == 0.0
    assert geo.diameter_m([(5.0, 5.0)]) == 0.0


def test_the_half_matrix_gives_the_same_answer_as_the_full_one():
    """The inline copies iterated `for a in points for b in points`, computing every pair
    twice plus the zero diagonal. Same answer, double the work -- at 563 machines, 317k
    distance calls instead of 158k. This pins that the cheaper form did not change any
    number while removing the duplication."""
    points = [(i * 137.0 % 9000, i * 911.0 % 7000) for i in range(60)]
    naive = max((math.dist(a, b) for a in points for b in points), default=0.0) / 100.0
    assert geo.diameter_m(points) == pytest.approx(naive)


def test_a_cluster_reuses_the_same_function(game):
    """geo.Cluster had this logic first; it now delegates rather than keeping a fifth
    copy shaped for node dicts."""
    members = [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 300.0, "y": 400.0, "z": 0.0},
    ]
    assert geo.Cluster(members).diameter_m == pytest.approx(
        geo.diameter_m([(0.0, 0.0), (300.0, 400.0)])
    )


# ------------------------------------------------------------- the callers


def test_a_factory_view_still_reports_a_centroid_and_spread(game, state):
    """The two graph modules were the biggest copies. Their output must be unchanged."""
    from satisfactory_mcp.domain.factories.query import build_view

    machines = sorted(state.graph.machines())[:40]
    if len(machines) < 2:
        pytest.skip("not enough machines in this projection")
    view = build_view("probe", machines, state.graph, game, state.projection)
    assert view.centroid != (0.0, 0.0)
    assert view.spread_m > 0
    # And the spread is a real diameter over those machines, not a stale field.
    assert view.spread_m == pytest.approx(
        geo.diameter_m(
            [
                (r["pos"][0], r["pos"][1])
                for r in state._all_records()
                if r.get("pos") and r["instance"].rsplit(".", 1)[-1] in set(machines)
            ]
        )
    )


# ------------------------------------------------------------- the conversion


def test_three_dimensional_distance_is_a_separate_function():
    """Not a flag on distance_m, because the choice is a modelling decision. distance_m
    drops Z on purpose -- a 40 m climb is noise against a 400 m walk when the question is
    "is this near that" -- while a pipe RUN has to include the vertical leg, which is real
    pipe. Passing 3-tuples to math.dist and letting it silently do 3D put that distinction
    in the shape of a tuple rather than the name of the function."""
    flat = geo.distance_m((0.0, 0.0), (300.0, 400.0))
    tall = geo.distance_3d_m((0.0, 0.0, 0.0), (300.0, 400.0, 1200.0))
    assert flat == pytest.approx(5.0)
    assert tall == pytest.approx(13.0)
    # distance_m must ignore a Z it is not given, not crash on a 2-tuple.
    assert geo.distance_m((0.0, 0.0), (0.0, 0.0)) == 0.0


def test_only_the_centimetre_module_still_calls_math_dist_directly():
    """graph/structure.py works in cm throughout against cm thresholds and reports nothing
    in metres, so there is no conversion there to get wrong. Everywhere else routes through
    geo, and this pins that a new hand-typed `/ 100.0` does not creep back in."""

    root = REPO_ROOT / "src" / "satisfactory_mcp"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name in ("geo.py", "structure.py"):
            continue
        if "math.dist" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == [], offenders
