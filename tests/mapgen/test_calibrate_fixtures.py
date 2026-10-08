"""Derived colours against the screenshot targets, on a real paint store and the installed game.

docs/map/calibration.md section 31. Marked ``integration``: it needs the install and a store
from paint generator 4 or later, ``data/local/paint`` unless ``MAPGEN_PAINT_DIR`` names another
(and the heightfield of ``MAPGEN_FIELD_DIR``, else ``data/local/heightmap``). It only reads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from mapgen.common import DEFAULT_GAME, LOCAL_DIR
from mapgen.palette.painted.calibration import derived_hex
from mapgen.palette.painted.derive.camera import delta_e, lab_of_hex
from mapgen.palette.painted.derive.gate import gate_hex
from tests.support.paths import committed_fixture

pytestmark = pytest.mark.integration

FIXTURES = json.loads(committed_fixture("calibration_screenshots.json").read_text(encoding="utf-8"))
PAINT = Path(os.environ.get("MAPGEN_PAINT_DIR") or LOCAL_DIR / "paint")
FIELD = Path(os.environ.get("MAPGEN_FIELD_DIR") or LOCAL_DIR / "heightmap")


def _target(key: str) -> str:
    entry = FIXTURES["fixtures"][key]
    if "hex" in entry:
        return entry["hex"]
    ratio = entry["ratio"]
    return derived_hex(_target(ratio["of"]), ratio)


@pytest.fixture(scope="module")
def derived():
    from mapgen.gamedata.ground.biome import read_biome
    from mapgen.gamedata.install import missing_container, open_game
    from mapgen.palette.painted.albedo import load_paint_meta
    from mapgen.palette.painted.derive.scene import area_grid, scene_from_store
    from mapgen.palette.painted.derive.targets import derive
    from mapgen.palette.styles import PAINTED_PALETTE, biome_lookup
    from satisfactory_mcp.domain.spatial import heightfield as hf

    if missing_container(DEFAULT_GAME):
        pytest.skip(f"needs the installed game at {DEFAULT_GAME}")
    meta = load_paint_meta(PAINT)
    if meta is None or not meta.get("lighting"):
        pytest.skip(f"needs a paint store from generator 4 or later at {PAINT}")
    game = open_game(DEFAULT_GAME)
    biome = read_biome(game.store, game.scripts)
    field = hf.load_field(FIELD, cache=False)
    shape = (meta["grid"]["height"], meta["grid"]["width"])
    areas = area_grid(biome, biome_lookup(biome)[1], field, shape)
    found = derive(scene_from_store(PAINT, meta, areas), PAINTED_PALETTE["calibration"])
    return meta, {t.key: t for t in found.targets}, found


def test_every_scored_key_lies_within_its_tolerance_of_its_screenshot(derived):
    _meta, targets, _found = derived
    default = FIXTURES["default_tolerance"]
    far = []
    for key, entry in FIXTURES["fixtures"].items():
        tolerance = entry.get("tolerance", default)
        if tolerance is None:
            continue
        colour = targets[key].hex
        gap = delta_e(lab_of_hex(colour), lab_of_hex(_target(key))) if colour else None
        if gap is None or gap > tolerance:
            far.append(f"  {key}: {colour} vs {_target(key)}, {gap} > {tolerance}")
        elif "tolerance" in entry and (tolerance <= default or tolerance > gap + 1.5):
            far.append(f"  {key}: tolerance {tolerance} is stale at {gap:.1f}, lower it")
    assert not far, "\n".join(far)


@pytest.mark.parametrize(
    "key",
    [
        "rock",
        "areas[GrassFields,NorthernForest,WesternDuneForest].rock",
        "areas[RedJungle,RedBambooFields].rock",
    ],
)
def test_the_rock_derives_the_cliff_body_within_the_gate_s_chroma_and_hue(derived, key):
    _meta, targets, _found = derived
    assert targets[key].rule.kind == "cliff body texture"
    verdict = gate_hex(targets[key].hex, _target(key))
    assert verdict.chromatic <= verdict.allowance, f"{key}: {verdict}"


def _chroma_hue(hex_colour: str) -> tuple[float, float]:
    lab = lab_of_hex(hex_colour)
    return float(np.hypot(lab[1], lab[2])), float(np.degrees(np.arctan2(lab[2], lab[1])) % 360)


def test_the_desert_canyons_cliffs_draw_grey_and_the_desert_family_terracotta(derived):
    from mapgen.palette.painted.derive.targets import with_targets
    from mapgen.palette.styles import PAINTED_PALETTE

    _meta, _targets, found = derived
    cal = with_targets(PAINTED_PALETTE, found.hexes()).palette["calibration"]

    def rock_of(area: str) -> str:
        own = (e["rock"] for e in cal["areas"] if "rock" in e and area in e["areas"])
        return next(own, cal["rock"])

    for area in ("Area_DesertCanyons", "Area_RockyDesert"):
        assert _chroma_hue(rock_of(area))[0] < 0.04, area
    for colour in (cal["families"]["desert"], rock_of("Area_DuneDesert")):
        chroma, hue = _chroma_hue(colour)
        assert chroma > 0.05 and 25.0 < hue < 70.0, colour


def test_the_exposure_of_build_502094(derived):
    meta, _targets, found = derived
    if meta.get("cl") != 502094:
        pytest.skip("the exposure is pinned for build 502094")
    assert found.exposure == pytest.approx(2.81, rel=0.02)


def test_the_dunes_vote_for_the_dune_light_and_the_sand_for_the_level_s(derived):
    _meta, targets, _found = derived
    assert targets["layers.SandRipples_LayerInfo"].light == "Atmosphere_DuneDesert"
    assert targets["layers.Sand_LayerInfo"].light == "global"
    assert targets["families.desert"].light == "Atmosphere_DuneDesert"
