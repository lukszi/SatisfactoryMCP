"""The shared decoders for the projection's five interned tables.

``core/saveio/rows.py`` exists because ten call sites were each decoding these rows by hand,
with guards copied from one another and already drifting apart. Two things therefore have
to be tested that neither the endpoints' tests nor the domain's cover:

* **the guard itself**, against rows the writer never emits -- torn, truncated, overlong,
  numerically nonsense -- because "a malformed row costs that row and nothing else" is a
  promise every one of those ten call sites makes in prose and none of them can now keep
  on its own;
* **the drift tripwire**, which is the one below that is not about correctness at all. A
  schema-18 column added to any of these five tables would be silently ignored by every
  iterator here, and the projection would go on decoding, so nothing would fail. Holding
  ``*_ROW_WIDTH`` against the widest row the committed projection actually contains turns
  that silence into a failing test in the module that would have to be changed.

No game install and no save: the synthetic projections are written out here and the
reference one is the committed fixture.
"""

from __future__ import annotations

import json

from satisfactory_mcp.core.saveio import rows
from tests.support.paths import FIXTURES

FIXTURE = FIXTURES / "save_projection.json"


def _structures(instances, classes=("Build_Foundation_8x1_01_C", "Build_Wall_8x4_01_C")):
    return {"structures": {"classes": list(classes), "instances": instances}}


def _belts(segments, classes=("Build_ConveyorBeltMk3_C",)):
    return {"belts": {"classes": list(classes), "segments": segments}}


def _pipes(segments, classes=("Build_Pipeline_C",)):
    return {"pipes": {"classes": list(classes), "segments": segments}}


def _power(poles=(), wires=(), classes=("Build_PowerPoleMk1_C", "Build_PowerTowerPlatform_C")):
    return {
        "power": {
            "poles": {"classes": list(classes), "instances": list(poles)},
            "wires": list(wires),
        }
    }


# ------------------------------------------------------------------- structures


def test_a_structure_row_decodes_to_raw_centimetres_and_its_own_class():
    """No rounding, no metres, no naming: the row's numbers and the table's own class."""
    (piece,) = rows.iter_structures(_structures([[1, 1234.5, -6789.5, 42.25, -20.0]]))
    assert piece == rows.Structure(
        class_index=1,
        cls="Build_Wall_8x4_01_C",
        x=1234.5,
        y=-6789.5,
        z=42.25,
        yaw=-20.0,
    )


def test_a_four_column_structure_row_is_a_placement_with_no_facing():
    """Schema 11's shape. The yaw column arrived in 12 and the row is additive."""
    (piece,) = rows.iter_structures(_structures([[0, 10, 20, 30]]))
    assert (piece.x, piece.y, piece.z) == (10.0, 20.0, 30.0)
    assert piece.yaw is None


def test_an_unreadable_rotation_and_an_absent_one_arrive_the_same_way():
    """Schema 16 writes ``null`` where the quaternion would not read.

    Not because the two are the same claim -- the extractor is emphatic that they are not --
    but because no reader of these rows has ever distinguished them: both mean "draw it
    axis-aligned and do not publish a bearing", which is what ``/api/structures`` sends.
    """
    both = list(rows.iter_structures(_structures([[0, 1, 2, 3, None], [0, 1, 2, 3]])))
    assert [p.yaw for p in both] == [None, None]


def test_a_structure_row_the_class_list_cannot_answer_is_still_a_place():
    """An index past the end is an unknown class at a known position, not a missing piece."""
    (piece,) = rows.iter_structures(_structures([[9, 1, 2, 3, 0.0]]))
    assert piece.class_index == 9
    assert piece.cls is None


def test_a_torn_structure_row_costs_that_row_and_nothing_after_it():
    good = [0, 100, 200, 300, 90.0]
    projection = _structures(
        [
            "not a row",
            None,
            [],
            [0, 1, 2],  # three columns: no Z, so no place
            [0, "x", 2, 3],  # a coordinate that is not a number
            ["nope", 1, 2, 3],  # a class index that is not a number
            [0, 1, 2, [3]],  # a coordinate that is a list
            good,
        ]
    )
    decoded = list(rows.iter_structures(projection))
    assert len(decoded) == 1
    assert (decoded[0].x, decoded[0].y, decoded[0].z, decoded[0].yaw) == (100.0, 200.0, 300.0, 90.0)


def test_an_unreadable_yaw_costs_the_facing_and_not_the_piece():
    """The trailing column is the one a reader is allowed to shrug at."""
    (piece,) = rows.iter_structures(_structures([[0, 1, 2, 3, "sideways"]]))
    assert (piece.x, piece.y, piece.z) == (1.0, 2.0, 3.0)
    assert piece.yaw is None


def test_a_projection_with_no_structures_at_all_yields_nothing():
    for projection in ({}, {"structures": None}, {"structures": []}, {"structures": {}}):
        assert list(rows.iter_structures(projection)) == []


# ------------------------------------------------------------------------ belts


def test_a_belt_row_decodes_its_chain_its_class_its_points_its_actor_and_its_curve():
    span = [7, 8, 9, 1, 2, 3]
    (seg,) = rows.iter_belt_segments(_belts([[4, 0, [[1, 2, 3], [4, 5, 6]], 11, [span]]]))
    assert seg.position == 0
    assert seg.chain == 4
    assert seg.class_index == 0
    assert seg.cls == "Build_ConveyorBeltMk3_C"
    assert seg.points == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert seg.actor_index == 11
    # Decoded to numbers, still centimetres: each reader converts to metres itself.
    assert seg.spans == [(7.0, 8.0, 9.0, 1.0, 2.0, 3.0)]


def test_a_flat_or_torn_span_reads_as_straight_and_keeps_its_place():
    route = [[0, 0, 0], [1, 1, 1], [2, 2, 2], [3, 3, 3]]
    (seg,) = rows.iter_belt_segments(_belts([[0, 0, route, 1, [0, [1, 2], [1, 2, 3, 4, 5, 6]]]]))
    assert seg.spans == [None, None, (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)]


def test_a_four_column_belt_row_is_a_straight_run_and_not_an_old_projection():
    """2,119 of the reference world's 3,085 pieces have no bend and so no curve column."""
    (seg,) = rows.iter_belt_segments(_belts([[0, 0, [[0, 0, 0], [800, 0, 0]], 4]]))
    assert seg.actor_index == 4
    assert seg.spans is None


def test_a_belt_actor_column_that_is_not_an_index_reads_as_no_join():
    """``-1`` for every way of not naming an actor, on ``iter_pipe_segments``' terms.

    The two tables carry the same column and must not disagree about what a torn one means:
    a belt whose actor index is a float or a string is a belt that cannot be named, not a
    belt that is not there.
    """
    projection = _belts(
        [
            [0, 0, [[0, 0, 0], [1, 1, 1]], -1],
            [0, 0, [[0, 0, 0], [1, 1, 1]], "not an index"],
            [0, 0, [[0, 0, 0], [1, 1, 1]], 3.0],
            [0, 0, [[0, 0, 0], [1, 1, 1]], None],
            [0, 0, [[0, 0, 0], [1, 1, 1]]],  # a projection older than schema 20
        ]
    )
    assert [seg.actor_index for seg in rows.iter_belt_segments(projection)] == [-1] * 5


def test_a_torn_belt_row_costs_that_row_and_leaves_the_ordinals_of_the_rest():
    projection = _belts(
        [
            "not a segment",
            [0, 0],  # two columns: no geometry
            [0, 0, []],  # a route with no points is not a piece
            [0, 0, [[1, 2]]],  # a point with no Z
            ["x", 0, [[1, 2, 3]]],  # a chain index that is not a number
            [1, 0, [[1, 2, 3], [4, 5, 6]]],
        ]
    )
    decoded = list(rows.iter_belt_segments(projection))
    assert [seg.position for seg in decoded] == [5], "the ordinal is the row's place in the table"
    assert decoded[0].chain == 1


def test_one_unreadable_point_costs_that_point_and_not_the_belt():
    """The same trade all four call sites were already making, in one place now."""
    (seg,) = rows.iter_belt_segments(
        _belts([[0, 0, [[1, 2, 3], "not a point", [4, 5], [7, 8, 9]]]])
    )
    assert seg.points == [[1.0, 2.0, 3.0], [7.0, 8.0, 9.0]]


def test_the_belt_segment_count_is_rows_in_not_rows_decoded():
    projection = _belts(["not a segment", [0, 0, [[1, 2, 3]]]])
    assert rows.belt_segment_count(projection) == 2
    assert len(list(rows.iter_belt_segments(projection))) == 1


# ------------------------------------------------------------------------ pipes


def test_a_pipe_row_decodes_its_network_its_class_its_points_its_actor_and_its_curve():
    span = [7, 8, 9, 1, 2, 3]
    (seg,) = rows.iter_pipe_segments(_pipes([[2, 0, [[1, 2, 3], [4, 5, 6]], 11, [span]]]))
    assert seg.position == 0
    assert seg.network_index == 2
    assert seg.class_index == 0
    assert seg.cls == "Build_Pipeline_C"
    assert seg.points == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
    assert seg.actor_index == 11
    assert seg.spans == [(7.0, 8.0, 9.0, 1.0, 2.0, 3.0)]


def test_a_three_column_pipe_row_is_a_schema_13_projection_and_joins_nothing():
    """The actor column arrived in 14, and a pipe without one is still drawable."""
    (seg,) = rows.iter_pipe_segments(_pipes([[0, 0, [[0, 0, 0], [400, 0, 0]]]]))
    assert seg.actor_index == -1
    assert seg.spans is None


def test_an_actor_column_that_is_not_an_index_reads_as_no_join():
    """``-1`` for every way of not naming an actor, so the caller has one case to handle.

    An actor index is a position in a list the projection also carries, so a float, a string
    or a negative number here is a torn column rather than a number in the wrong type -- and
    the pipe is still a pipe, which is why this costs the join and not the row.
    """
    projection = _pipes(
        [
            [0, 0, [[0, 0, 0], [1, 1, 1]], -1],
            [0, 0, [[0, 0, 0], [1, 1, 1]], "not an index"],
            [0, 0, [[0, 0, 0], [1, 1, 1]], 3.0],
            [0, 0, [[0, 0, 0], [1, 1, 1]], None],
        ]
    )
    assert [seg.actor_index for seg in rows.iter_pipe_segments(projection)] == [-1, -1, -1, -1]


def test_a_torn_pipe_row_leaves_a_HOLE_in_the_ordinals_rather_than_shifting_them():
    """The property ``/api/pipes`` and ``pipe_flow`` join to each other on.

    Both key a segment by its position in the table, so a row that will not decode has to
    take its own ordinal with it. A decoder that simply skipped would renumber every pipe
    after the fault and hang each one's inferred direction on its neighbour.
    """
    projection = _pipes(
        [
            [0, 0, [[0, 0, 0], [1, 1, 1]], 5],
            "not a segment",
            [0, 0, [[2, 2, 2], [3, 3, 3]], 6],
        ]
    )
    decoded = list(rows.iter_pipe_segments(projection))
    assert [seg.position for seg in decoded] == [0, 2]
    assert rows.pipe_segment_count(projection) == 3


def test_a_projection_with_no_pipes_at_all_yields_nothing():
    for projection in ({}, {"pipes": None}, {"pipes": {}}, {"pipes": {"segments": None}}):
        assert list(rows.iter_pipe_segments(projection)) == []
        assert rows.pipe_segment_count(projection) == 0


# ------------------------------------------------------------------------ power


def test_a_pole_row_decodes_to_raw_centimetres_its_class_and_its_actor():
    (pole,) = rows.iter_power_poles(_power(poles=[[1, 1200.0, -3400.0, 55.0, -20.0, 42]]))
    assert pole == rows.PowerPole(
        class_index=1,
        cls="Build_PowerTowerPlatform_C",
        x=1200.0,
        y=-3400.0,
        z=55.0,
        yaw=-20.0,
        actor_index=42,
    )


def test_a_pole_no_wire_names_still_stands_somewhere():
    """2 of the reference world's 701, both tower platforms nobody strung a line to."""
    (pole,) = rows.iter_power_poles(_power(poles=[[0, 1, 2, 3, 0.0, -1]]))
    assert pole.actor_index == -1
    assert (pole.x, pole.y, pole.z) == (1.0, 2.0, 3.0)


def test_a_torn_pole_row_costs_that_pole_and_nothing_after_it():
    projection = _power(
        poles=[
            "not a row",
            None,
            [0, 1, 2],  # three columns: no Z, so no place
            [0, "x", 2, 3, 0.0, 0],  # a coordinate that is not a number
            ["nope", 1, 2, 3, 0.0, 0],  # a class index that is not a number
            [0, 1, 2, 3, "sideways", 3.5],  # an unreadable yaw and a non-index actor
            [1, 10, 20, 30, 90.0, 7],
        ]
    )
    decoded = list(rows.iter_power_poles(projection))
    assert len(decoded) == 2
    assert (decoded[0].yaw, decoded[0].actor_index) == (None, -1)
    assert (decoded[1].x, decoded[1].yaw, decoded[1].actor_index) == (10.0, 90.0, 7)


def test_a_wire_decodes_to_its_two_ends_and_carries_its_ordinal():
    (wire,) = rows.iter_wires(_power(wires=[[1, 2, 3, 4, 5, 6]]))
    assert wire == rows.Wire(position=0, a=[1.0, 2.0, 3.0], b=[4.0, 5.0, 6.0])


def test_a_wire_with_no_geometry_leaves_a_HOLE_in_the_ordinals():
    """``null`` is what the writer emits for a wire that published no span.

    The ordinal is the row's place in ``power["wires"]``, which is its place in
    ``graph["power"]`` -- so a wire that cannot be drawn must not renumber the ones that can,
    or every drawn span after it would be joined to the wrong pair of actors.
    """
    projection = _power(wires=[[0, 0, 0, 100, 0, 0], None, "not a wire", [1, 2, 3, 4, 5]])
    decoded = list(rows.iter_wires(projection))
    assert [w.position for w in decoded] == [0]
    assert rows.wire_count(projection) == 4


def test_a_projection_with_no_power_at_all_yields_nothing():
    """Every schema before 17, which had no such key, plus every way of it being empty."""
    for projection in (
        {},
        {"power": None},
        {"power": {}},
        {"power": {"poles": None, "wires": None}},
        {"power": {"poles": {"instances": []}, "wires": []}},
    ):
        assert list(rows.iter_power_poles(projection)) == []
        assert list(rows.iter_wires(projection)) == []
        assert rows.wire_count(projection) == 0


def test_the_wires_are_positionally_aligned_with_the_power_edges(projection):
    """The one promise this key is built around, held against a real save.

    ``extract.power.power_network`` writes both lists in a single pass so that ``wires[i]`` is
    the span of ``graph["power"][i]``. Nothing in the row shape enforces that -- a wire row
    carries no actor index of its own, deliberately, because ``graph["power"]`` is the
    connectivity and a second copy could disagree -- so the alignment is a claim, and this is
    where it is checked.
    """
    assert rows.wire_count(projection) == len(projection["graph"]["power"])


# ------------------------------------------------------------------- the tripwire


def _widths(table: dict, key: str) -> set[int]:
    return {len(row) for row in table[key] if row is not None}


def test_the_iterators_read_every_column_the_writer_emits(projection):
    """The drift tripwire, and the only test here that is about a FUTURE change.

    ``extract`` decides these rows' shape and this module decides how much of it is read.
    Nothing connects the two: adding a sixth structure column in schema 18 would leave every
    iterator working, every endpoint answering and every test passing, with the new column
    reaching no reader at all -- which is the failure mode this whole module was written to
    stop happening one call site at a time.

    So the widest row the committed projection contains is held against what the decoder
    says it consumes. A new column fails here first, in the module that has to decide
    whether these ten readers want it.

    Held against the FIXTURE rather than against a constant in ``extract``, because the
    fixture is what the suite tests the server against: a column the extractor emits and the
    fixture has not been regenerated for is a second thing worth failing on.
    """
    checks = (
        ("structures", projection["structures"], "instances", rows.STRUCTURE_ROW_WIDTH),
        ("belts", projection["belts"], "segments", rows.BELT_ROW_WIDTH),
        ("pipes", projection["pipes"], "segments", rows.PIPE_ROW_WIDTH),
        # Two more since schema 17, and the wires are the odd one: ``power["wires"]`` is a
        # bare list rather than a ``{classes, rows}`` table, and its rows may be null -- so
        # the widths are taken off the rows that are there. A fixture of nothing but nulls
        # would trip the "empty" assertion below, which is the right failure: it would mean
        # this save recorded no wire geometry and the tripwire is measuring nothing.
        ("power.poles", projection["power"]["poles"], "instances", rows.POWER_POLE_ROW_WIDTH),
        ("power", projection["power"], "wires", rows.WIRE_ROW_WIDTH),
    )
    for key, table, sub, width in checks:
        widths = _widths(table, sub)
        assert widths, f"{key} is empty in the fixture, so this test proves nothing"
        assert max(widths) == width, (
            f"{key}[{sub!r}] rows are up to {max(widths)} columns wide and "
            f"core.saveio.rows reads {width} -- decide whether the new column has a reader"
        )


def test_the_fixture_carries_both_the_short_and_the_long_form_of_a_route():
    """Otherwise the tripwire above and the len-guards below it are untested by real data.

    A curve column is emitted only where a route bends, so a fixture of nothing but straight
    runs would exercise the four-column path and never the five-column one. The two tables
    take the same pair of widths since schema 20 gave a belt the actor column a pipe had.
    """
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _widths(fixture["belts"], "segments") == {4, 5}
    assert _widths(fixture["pipes"], "segments") == {4, 5}


def test_every_row_of_the_reference_projection_decodes(projection):
    """No row of a real save is torn, so the guards must be costing nothing on real data."""
    assert len(list(rows.iter_structures(projection))) == len(projection["structures"]["instances"])
    assert len(list(rows.iter_belt_segments(projection))) == rows.belt_segment_count(projection)
    assert len(list(rows.iter_pipe_segments(projection))) == rows.pipe_segment_count(projection)
    assert len(list(rows.iter_power_poles(projection))) == len(
        projection["power"]["poles"]["instances"]
    )
    assert len(list(rows.iter_wires(projection))) == rows.wire_count(projection)
