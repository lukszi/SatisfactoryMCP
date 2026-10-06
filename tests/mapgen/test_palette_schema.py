"""The palette files are refused at load unless they have their style's shape (palette.schema)."""

from __future__ import annotations

import copy

import pytest

from mapgen.palette import schema
from mapgen.palette.styles import LAYER_STYLES, load_palette

SHAPES = {
    "terrain": schema.TerrainPalette,
    "satellite": schema.SatellitePalette,
    "painted": schema.PaintedPalette,
    "relief": schema.ReliefPalette,
    "relief-dark": schema.ReliefPalette,
}


def _file(layer: str) -> dict:
    palette, _digest = load_palette(LAYER_STYLES[layer])
    return copy.deepcopy(palette)


def _refusal(layer: str, palette: dict) -> str:
    with pytest.raises(schema.PaletteError) as refused:
        schema.checked(SHAPES[layer], palette, LAYER_STYLES[layer])
    return str(refused.value)


def test_every_shipped_palette_has_exactly_its_shape():
    assert set(SHAPES) == set(LAYER_STYLES)
    for layer, shape in SHAPES.items():
        palette = _file(layer)
        assert schema.checked(shape, palette, LAYER_STYLES[layer]) is palette


@pytest.mark.parametrize("block", ["families", "tops"])
def test_a_calibration_target_for_a_family_the_game_lacks_is_refused(block):
    palette = _file("painted")
    palette["calibration"][block]["lava"] = "#000000"
    assert "lava" in _refusal("painted", palette)


def test_a_missing_and_a_stray_key_are_named_with_their_place():
    palette = _file("terrain")
    del palette["shore"]["clarity_m"]
    assert _refusal("terrain", palette) == "terrain-hypsometric.shore: missing ['clarity_m']"
    palette = _file("painted")
    palette["water_classes"]["lakeblue"] = palette["water_classes"]["lake_blue"]
    assert "water_classes: not read ['lakeblue']" in _refusal("painted", palette)


def test_values_are_held_to_their_type():
    palette = _file("satellite")
    palette["falls"]["edge_m"] = True
    assert "falls.edge_m: True is not a number" in _refusal("satellite", palette)
    palette = _file("satellite")
    palette["noise_seed"] = 1.5
    assert "noise_seed: 1.5 is not a whole number" in _refusal("satellite", palette)
    palette = _file("relief")
    palette["shade"]["mode"] = "multiply"
    assert "shade.mode: 'multiply'" in _refusal("relief", palette)
