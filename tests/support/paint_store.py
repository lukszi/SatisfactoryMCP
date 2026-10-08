"""A small synthetic paint store on disk, for the calibrate tests: no install, no data/local.

A 64 x 64 grid at 1 m from the origin, so a sample of the 4 m grid at row r, column c stands at
(4c + 0.5, 4r + 0.5) metres. Sand fills the west half and grass the east; a column band of each
is only 0.698 of its blend, and soil takes the south-east corner. The north half is
``Area_Desert``, the south ``Area_Grass``. Two
atmosphere volumes overlap in the west: the dune (priority 3) over x < 20 m and the oasis
(priority 5) over x < 8 m. Three species: a green tree, a blue palm and a red Kapok.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD, encode_records, encode_sprites
from satisfactory_mcp.domain.spatial import heightfield as hf

GRID = 64
NAMES = ["Area_Desert", "Area_Grass"]
SAND_RGB = (210, 190, 160)
GRASS_RGB = (110, 130, 80)
MIXED_RGB = (255, 0, 0)
#: The cliff body texture's mean, linear, as build 502094 has it.
CLIFF_BODY = [0.09605, 0.09008, 0.0859]
#: The cliff family's Color Tint, which the rock rule records and does not apply.
CLIFF_TINT = [0.624, 0.545, 0.471]
#: Columns where sand and grass mix at 178 and 77 of 255: not pure at 0.7.
MIXED_COLUMNS = slice(28, 36)

LIGHTING = {
    "level": "/GameLevel01/Persistent_Level.umap",
    "noon_h": 12.0,
    "sun_colour": [1.0, 0.9283739469394504, 0.7135382425944763],
    "sun_lux": 3.140000104904175,
    "sun_pitch_deg": -59.4914056493052,
    "day_seconds": 39600.0,
    "sky_luminance_factor": [1.1266670227050781, 1.1266670227050781, 1.2999999523162842],
    "auto_exposure": {"bias_ev": 1.5, "low_pct": 75.0, "high_pct": 95.0,
                      "min_brightness": 0.5, "max_brightness": 4.0},
}  # fmt: skip


def _volume(name: str, priority: float, x1: float, sun: list[float]) -> dict:
    return {"name": name, "level": "L.umap", "priority": priority,
            "noon": {"mSunLightColorCurve": sun, "mColorGainMidtones": [1.0, 0.95, 0.97]},
            "box_m": [0.0, x1, 0.0, 64.0, -10.0, 10.0],
            "hull_xy_m": [[0.0, 0.0], [x1, 0.0], [x1, 64.0], [0.0, 64.0]]}  # fmt: skip


VOLUMES = [
    _volume("Atmosphere_DuneDesert", 3.0, 20.0, [1.0, 0.88, 0.66]),
    _volume("Atmosphere_Oasis", 5.0, 8.0, [1.0, 0.80, 0.44]),
]

CALIBRATION = {
    "about": "test",
    "pure_share": 0.7,
    "min_texels": 4,
    "area_blur_m": 4.0,
    "layers": {"Sand_LayerInfo": "#d5cbb6", "Grass_LayerInfo": "#83986e"},
    "derived": {"WetSand_LayerInfo": {"from": "Sand_LayerInfo", "lightness": 0.8, "chroma": 1.0,
                                      "hue_deg": -10.0}},
    "canopy": "#558653",
    "rock": "#85816c",
    "rock_keeps_exposure": True,
    "families": {"desert": "#ae8271"},
    "tops": {"forest": "#505936"},
    "meshes": {"coral": "#99868e", "coral_seabed": "#5f8899", "kelp": "#00ff00"},
    "crowns": {"blue_palm": "#3d627d"},
    "species": {"SM_Kapok_03": "#7c4955"},
    "areas": [
        {"areas": ["Area_Desert"], "layers": {"Sand_LayerInfo": "#c4ab8b"}, "rock": "#ae8271"},
        {"areas": ["Area_Grass"], "water": "#302627", "water_class": "swamp"},
    ],
}  # fmt: skip


def area_cells() -> np.ndarray:
    """The area index on the 16 x 16 sample grid: desert north, grass south."""
    cells = np.zeros((GRID // 4, GRID // 4), np.uint8)
    cells[GRID // 8 :] = 1
    return cells


def _weights() -> dict[str, np.ndarray]:
    sand = np.zeros((GRID, GRID), np.uint8)
    grass = np.zeros((GRID, GRID), np.uint8)
    sand[:, :32], grass[:, 32:] = 255, 255
    sand[:, MIXED_COLUMNS], grass[:, MIXED_COLUMNS] = 178, 77
    wet = np.zeros((GRID, GRID), np.uint8)
    soil = np.zeros((GRID, GRID), np.uint8)
    soil[60:, 36:], grass[60:, 36:] = 255, 0
    return {
        "Sand_LayerInfo": sand,
        "Grass_LayerInfo": grass,
        "WetSand_LayerInfo": wet,
        "Soil_LayerInfo": soil,
    }


def _bake(weights: dict[str, np.ndarray]) -> np.ndarray:
    bake = np.zeros((GRID, GRID, 3), np.uint8)
    bake[weights["Sand_LayerInfo"] == 255] = SAND_RGB
    bake[weights["Grass_LayerInfo"] == 255] = GRASS_RGB
    bake[:, MIXED_COLUMNS] = MIXED_RGB
    bake[weights["Soil_LayerInfo"] == 255] = (120, 90, 60)
    return bake


def _species(name: str, linear: list[float]) -> dict:
    return {"name": name, "mesh": f"/Game/FactoryGame/World/Environment/Foliage/Trees/{name}",
            "materials": [{"path": f"/Game/M_{name}", "kind": "leaf", "linear": linear,
                           "opacity": 1.0}], "radius_m": 2.0, "top_m": 10.0}  # fmt: skip


def _crowns() -> tuple[dict, bytes, bytes]:
    species = [_species("SM_GreenTree_01", [0.06, 0.12, 0.035]),
               _species("BluePalm_01", [0.27, 0.40, 0.46]),
               _species("SM_Kapok_03", [0.22, 0.04, 0.04])]  # fmt: skip
    cover = np.full((4, 4), 255, np.uint8)
    sprites = [{"x0_cm": -25.0, "y0_cm": -25.0, "cover": cover,
                "top_cm": np.full((4, 4), 900, np.uint16), "slot": np.zeros((4, 4), np.uint8)}
               for _ in species]  # fmt: skip
    blob, index = encode_sprites(sprites)
    for entry, sprite in zip(species, index, strict=True):
        entry["sprite"] = sprite
    records = np.zeros(90, CROWN_RECORD)
    records["species"] = np.repeat([0, 1, 2], 30)
    records["x"] = np.tile(np.linspace(100.0, 6300.0, 30), 3)
    records["y"] = 1000.0
    records["scale"] = records["scale_z"] = records["axis_z"] = 1.0
    block = {"species": species, "skipped": {}, "instances": 90, "tilt_max_deg": 0.0,
             "top_texels": 0, "seconds": 0.0}  # fmt: skip
    return block, encode_records(records), blob


def write_store(directory: Path, daylight: bool = True) -> Path:
    """The store under ``directory``; ``daylight`` False writes generator 3's meta."""
    directory.mkdir(parents=True, exist_ok=True)
    weights = _weights()
    files: dict[str, dict] = {}
    for name, plane in weights.items():
        (directory / f"w.{name}.u8.z").write_bytes(hf.encode_u8(plane))
        files[f"w.{name}.u8.z"] = {"shape": [GRID, GRID], "kind": "u8", "layer": name}
    bake = _bake(weights)
    (directory / "bake.rgb.u8.z").write_bytes(hf.encode_u8(bake.reshape(GRID, -1)))
    files["bake.rgb.u8.z"] = {"shape": [GRID, GRID, 3], "kind": "u8", "srgb": True}
    crowns, records, sprites = _crowns()
    (directory / "crowns.rec.z").write_bytes(records)
    (directory / "crowns.sprites.z").write_bytes(sprites)
    files["crowns.rec.z"] = {"kind": "records"}
    files["crowns.sprites.z"] = {"kind": "sprites"}
    meta: dict = {
        "generator_version": 4 if daylight else 3,
        "cl": 1,
        "digest": "store-digest",
        "grid": {"width": GRID, "height": GRID, "x0_cm": 0.0, "y0_cm": 0.0, "spacing_cm": 100.0},
        "files": files,
        "texture_means_linear": {"TX_Forest_Far_01_Alb": [0.052, 0.081, 0.028],
                                 "TX_SandRock_Alb_01": [0.491, 0.392, 0.287],
                                 "TX_Soil_01_Alb": [0.12, 0.09, 0.07],
                                 "Cliff_Sediment_Alb": CLIFF_BODY},
        "material_vectors": {"Sand Rock BaseColor": [0.594, 0.339, 0.328]},
        "rock_families": {"cliff": {"material": "/Game/Cliff", "tint": CLIFF_TINT},
                          "forest": {"material": "/Game/Cliff_Forest", "tint": None,
                                     "top_texture": "/Game/TX_Forest", "top": [0.05, 0.08, 0.03]},
                          "desert": {"material": "/Game/MI_DesertRock", "tint": None}},
        "crowns": crowns,
    }  # fmt: skip
    if daylight:
        meta.update(lighting=LIGHTING, atmosphere_volumes=VOLUMES, mesh_materials={})
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return directory
