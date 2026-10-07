"""Steps counted through a mask (palette.water.geodesic), as the open sea and the mouths count."""

from __future__ import annotations

import numpy as np

from mapgen.palette.water.geodesic import geodesic_steps


def _centre(size: int = 9) -> tuple[np.ndarray, np.ndarray]:
    seed = np.zeros((size, size), bool)
    seed[size // 2, size // 2] = True
    return seed, ~seed


def test_four_neighbour_steps_are_the_city_block_distance():
    seed, inside = _centre()
    steps = geodesic_steps(seed, inside, 20)
    rows, cols = np.indices(seed.shape)
    city = np.abs(rows - 4) + np.abs(cols - 4)
    assert (steps[inside] == city[inside]).all()


def test_the_octagon_takes_a_diagonal_on_every_odd_step():
    seed, inside = _centre()
    steps = geodesic_steps(seed, inside, 20, octagon=True)
    assert steps[5, 5] == 1 and steps[6, 4] == 2 and steps[6, 6] == 3
    assert steps[4, 7] == 3 and steps[8, 8] == 5 and steps[8, 6] == 4


def test_steps_go_round_what_is_not_inside_and_stop_at_the_limit():
    seed = np.zeros((5, 7), bool)
    seed[0, 0] = True
    inside = ~seed
    inside[0:4, 3] = False
    steps = geodesic_steps(seed, inside, 9)
    assert steps[0, 2] == 2 and steps[0, 4] == 10
    assert steps[4, 3] == 7 and steps[3, 4] == 9
    assert steps[seed] == 10 and (steps[~inside & ~seed] == 10).all()
