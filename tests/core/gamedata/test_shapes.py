"""Building outlines: the clearance offset, the per-box fallback and the shape table."""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.gamedata.footprint import clearance_rings, extract_footprint
from satisfactory_mcp.core.gamedata.model import GameData
from satisfactory_mcp.core.gamedata.shapes import ShapeRow, building_shape, parse_shape_table
from satisfactory_mcp.core.jsontypes import JsonObject


def _box(
    lo: tuple[float, float, float], hi: tuple[float, float, float], **extra: object
) -> JsonObject:
    keys = ("X", "Y", "Z")
    entry: JsonObject = {
        "ClearanceBox": {
            "Min": dict(zip(keys, lo, strict=True)),
            "Max": dict(zip(keys, hi, strict=True)),
        }
    }
    entry.update(extra)  # type: ignore[arg-type]
    return entry


#: A box sitting off-centre: 4 x 2 m, its middle 3 m forward and 1 m to the right.
OFFSET_BOX = [_box((100, 0, 0), (500, 200, 300))]


def test_the_footprint_keeps_where_its_box_sits():
    fp = extract_footprint(OFFSET_BOX)
    assert fp is not None
    assert (fp.width_m, fp.depth_m, fp.height_m) == (4.0, 2.0, 3.0)
    assert (fp.offset_x_m, fp.offset_y_m) == (3.0, 1.0)


def test_a_centred_box_has_no_offset():
    fp = extract_footprint([_box((-400, -500, 0), (400, 500, 700))])
    assert fp is not None and (fp.offset_x_m, fp.offset_y_m) == (0.0, 0.0)


def test_each_box_is_drawn_on_its_own_at_its_own_place():
    rings = clearance_rings(OFFSET_BOX + [_box((-100, -100, 0), (0, 0, 100))])
    assert sorted(sorted(ring) for ring in rings) == [
        [(-1.0, -1.0), (-1.0, 0.0), (0.0, -1.0), (0.0, 0.0)],
        [(1.0, 0.0), (1.0, 2.0), (5.0, 0.0), (5.0, 2.0)],
    ]


def test_a_box_turned_about_z_is_drawn_turned():
    # A quarter turn about Z: (x, y) -> (-y, x).
    turn = {"Rotation": {"X": 0.0, "Y": 0.0, "Z": 0.7071067811865476, "W": 0.7071067811865476}}
    ring = clearance_rings([_box((0, 0, 0), (400, 100, 100), RelativeTransform=turn)])[0]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    assert (min(xs), max(xs)) == pytest.approx((-1.0, 0.0))
    assert (min(ys), max(ys)) == pytest.approx((0.0, 4.0))


def test_snapping_approach_boxes_and_soft_boxes_beside_hard_ones_are_not_drawn():
    rings = clearance_rings(
        [
            _box((0, 0, 0), (100, 100, 100)),
            _box((0, 0, 0), (900, 900, 100), Type="CT_Soft"),
            _box((0, 0, 0), (900, 900, 100), ExcludeForSnapping="True"),
        ]
    )
    assert len(rings) == 1


def test_a_torn_table_row_costs_itself_only():
    table = parse_shape_table(
        {
            "classes": {
                "Build_Good_C": {
                    "rings": [[[0, 0], [1, 0], [1, 1]], [[0, 0], [1]]],
                    "ports": [
                        {"name": "Input0", "kind": "belt", "flow": "in", "x_m": 0, "y_m": -3,
                         "z_m": 1, "yaw": -90},
                        {"name": "Odd", "kind": "rail", "flow": "in", "x_m": 0, "y_m": 0,
                         "z_m": 0, "yaw": 0},
                    ],
                },
                "Build_Torn_C": "not a row",
            }
        }
    )  # fmt: skip
    assert list(table) == ["Build_Good_C"]
    assert table["Build_Good_C"]["rings"] == [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]]
    assert [p["name"] for p in table["Build_Good_C"]["ports"]] == ["Input0"]


def test_bands_come_lowest_first_and_a_torn_band_is_dropped():
    ring = [[0, 0], [1, 0], [1, 1]]
    table = parse_shape_table(
        {
            "classes": {
                "Build_Tall_C": {
                    "rings": [ring],
                    "bands": [
                        {"tier": 2, "z_m": 9.5, "rings": [ring]},
                        {"tier": 1, "z_m": 2, "rings": [ring]},
                        {"tier": 1, "z_m": "high", "rings": [ring]},
                        {"tier": 2, "z_m": 4, "rings": [[[0, 0]]]},
                        {"z_m": 3, "rings": [ring]},
                    ],
                    "ports": [],
                },
                "Build_Flat_C": {"rings": [ring], "ports": []},
            }
        }
    )
    bands = table["Build_Tall_C"]["bands"]
    assert [(band["tier"], band["z_m"]) for band in bands] == [(1, 2.0), (2, 9.5)]
    assert table["Build_Flat_C"]["bands"] == []


def test_a_class_with_no_mesh_outline_falls_back_to_its_clearance_boxes(game: GameData):
    cls = "Build_ConstructorMk1_C"
    mesh: dict[str, ShapeRow] = {
        cls: {"rings": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]], "bands": [], "ports": []}
    }
    assert building_shape(game, mesh, cls)["source"] == "mesh"  # type: ignore[index]
    fallback = building_shape(game, {}, cls)
    assert fallback is not None and fallback["source"] == "clearance" and fallback["rings"]
    assert building_shape(game, {}, "Build_NoSuchThing_C") is None
