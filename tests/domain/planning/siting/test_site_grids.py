"""Foundation grids for the pad's edge snap: lattices read from the tiles, never assumed."""

from __future__ import annotations

import math

from satisfactory_mcp.domain.planning.siting.grids import foundation_grids

FOUNDATION = "Build_Foundation_8x1_01_C"


def _projection(rows: list[list[float]], classes: list[str] | None = None) -> dict:
    return {"structures": {"classes": classes or [FOUNDATION], "instances": rows}}


def _turned(x0: float, y0: float, yaw: float, n: int) -> list[list[float]]:
    """``n`` × ``n`` tiles in cm from a centre at (x0, y0) m, laid along ``yaw``."""
    a = math.radians(yaw)
    out = []
    for i in range(n):
        for j in range(n):
            u, v = i * 8.0, j * 8.0
            x = x0 + u * math.cos(a) - v * math.sin(a)
            y = y0 + u * math.sin(a) + v * math.cos(a)
            out.append([0, x * 100, y * 100, 0, yaw])
    return out


def test_a_patch_off_the_world_phase_is_its_own_grid():
    grids = foundation_grids(_projection(_turned(3.0, -5.5, 0.0, 3)))
    assert len(grids) == 1
    grid = grids[0]
    assert grid["tiles"] == 9 and grid["yaw_deg"] == 0.0
    assert grid["anchor_m"] == [3.0, -5.5]
    assert grid["bbox_m"][0] < 3.0 - 4 and grid["bbox_m"][2] > 3.0 + 16 + 4


def test_a_turned_patch_keeps_its_turn_modulo_a_quarter():
    grids = foundation_grids(_projection(_turned(100.0, 200.0, 120.0, 2)))
    assert len(grids) == 1
    assert math.isclose(grids[0]["yaw_deg"], 30.0) and grids[0]["tiles"] == 4


def test_two_patches_apart_or_out_of_phase_are_two_grids_largest_first():
    near = _turned(0.0, 0.0, 0.0, 3)
    shifted = _turned(0.0 + 4.0, 40.0, 0.0, 2)  # half a tile out of phase
    far = _turned(400.0, 0.0, 0.0, 1)
    grids = foundation_grids(_projection(near + shifted + far))
    assert [g["tiles"] for g in grids] == [9, 4, 1]


def test_stacked_storeys_share_cells_and_passthroughs_and_unread_yaws_are_skipped():
    ground = _turned(0.0, 0.0, 0.0, 2)
    upper = [[0, r[1], r[2], 400, 0] for r in ground]
    hole = [[1, 1600, 0, 0, 0]]
    unread = [[0, 5000, 5000, 0, None]]
    classes = [FOUNDATION, "Build_FoundationPassthrough_Lift_C"]
    grids = foundation_grids(_projection(ground + upper + hole + unread, classes))
    assert [g["tiles"] for g in grids] == [4]


def test_no_structures_means_no_grids():
    assert foundation_grids({"machines": []}) == []
