"""The camera model calibrate measures a game albedo through: tonemap, sky, light, discount.

docs/map/calibration.md section 31. No install: the pinned values are the prototype's, which
the port reproduces to the last printed digit.
"""

from __future__ import annotations

import numpy as np
import pytest

from mapgen.palette.painted.derive import atmosphere, camera, tonemap
from mapgen.palette.painted.derive.camera import Light

#: Build 502094's noon light, as the paint store keeps it.
NOON = Light(
    "global",
    (1.0, 0.9283739469394504, 0.7135382425944763),
    3.140000104904175,
    59.4914056493052,
    (1.1266670227050781, 1.1266670227050781, 1.2999999523162842),
)
#: The exposure the build's lit bake gives.
E = 2.8067


def test_mid_grey_comes_out_of_the_film_curve_as_mid_grey():
    out = tonemap.tonemap(np.full(3, 0.18))
    np.testing.assert_allclose(out, 0.18, atol=1e-4)


def test_a_neutral_stays_neutral_and_a_ramp_stays_in_order():
    grey = tonemap.tonemap(np.full(3, 0.5))
    assert float(grey.max() - grey.min()) < 2e-4
    ramp = tonemap.tonemap(np.linspace(0.0, 4.0, 50)[:, None] * np.ones(3))
    assert np.all(np.diff(ramp, axis=0) > 0)
    assert float(ramp.max()) < 1.0


def test_the_default_sky_is_pinned():
    np.testing.assert_allclose(
        atmosphere.sun_transmittance(59.5),
        [0.931192810375, 0.848208311258, 0.730035441295],
        rtol=1e-9,
    )
    np.testing.assert_allclose(
        atmosphere.sky_irradiance(59.5), [0.024073644309, 0.044318029284, 0.081888076753], rtol=1e-9
    )
    np.testing.assert_allclose(
        camera.illuminant(NOON), [0.828969253372, 0.724411101691, 0.524469038137], rtol=1e-9
    )


def test_unit_gains_grade_nothing():
    colours = np.array([[0.3, 0.2, 0.1], [0.05, 0.4, 0.2], [0.9, 0.9, 0.95]])
    graded = tonemap.tonemap(colours, ((1.0, 1.0, 1.0),) * 3)
    np.testing.assert_array_equal(graded, tonemap.tonemap(colours))


@pytest.mark.parametrize(
    ("albedo", "golden"),
    [
        ((0.519, 0.332, 0.225), "#d7c2a3"),  # Sand, the bake's median
        ((0.099, 0.139, 0.060), "#7f8d4e"),  # Grass
        ((0.118, 0.101, 0.080), "#897758"),  # the cliff textures x the cliff Color Tint
    ],
)
def test_an_albedo_measures_its_golden_display_colour(albedo, golden):
    assert camera.hex_of_lab(camera.measured_lab(np.array(albedo), NOON, E)) == golden


def test_the_discount_caps_lightness_and_shrinks_chroma():
    lab = camera.discount(np.array([0.95, 0.1, -0.1]))
    np.testing.assert_allclose(lab, [0.86, 0.09, -0.09])


def test_exposure_takes_the_band_mean_to_mid_grey_with_the_bias():
    bake = np.full((1000, 3), 0.2, np.float32)
    e, band = camera.exposure(bake, NOON, 1.5, (75.0, 95.0))
    assert band == pytest.approx(float(0.2 * camera.illuminant(NOON) @ camera.REC709), rel=1e-6)
    assert e == pytest.approx(0.18 * 2**1.5 / band)


def test_delta_e_is_the_oklab_distance_times_100():
    a, b = camera.lab_of_hex("#d5cbb6"), camera.lab_of_hex("#d5cbb6")
    assert camera.delta_e(a, b) == 0.0
    assert camera.delta_e(camera.lab_of_hex("#000000"), camera.lab_of_hex("#ffffff")) == (
        pytest.approx(100.0, abs=0.01)
    )
