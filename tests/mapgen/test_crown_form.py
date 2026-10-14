"""The crowns' form in the sun (``palette/painted/crown_form.py``): their stamped tops' slope,
raised by the style's relief and lit against flat ground, a factor on the crowns' sun term.

docs/map/light-and-crowns.md section 36, "Crown form". Synthetic fixtures: no install.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen import jit
from mapgen.lighting.sun import DEFAULT_SUN, sun_vector
from mapgen.palette.painted.crown_form import FORM_M, crown_form, form_reach
from mapgen.palette.painted.trees import crown_layer, crown_shade
from mapgen.palette.styles import PAINTED_PALETTE

pytestmark = pytest.mark.filterwarnings("ignore:CUDA path could not be detected:UserWarning")

SP = 0.229
N = 121


def _dome(height_cm: float = 600.0, radius_px: float = 50.0) -> np.ndarray:
    """A round crown whose top falls ``height_cm`` from its middle to its rim; NaN outside."""
    y, x = np.mgrid[0:N, 0:N] - N // 2
    r2 = (x * x + y * y) / radius_px**2
    return np.where(r2 <= 1.0, 2000.0 + height_cm * (1.0 - r2), np.nan).astype(np.float32)


def _toward_sun(steps: int) -> tuple[int, int]:
    """The dome's pixel ``steps`` from its middle toward the default sun: ``(row, col)``."""
    east, south, _up = sun_vector(*DEFAULT_SUN)
    norm = np.hypot(east, south)
    return N // 2 + round(steps * south / norm), N // 2 + round(steps * east / norm)


def test_flat_tops_no_tops_and_no_relief_leave_the_sun_term_as_it_is():
    flat = np.full((N, N), 1500.0, np.float32)
    assert (crown_form(flat, SP, 1.5, DEFAULT_SUN, gpu=False) == 1.0).all()
    empty = np.full((N, N), np.nan, np.float32)
    assert (crown_form(empty, SP, 1.5, DEFAULT_SUN, gpu=False) == 1.0).all()
    assert (crown_form(_dome(), SP, 0.0, DEFAULT_SUN, gpu=False) == 1.0).all()


def test_a_crown_is_lit_on_its_sunward_side_and_shaded_on_the_far_one():
    form = crown_form(_dome(), SP, 1.5, DEFAULT_SUN, gpu=False)
    near, far = _toward_sun(35), _toward_sun(-35)
    assert form[near] > 1.0 > form[far]
    assert form[N // 2, N // 2] == pytest.approx(1.0, abs=1e-6), "the top is flat"
    assert np.isfinite(form).all() and form.min() >= 0.0


def test_the_relief_scales_the_form():
    weak = crown_form(_dome(), SP, 0.5, DEFAULT_SUN, gpu=False)
    strong = crown_form(_dome(), SP, 1.5, DEFAULT_SUN, gpu=False)
    far = _toward_sun(-35)
    assert strong[far] < weak[far] < 1.0


def test_the_ground_beside_a_crown_draws_no_rim():
    """Only crown tops are averaged: a flat crown over bare ground stays flat to its edge."""
    top = np.full((N, N), np.nan, np.float32)
    top[30:90, 30:90] = 1800.0
    form = crown_form(top, SP, 1.5, DEFAULT_SUN, gpu=False)
    assert (form == 1.0).all()


def test_its_reach_is_a_box_and_an_offset_of_form_m():
    r = round(FORM_M / SP)
    assert form_reach(SP) == 2 * r
    assert form_reach(10.0) == 1, "under a pixel, the slope between neighbours"


def test_the_form_multiplies_the_clamped_sun_term():
    style = PAINTED_PALETTE["crowns"]
    low, _high = style["shade_clamp"]
    shape = (1, 3)
    crowns = {
        "ndl": np.full(shape, 0.01, np.float32),
        "form": np.array([[0.5, 1.0, 1.1]], np.float32),
    }
    scene = {"ndl_flat": np.float32(0.7)}
    shade = crown_shade(crowns, scene, style)  # type: ignore[arg-type]
    np.testing.assert_allclose(shade, [[0.5 * low, low, 1.1 * low]], rtol=1e-6)
    del crowns["form"]
    np.testing.assert_allclose(crown_shade(crowns, scene, style), [[low] * 3], rtol=1e-6)  # type: ignore[arg-type]


def test_the_style_raises_its_crowns_form():
    assert PAINTED_PALETTE["crowns"]["relief"] == 1.5
    assert PAINTED_PALETTE["crowns"]["form_gain"] == 1.2


def test_the_form_gain_lights_only_crowns_with_a_form():
    """The gain returns the form's mean darkening; a flat form draws the crown brighter by
    exactly the gain, and a crown with no form keeps its light."""
    shape = (1, 2)
    flat = np.float32(0.7)
    crowns = {
        "cover": np.ones(shape, np.float32),
        "rgb": np.full((*shape, 3), 0.1, np.float32),
        "top_cm": np.full(shape, 500.0, np.float32),
        "ndl": np.full(shape, flat, np.float32),
        "form": np.ones(shape, np.float32),
    }
    scene = {
        "z_m": np.zeros(shape, np.float32),
        "water": {"cover": np.zeros(shape, np.float32), "depth_m": np.zeros(shape, np.float32)},
        "ndl_flat": flat,
    }
    formed = crown_layer(crowns, scene, PAINTED_PALETTE, 0.4, 1.0)["colour"]  # type: ignore[arg-type]
    del crowns["form"]
    plain = crown_layer(crowns, scene, PAINTED_PALETTE, 0.4, 1.0)["colour"]  # type: ignore[arg-type]
    np.testing.assert_allclose(formed, plain * PAINTED_PALETTE["crowns"]["form_gain"], rtol=1e-6)


@pytest.mark.parametrize("spacing", [0.229, 0.92, 3.66])
def test_the_gpu_twin_is_the_reference_bit_for_bit(spacing):
    problem = jit.gpu_problem()
    if problem is not None:
        pytest.skip(problem)
    rng = np.random.default_rng(3)
    top = (1500.0 + 900.0 * rng.random((200, 260))).astype(np.float32)
    top[rng.random(top.shape) < 0.3] = np.nan
    top[:30] = np.nan
    top[90:100, 100:160] += 2500.0
    on_gpu = crown_form(top, spacing, 1.5, DEFAULT_SUN, gpu=True)
    reference = crown_form(top, spacing, 1.5, DEFAULT_SUN, gpu=False)
    assert on_gpu.dtype == reference.dtype and on_gpu.tobytes() == reference.tobytes()
