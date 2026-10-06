"""The landscape's baked ground colour: the HLOD proxies' unlit BaseColor, on the 1 m grid.

Each landscape HLOD cell of the persistent level ships a 1024 px virtual-texture BaseColor of
its 508 m square. Read here once per build into the paint store, together with the paint
layers' albedos refitted to it. docs/spatial-and-map.md section 30 describes both.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path

import numpy as np

from mapgen.common import ROOT
from mapgen.gamedata.frame import ORIGIN_X_CM, ORIGIN_Y_CM, SPACING_CM
from mapgen.gamedata.sweep import LANDSCAPE_SECTION_ORIGIN
from satisfactory_mcp.core.gameassets.packages import PackageView, class_name_of, property_tags

__all__ = [
    "BAKE_CELL_M",
    "BAKE_NAME",
    "BAKE_PATTERN",
    "FIT_MIN_PURE",
    "FIT_SAMPLES",
    "FIT_STEP",
    "NODE_TABLE",
    "OIL_NODE",
    "PERSISTENT_LEVEL",
    "STAMP_INNER_M",
    "STAMP_OUTER_M",
    "STAMP_RING_MIN",
    "VT_BORDER_PX",
    "VT_BULK_FLAGS",
    "VT_TILE_PX",
    "bake_cell_origin",
    "bake_have",
    "demorton",
    "fit_layer_table",
    "oil_nodes",
    "read_bake",
    "stamp_windows",
]

BAKE_NAME = "bake.rgb.u8.z"
PERSISTENT_LEVEL = "/GameLevel01/Persistent_Level.umap"
BAKE_PATTERN = re.compile(r"LandscapeStreamingProxy.*_508_(-?\d+)_(-?\d+)_0_BaseColor$")

#: One HLOD cell, metres: the landscape's own section size.
BAKE_CELL_M = 508

#: The virtual texture's tile layout: 128 px tiles with a 4 px border, BC1, Morton order.
VT_TILE_PX = 128
VT_BORDER_PX = 4
#: The bulk flags of a virtual texture's mip-0 chunk on this build.
VT_BULK_FLAGS = 66817

#: The refit samples every ``FIT_STEP``-th texel, at most ``FIT_SAMPLES`` of them; a layer
#: dominant (weight above 0.9) on fewer than ``FIT_MIN_PURE`` keeps its name-matched albedo.
FIT_STEP = 4
FIT_SAMPLES = 400_000
FIT_MIN_PURE = 200

#: The node table, and the node whose oil puddle every bake cell carries as a stamp.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.json"
OIL_NODE = ("BP_ResourceNode_C", "Desc_LiquidOil_C")

#: The bake gives way within ``STAMP_INNER_M`` of an oil node and is back by ``STAMP_OUTER_M``;
#: the ring between is measured when it holds at least ``STAMP_RING_MIN`` texels.
STAMP_INNER_M = 11.0
STAMP_OUTER_M = 15.0
STAMP_RING_MIN = 50


def demorton(k: int) -> tuple[int, int]:
    """Tile index to (x, y): the even bits are x, the odd bits y."""
    x = y = 0
    for bit in range(16):
        x |= ((k >> (2 * bit)) & 1) << bit
        y |= ((k >> (2 * bit + 1)) & 1) << bit
    return x, y


def bake_cell_origin(gx: int, gy: int) -> tuple[int, int]:
    """Where cell ``(gx, gy)``'s north-west texel lands on the 1 m grid, as (row, col)."""
    origin = int(LANDSCAPE_SECTION_ORIGIN)
    col = gx * BAKE_CELL_M - origin + round(-ORIGIN_X_CM / SPACING_CM)
    row = gy * BAKE_CELL_M - origin + round(-ORIGIN_Y_CM / SPACING_CM)
    return row, col


def bake_have(rgb: np.ndarray) -> np.ndarray:
    """Where the bake says anything: covered and not one of its black holes."""
    return rgb.astype(np.uint16).sum(-1) >= 3


def oil_nodes(table: Path = NODE_TABLE) -> np.ndarray:
    """``(n, 2)`` world metres of every crude oil node in the table; none without one."""
    try:
        nodes = json.loads(table.read_text(encoding="utf-8"))["nodes"]
    except (OSError, ValueError, KeyError):
        nodes = []
    found = [(n["x"] / 100.0, n["y"] / 100.0) for n in nodes
             if (n.get("class"), n.get("resource")) == OIL_NODE]  # fmt: skip
    return np.asarray(found, np.float64).reshape(-1, 2)


def stamp_windows(nodes_m: np.ndarray, shape: tuple[int, int]):
    """Per node on the 1 m grid of ``shape``: its window's slices and the bake's own share
    there, 0 within ``STAMP_INNER_M`` and 1 again from ``STAMP_OUTER_M``, smoothstepped."""
    reach = int(np.ceil(STAMP_OUTER_M * 100.0 / SPACING_CM)) + 1
    for x_m, y_m in nodes_m:
        col = (x_m * 100.0 - ORIGIN_X_CM) / SPACING_CM
        row = (y_m * 100.0 - ORIGIN_Y_CM) / SPACING_CM
        r0, c0 = max(int(row) - reach, 0), max(int(col) - reach, 0)
        r1, c1 = min(int(row) + reach + 1, shape[0]), min(int(col) + reach + 1, shape[1])
        if r0 >= r1 or c0 >= c1:
            continue
        rr, cc = np.ogrid[r0:r1, c0:c1]
        metres = np.hypot(rr - row, cc - col) * SPACING_CM / 100.0
        t = np.clip((metres - STAMP_INNER_M) / (STAMP_OUTER_M - STAMP_INNER_M), 0.0, 1.0)
        yield (slice(r0, r1), slice(c0, c1)), (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _mip0(view, export, entries, ubulk: bytes, decoder) -> np.ndarray | None:
    """The cell's mip 0 as (h, w, 3) uint8, or ``None`` when its layout is not the known one."""
    body = view.pkg.body(export)
    tags, _end = property_tags(body, view.pkg.names)
    size = next((raw for name, _kind, raw, _v in tags if name == "ImportedSize"), None)
    if size is None or len(size) != 8:
        return None
    width, height = struct.unpack("<ii", size)
    across, down = -(-width // VT_TILE_PX), -(-height // VT_TILE_PX)
    side = VT_TILE_PX + 2 * VT_BORDER_PX
    tile_bytes = (side // 4) ** 2 * 8
    want = 4 + across * down * tile_bytes
    # The export names its mip-0 chunk by bulk index somewhere in its body; the chunk's size
    # and flags single it out.
    found = None
    for at in range(len(body) - 3):
        ref = struct.unpack_from("<I", body, at)[0]
        entry = entries[ref] if ref < len(entries) else None
        if entry and entry["size"] == want and entry["flags"] == VT_BULK_FLAGS:
            found = entry
            break
    if found is None:
        return None
    chunk = ubulk[found["offset"] : found["offset"] + want]
    img = np.zeros((down * VT_TILE_PX, across * VT_TILE_PX, 3), np.uint8)
    inner = slice(VT_BORDER_PX, VT_BORDER_PX + VT_TILE_PX)
    for k in range(across * down):
        raw = chunk[4 + k * tile_bytes : 4 + (k + 1) * tile_bytes]
        tile = np.frombuffer(decoder.decode_bc1(raw, side, side), np.uint8)
        x, y = demorton(k)
        if x >= across or y >= down:
            return None
        rgb = tile.reshape(side, side, 4)[inner, inner][..., [2, 1, 0]]
        img[y * VT_TILE_PX : (y + 1) * VT_TILE_PX, x * VT_TILE_PX : (x + 1) * VT_TILE_PX] = rgb
    return img[:height, :width]


def read_bake(store, scripts, decoder, image_mod, grid: int) -> tuple[np.ndarray, dict]:
    """Every landscape HLOD cell's BaseColor, box-filtered onto the ``grid`` square at 1 m.

    Black where no cell reached and in the bake's own holes; ``bake_have`` tells them apart
    from colour. Cells whose layout is not the 1024 px one are counted and left out.
    """
    path = next((p for p in store.by_path if p.endswith(PERSISTENT_LEVEL)), None)
    if path is None:
        raise ValueError(f"no {PERSISTENT_LEVEL} in the container")
    view = PackageView(store.read_path(path), scripts)
    entries = view.pkg.bulk_entries()
    ubulk = store.read_path(path[: -len(".umap")] + ".ubulk")
    rgb = np.zeros((grid, grid, 3), np.uint8)
    cells, skipped = 0, []
    for export in view.exports:
        match = BAKE_PATTERN.search(export["name"])
        if not match or class_name_of(view.class_of[export["slot"]]) != "Texture2D":
            continue
        cells += 1
        img = _mip0(view, export, entries, ubulk, decoder)
        if img is None:
            skipped.append(export["name"][-32:])
            continue
        small = np.asarray(
            image_mod.fromarray(img).resize((BAKE_CELL_M, BAKE_CELL_M), image_mod.BOX)
        )
        row, col = bake_cell_origin(int(match.group(1)), int(match.group(2)))
        r0, c0 = max(row, 0), max(col, 0)
        r1, c1 = min(row + BAKE_CELL_M, grid), min(col + BAKE_CELL_M, grid)
        if r0 < r1 and c0 < c1:
            rgb[r0:r1, c0:c1] = small[r0 - row : r1 - row, c0 - col : c1 - col]
    del ubulk
    have = bake_have(rgb)
    return rgb, {
        "cells": cells,
        "decoded": cells - len(skipped),
        "skipped": skipped,
        "cover_of_grid": round(float(have.mean()), 4),
    }


def fit_layer_table(
    weights: dict[str, np.ndarray], table: dict[str, list[float]], bake_rgb: np.ndarray
) -> tuple[dict[str, list[float]], dict]:
    """Per-layer linear albedo refitted to the bake: a non-negative least squares per channel.

    ``weights`` are the uint8 planes of the blended layers, ``table`` their name-matched
    albedo, which a layer seldom painted on its own keeps.
    """
    from scipy.optimize import nnls

    names = [name for name in table if name in weights]
    sample = (slice(None, None, FIT_STEP), slice(None, None, FIT_STEP))
    stack = np.stack([weights[n][sample].astype(np.float32) / 255.0 for n in names], -1)
    total = stack.sum(-1)
    bake = bake_rgb[sample]
    ok = bake_have(bake) & (total > 0.5)
    mix = stack[ok] / total[ok][:, None]
    c = bake[ok].astype(np.float64) / 255.0
    target = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    rng = np.random.default_rng(0)
    pick = rng.choice(len(mix), min(FIT_SAMPLES, len(mix)), replace=False)
    fitted = np.zeros((len(names), 3), np.float64)
    for k in range(3):
        fitted[:, k], _residual = nnls(mix[pick].astype(np.float64), target[pick, k])
    dominant, strength = mix.argmax(-1), mix.max(-1)
    out, pure = {}, {}
    for j, name in enumerate(names):
        pure[name] = int(((dominant == j) & (strength > 0.9)).sum())
        value = fitted[j] if pure[name] >= FIT_MIN_PURE else np.asarray(table[name])
        out[name] = [round(float(v), 5) for v in value]
    return out, {
        "samples": len(pick),
        "pure_texels": pure,
        "kept": sorted(n for n in names if pure[n] < FIT_MIN_PURE),
    }
