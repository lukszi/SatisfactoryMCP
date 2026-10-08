"""``/api/maptiles/{id}/{z}/{x}/{y}?kind=``: a lit layer's unlit colour and lighting pyramid.

A layer drawn with ``--unlit`` names its lighting pyramid in its sidecar; the z0 probe carries
what the page's shader needs in ``X-Map-Light``. docs/maps_contract.md section 8.1.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp import config

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
)
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00/\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"


def _tree(root: Path, max_z: int, suffix: str, payload: bytes) -> None:
    for z in range(max_z + 1):
        for x in range(1 << z):
            for y in range(1 << z):
                path = root / str(z) / f"{x}_{y}{suffix}"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)


def _lit_layer(local: Path, light_dir: str = "../light") -> None:
    layer = local / "renders" / "terrain"
    _tree(layer / "tiles", 1, ".png", PNG)
    _tree(layer / "unlit", 1, ".png", PNG)
    params = {
        "space": "srgb",
        "ambient": 0.54,
        "sky": [1, 1, 1],
        "sun": [1, 1, 1],
        "tone_knee": 1.0,
        "tone_white": 1.0,
    }
    sidecar = {
        "_meta": {
            "generator": "tools/gen_map_renders.py",
            "layer": "terrain",
            "tiles": {"max_z": 1, "count": 5},
            "light": {
                "dir": light_dir,
                "unlit_dir": "unlit",
                "unlit_tiles": {"max_z": 1, "bytes": 5},
                "params": params,
                "baked_sun": [225.0, 62.25],
            },
        }
    }
    (layer / "meta.json").write_text(json.dumps(sidecar), encoding="utf-8")
    light = local / "renders" / "light"
    for suffix in (".nrm.webp", ".hz.webp"):
        _tree(light / "tiles", 1, suffix, WEBP)
    meta = {"_meta": {"kind": "light", "tiles": {"max_z": 1, "count": 5, "bytes": 99},
                      "light": {"id": "sun", "label": "live sun", "dirs": 32, "digest": "sha256:x",
                                "normalise_min_el": 35.0}}}  # fmt: skip
    (light / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_a_lit_layer_serves_its_unlit_and_light_trees_and_says_so_on_the_probe(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    _lit_layer(tmp_path / "local")
    head = client.head("/api/maptiles/terrain/0/0/0")
    assert head.status_code == 200
    light = json.loads(head.headers["x-map-light"])
    assert light["max_z"] == 1 and light["unlit_max_z"] == 1
    assert light["params"]["space"] == "srgb" and light["model"]["dirs"] == 32
    assert "digest" not in light["model"]
    assert "x-map-light" not in client.head("/api/maptiles/terrain/1/0/0").headers

    nrm = client.get("/api/maptiles/terrain/1/1/0?kind=nrm&v=" + light["build"])
    assert nrm.status_code == 200 and nrm.headers["content-type"] == "image/webp"
    assert nrm.content == WEBP and "immutable" in nrm.headers["cache-control"]
    assert client.get("/api/maptiles/terrain/0/0/0?kind=hz").content == WEBP
    unlit = client.get("/api/maptiles/terrain/1/0/1?kind=unlit")
    assert unlit.status_code == 200 and unlit.headers["content-type"] == "image/png"
    etag = nrm.headers["etag"]
    again = client.get("/api/maptiles/terrain/1/1/0?kind=nrm", headers={"If-None-Match": etag})
    assert again.status_code == 304


def test_a_bad_kind_a_tile_off_the_tree_or_an_unlit_less_layer_is_a_404(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    _lit_layer(tmp_path / "local")
    assert client.get("/api/maptiles/terrain/0/0/0?kind=png").status_code == 404
    assert client.get("/api/maptiles/terrain/2/0/0?kind=nrm").status_code == 404
    assert client.get("/api/maptiles/terrain/1/2/0?kind=hz").status_code == 404
    (tmp_path / "local" / "renders" / "light" / "tiles" / "1" / "0_0.hz.webp").unlink()
    assert client.head("/api/maptiles/terrain/1/0/0?kind=hz").status_code == 204
    assert client.get("/api/maptiles/terrain/1/0/0?kind=hz").status_code == 404


def _apart(local: Path, trees_bytes: int = 7) -> None:
    """The lit layer redrawn apart at its trees: its ground as WebP, its trees a sparse tree
    holding z0 and one z1 tile."""
    layer = local / "renders" / "terrain"
    for old in (layer / "unlit").rglob("*.png"):
        old.unlink()
    _tree(layer / "unlit", 1, ".webp", WEBP)
    for name in ("0/0_0", "1/1_0"):
        path = layer / "trees" / f"{name}.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(WEBP)
    sidecar = json.loads((layer / "meta.json").read_text(encoding="utf-8"))
    light = sidecar["_meta"]["light"]
    light["unlit_tiles"] = {"max_z": 1, "count": 5, "bytes": 5, "layout": "unlit/{z}/{x}_{y}.webp"}
    light["trees_dir"] = "trees"
    light["trees_tiles"] = {"max_z": 1, "count": 2, "bytes": trees_bytes, "sparse": True,
                            "layout": "trees/{z}/{x}_{y}.webp"}  # fmt: skip
    (layer / "meta.json").write_text(json.dumps(sidecar), encoding="utf-8")


def test_a_layer_drawn_apart_serves_its_ground_and_its_sparse_trees(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    _lit_layer(tmp_path / "local")
    before = json.loads(client.head("/api/maptiles/terrain/0/0/0").headers["x-map-light"])
    assert before["parts"] == {}, "drawn whole: no parts to switch"
    assert client.get("/api/maptiles/terrain/0/0/0?kind=trees").status_code == 404
    _apart(tmp_path / "local")
    light = json.loads(client.head("/api/maptiles/terrain/0/0/0").headers["x-map-light"])
    assert light["parts"] == {"trees": {"max_z": 1, "sparse": True}}
    assert light["build"] != before["build"], "a recut changes every URL"
    tag = "&v=" + light["build"]
    unlit = client.get("/api/maptiles/terrain/1/0/1?kind=unlit" + tag)
    assert unlit.status_code == 200 and unlit.headers["content-type"] == "image/webp"
    trees = client.get("/api/maptiles/terrain/1/1/0?kind=trees" + tag)
    assert trees.status_code == 200 and trees.content == WEBP
    assert trees.headers["content-type"] == "image/webp"
    for method in (client.get, client.head):
        empty = method("/api/maptiles/terrain/1/0/0?kind=trees" + tag)
        assert empty.status_code == 204 and not empty.content
        assert "immutable" in empty.headers["cache-control"]
    assert client.get("/api/maptiles/terrain/2/0/0?kind=trees").status_code == 404
    _apart(tmp_path / "local", trees_bytes=8)
    again = json.loads(client.head("/api/maptiles/terrain/0/0/0").headers["x-map-light"])
    assert again["build"] != light["build"], "the trees' bytes are in the tag"


def test_a_light_dir_outside_data_local_is_never_followed(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    _lit_layer(tmp_path / "local", light_dir="../../../outside")
    outside = tmp_path / "outside" / "tiles" / "0"
    outside.mkdir(parents=True)
    (outside / "0_0.nrm.webp").write_bytes(WEBP)
    (tmp_path / "outside" / "meta.json").write_text(
        json.dumps({"_meta": {"tiles": {"max_z": 0}}}), encoding="utf-8"
    )
    assert "x-map-light" not in client.head("/api/maptiles/terrain/0/0/0").headers
    assert client.get("/api/maptiles/terrain/0/0/0?kind=nrm").status_code == 404


def test_the_render_preset_bakes_live_light_by_default_and_names_it(game):
    from satisfactory_mcp.domain.maps import axes, presets

    options = presets.normalise("render", {"size": 1024})
    assert options["light"] is True
    argv = presets.plan("render", options, "j1", 502094, set())["argv"]
    assert "--light" in argv and "--no-light" not in argv
    stages = list(presets.stage_plan("render", options))
    assert stages.index("light") == stages.index("draw") + 1
    dark = presets.normalise("render", {"size": 1024, "light": False})
    argv = presets.plan("render", dark, "j2", 502094, set())["argv"]
    assert "--no-light" in argv and "--light" not in argv
    assert "light" not in presets.stage_plan("render", dark)
    with pytest.raises(presets.PresetError):
        presets.normalise("render", {"light": "yes"})
    named = axes.display_name({"style": {"label": "terrain"}, "light": {"label": "live sun"}})
    assert named.endswith("· live sun")
