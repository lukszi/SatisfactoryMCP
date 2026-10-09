"""The crown sprite cache: mip chains, the packed atlas and its records, the stamp.

docs/map/light-and-crowns.md section 36, "Crown sprites". Synthetic sprites: no install.
"""

from __future__ import annotations

import itertools
import json

import numpy as np
import pytest

from mapgen.cache import CACHE_SIDECAR_NAME
from mapgen.gamedata.vegetation.crown_sprites import CROWN_RECORD
from mapgen.sprites import store
from mapgen.sprites.raster import SPRITE_CM, SpritePlanes

LEAF = np.array([0.08, 0.20, 0.04], np.float32)


def _sprite(h, w, x0=-100.0, y0=-50.0, seed=0):
    rng = np.random.default_rng(seed)
    alpha = np.zeros((h, w), np.float32)
    alpha[1:-1, 1:-1] = rng.uniform(0.5, 1.0, (h - 2, w - 2)).astype(np.float32)
    colour = np.where(alpha[..., None] > 0, LEAF * rng.uniform(0.8, 1.2, (h, w, 1)), 0.0)
    normal = np.zeros((h, w, 3), np.float32)
    normal[..., 2] = 1.0
    normal[1:-1, 1:-1, 0] = 0.6
    normal[1:-1, 1:-1, 2] = 0.8
    top = np.where(alpha > 0, 1200.0, 0.0)
    return SpritePlanes(x0, y0, alpha, colour.astype(np.float32), normal, top.astype(np.float32))


def test_a_mip_chain_halves_to_four_texels_keeping_the_corner_and_the_mean():
    chain = store.mip_chain(_sprite(37, 20))
    assert [lv.alpha.shape for lv in chain] == [(37, 20), (19, 10), (10, 5), (5, 3), (3, 2)]
    assert all((lv.x0_cm, lv.y0_cm) == (-100.0, -50.0) for lv in chain)
    for upper, lower in itertools.pairwise(chain):
        assert lower.alpha.sum() * 4 == pytest.approx(upper.alpha.sum(), rel=1e-5)
        cover = upper.alpha[..., None]
        assert (lower.colour * lower.alpha[..., None]).sum((0, 1)) * 4 == pytest.approx(
            (upper.colour * cover).sum((0, 1)), rel=1e-4
        )
    assert np.allclose(np.linalg.norm(chain[-1].normal, axis=-1), 1.0)
    assert np.allclose(chain[2].top_cm[chain[2].alpha > 0], 1200.0)


def test_the_atlas_holds_every_level_in_its_own_rectangle_and_reads_back():
    sprites = [("A", _sprite(40, 30)), ("B", _sprite(9, 60, 10.0, 20.0, 1))]
    atlas = store.encode_atlas(sprites)
    assert atlas.names == ["A", "B"] and atlas.colour.shape[1] == store.ATLAS_WIDTH
    assert list(atlas.levels) == [len(store.mip_chain(p)) for _n, p in sprites]
    taken = np.zeros(atlas.colour.shape[:2], int)
    for rec in atlas.records:
        x, y, w, h = (int(rec[f]) for f in ("x", "y", "width", "height"))
        g = store.GUTTER
        taken[y - g : y + h + g, x - g : x + w + g] += 1
        assert rec["texel_cm"] == SPRITE_CM * 2 ** int(rec["level"])
    assert taken.max() == 1, "rectangles and their gutters never overlap"
    back = store.level_planes(atlas, 1)
    a = sprites[1][1]
    assert (back.x0_cm, back.y0_cm, back.alpha.shape) == (10.0, 20.0, a.alpha.shape)
    assert np.abs(back.alpha - a.alpha).max() <= 0.5 / 255 + 1e-6
    inside = a.alpha > 0
    assert np.allclose(back.colour[inside], a.colour[inside], rtol=0.06, atol=0.002)
    assert np.allclose(back.normal[inside], a.normal[inside], atol=0.01)
    assert np.all(back.top_cm[inside] == 1200.0)


def test_a_record_bounds_its_crown_under_any_yaw():
    sprite = _sprite(12, 16, x0=-100.0, y0=-50.0)
    rec = store.encode_atlas([("A", sprite)]).records[0]
    assert rec["top_max_cm"] == 1200.0
    # covered texels run from (-81.25, -31.25) to (81.25, 81.25) at their centres
    far = np.hypot(-100 + 14.5 * SPRITE_CM, -50 + 10.5 * SPRITE_CM)
    assert rec["reach_cm"] == pytest.approx(far + SPRITE_CM * np.sqrt(0.5), rel=1e-6)


def test_the_gutter_carries_the_edge_colour_at_no_alpha():
    atlas = store.encode_atlas([("A", _sprite(12, 12))])
    rec = atlas.records[0]
    x, y = int(rec["x"]), int(rec["y"])
    edge, gutter = atlas.colour[y + 1, x + 1], atlas.colour[y - 1, x - 1]
    assert gutter[3] == 0 and edge[3] > 0
    assert np.array_equal(gutter[:3], edge[:3]), "a bilinear read at the rim keeps its colour"


def test_an_upright_normal_reads_back_upright():
    sprite = _sprite(6, 6)
    sprite.normal[...] = (0.0, 0.0, 1.0)
    back = store.level_planes(store.encode_atlas([("A", sprite)]), 0)
    assert np.all(back.normal[..., :2] == 0.0) and np.all(back.normal[..., 2] == 1.0)


def test_the_cache_is_read_only_under_its_own_stamp(tmp_path):
    titan = np.zeros(3, CROWN_RECORD)
    titan["x"], titan["species"] = (1.0, 2.0, 3.0), 0
    atlas = store.encode_atlas([("A", _sprite(12, 12))], titan)
    stamp = store.sprite_stamp("b1")
    species = [{"name": "A", "source": "mesh raster"}]
    store.write_sprites(tmp_path, stamp, atlas, species, ["B"])
    found = store.read_sprites(tmp_path, stamp)
    assert found is not None
    again, meta = found
    assert again.names == ["A"] and again.records.tobytes() == atlas.records.tobytes()
    assert again.titan.tobytes() == titan.tobytes(), "the Titan canopy's placements ride along"
    assert meta["species"] == species and meta["skipped"] == ["B"]
    assert meta["titan_placements"] == 3
    assert store.read_sprites(tmp_path, store.sprite_stamp("b2")) is None, "another build"
    sidecar = json.loads((tmp_path / CACHE_SIDECAR_NAME).read_text(encoding="utf-8"))
    sidecar["format"] = store.FORMAT + 1
    (tmp_path / CACHE_SIDECAR_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    assert store.read_sprites(tmp_path, stamp) is None, "another layout"
    (tmp_path / CACHE_SIDECAR_NAME).unlink()
    assert store.read_sprites(tmp_path, stamp) is None, "a write cut short"
