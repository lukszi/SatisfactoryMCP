"""``/api/mapimage`` and ``/api/maptiles``: the base map, which is never shipped.

Each test writes its own ``data/local/`` tree under ``tmp_path``, because no render of this
world is committed. The generators' side of the contract is in ``tests/mapgen/test_generators.py``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from satisfactory_mcp import config
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
)
from satisfactory_mcp.domain.maps import registry
from satisfactory_mcp.interfaces.web.routers.assets import tiles as web_tiles
from tests.support.tiles import PNG_BYTES, fake_pyramid

#: The three ids the page used before the registry, which it still answers unregistered.
LEGACY_LAYERS = tuple(registry.LEGACY)


def test_the_map_image_is_a_loader_and_says_where_the_file_goes(client, tmp_path, monkeypatch):
    """No image is shipped, so the 404 has to be useful: it names the exact path.

    ``config.data_dir`` is redirected at the tmp tree, which is also what keeps this test
    honest about the repository never carrying one -- it writes the png itself.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    r = client.get("/api/mapimage")
    assert r.status_code == 404
    message = r.json()["error"]
    assert str(tmp_path / "local" / "map.png") in message
    assert "only ever read locally" in message
    # The HEAD probe runs on every page load and an absent optional file is the normal
    # answer, so it must not be a status the devtools console logs red.
    assert client.head("/api/mapimage").status_code == 204

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
    )
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "map.png").write_bytes(png)

    r = client.get("/api/mapimage")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png
    # The corners ride along on the probe the page already makes.
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"


def test_a_local_sidecar_can_repin_the_map_images_corners(client, tmp_path, monkeypatch):
    """Someone else's render will not share our corners, and a bad one is ignored."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "map.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (tmp_path / "local" / "map.json").write_text(json.dumps({"x_min_m": -4000, "y_max_m": 4000}))
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-4000.0,-3750.0,4253.0,4000.0"

    (tmp_path / "local" / "map.json").write_text("{not json")
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"


def _fake_layer(
    local: Path,
    layer: str,
    max_z: int = 2,
    sidecar: dict | None = None,
    payload: bytes = PNG_BYTES,
) -> int:
    """The same tree one level down, where ``tools/gen_map_renders.py`` writes a layer.

    Deliberately built through ``web_tiles``'s own names rather than a hand-typed path: what
    these tests are checking is that the endpoint finds a layer where the generator puts
    one, and a fixture that spelled the directory itself would agree with the endpoint by
    construction rather than by the module having got it right.
    """
    directory = local / web_tiles.MAP_RENDERS_DIR_NAME / layer
    directory.mkdir(parents=True, exist_ok=True)
    if sidecar is not None:
        (directory / web_tiles.MAP_RENDER_SIDECAR_NAME).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
    return fake_pyramid(directory, max_z, payload)


def test_the_tile_pyramid_is_a_loader_too_and_names_the_tool_that_writes_it(
    client, tmp_path, monkeypatch
):
    """Absent, present, and the probe the page opens with -- the same posture as map.png.

    The pyramid is the page's first choice for the base map, so "no pyramid" is asked on
    every clean load and must not be a console error: HEAD says 204 while GET keeps the
    404 that names the generator.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    r = client.get("/api/maptiles/0/0/0")
    assert r.status_code == 404
    message = r.json()["error"]
    assert str(tmp_path / "local" / "tiles") in message
    assert "gen_map_image.py" in message
    assert client.head("/api/maptiles/0/0/0").status_code == 204

    (tmp_path / "local").mkdir()
    fake_pyramid(tmp_path / "local")

    r = client.get("/api/maptiles/2/3/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == PNG_BYTES
    # The probe answers every question the tile layer is built from at once.
    head = client.head("/api/maptiles/0/0/0")
    assert head.status_code == 200
    assert head.headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"
    assert head.headers["x-map-tile-px"] == str(PYRAMID_TILE_PX)
    assert head.headers["x-map-tile-max-z"] == str(web_tiles.MAP_TILE_MAX_Z)
    # ``immutable`` is earned by the ``?v=`` build tag alone. The probe carries no tag, and
    # caching IT hard is the measured failure: a regenerated pyramid stayed invisible in
    # Firefox behind a year-old probe until the browser cache was disabled by hand.
    assert head.headers["cache-control"] == "no-cache"
    versioned = client.get("/api/maptiles/2/3/1?v=" + head.headers["x-map-build"])
    assert "immutable" in versioned.headers["cache-control"]
    # A tile is immutable per build, so the tag it is fetched under has to revalidate free.
    etag = head.headers["etag"]
    assert client.get("/api/maptiles/0/0/0", headers={"If-None-Match": etag}).status_code == 304


def test_a_tile_outside_the_pyramid_is_a_404_and_cannot_name_a_file(client, tmp_path, monkeypatch):
    """Off the grid, off the end, and shaped like an escape -- all answered, none served.

    The endpoint takes three ints, so the only strings that reach it are integers: a
    segment with a slash, a dot-dot or an encoded one never matches the route at all.
    That is the whole traversal argument, and it is asserted rather than asserted-to.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    (tmp_path / "local").mkdir()
    fake_pyramid(tmp_path / "local")

    # z0 is one tile, so (1, 0) is off its grid; z6 is past the top of this pyramid.
    for path in ("/api/maptiles/0/1/0", "/api/maptiles/0/0/1", "/api/maptiles/2/4/0"):
        assert client.get(path).status_code == 404, path
        assert "2**z" in client.get(path).json()["error"]
    assert client.get("/api/maptiles/6/0/0").status_code == 404
    assert client.get("/api/maptiles/-1/0/0").status_code == 404

    # And nothing that is not an integer is even routed to the handler.
    for path in (
        "/api/maptiles/0/0/..%2f..%2f..%2fmap",
        "/api/maptiles/0/..%2f..%2fmap/0",
        "/api/maptiles/0/0/0.png",
        "/api/maptiles/0/0/%2e%2e",
    ):
        assert client.get(path).status_code in (404, 422), path

    # The path builder says the same thing on its own, which is what the endpoint leans on.
    assert web_tiles.map_tile_path(0, 0, 0, 5).name == "0_0.png"
    assert web_tiles.map_tile_path(0, 1, 0, 5) is None
    assert web_tiles.map_tile_path(6, 0, 0, 5) is None
    assert web_tiles.map_tile_path(3, 7, 7, 5) is not None
    assert web_tiles.map_tile_path(3, 8, 0, 5) is None


#: Three pyramids of identical bytes could not show that a request reached the tree it
#: named, so each fixture layer gets a PNG of its own. All three are valid 1x1 PNGs -- the
#: point is which one comes back, not what is in it.
_PNG_TERRAIN = PNG_BYTES[:-4] + b"TERR"
_PNG_SATELLITE = PNG_BYTES[:-4] + b"SATL"


def test_a_named_layer_is_served_from_its_own_tree_and_the_bare_route_is_still_map(
    client, tmp_path, monkeypatch
):
    """Three pictures of one world, one grid, one path segment between them.

    The design is that a layer is a directory and nothing else: same frame, same tile size,
    same ``{z}/{x}_{y}.png``. So what has to hold is that naming a layer reaches THAT tree
    -- proven with three different payloads rather than three identical ones -- and that the
    route which existed before layers did still answers exactly what ``map`` answers, byte
    for byte and header for header. That last part is not a nicety: the live page addresses
    the base map through the bare route, and this branch must not be able to break it.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    fake_pyramid(local)
    _fake_layer(local, "terrain", payload=_PNG_TERRAIN)
    _fake_layer(local, "satellite", payload=_PNG_SATELLITE)

    for layer, payload in (
        ("map", PNG_BYTES),
        ("terrain", _PNG_TERRAIN),
        ("satellite", _PNG_SATELLITE),
    ):
        r = client.get(f"/api/maptiles/{layer}/2/3/1")
        assert r.status_code == 200, layer
        assert r.headers["content-type"] == "image/png"
        assert r.content == payload, layer
        assert r.headers["x-map-layer"] == layer

    # The alias is the same answer, not a similar one.
    bare = client.get("/api/maptiles/2/3/1")
    named = client.get("/api/maptiles/map/2/3/1")
    assert bare.status_code == named.status_code == 200
    assert bare.content == named.content == PNG_BYTES
    for header in (
        "x-map-bounds-m",
        "x-map-layer",
        "x-map-tile-px",
        "x-map-tile-max-z",
        "x-map-build",
        "cache-control",
        "etag",
    ):
        assert bare.headers[header] == named.headers[header], header

    # And the two routes cannot collide: three segments has no layer to name.
    assert web_tiles.map_tile_path(2, 3, 1) == local / TILES_DIR_NAME / "2" / "3_1.png"
    assert web_tiles.map_tile_path(2, 3, 1, layer="terrain") == (
        local / web_tiles.MAP_RENDERS_DIR_NAME / "terrain" / TILES_DIR_NAME / "2" / "3_1.png"
    )


def test_a_layer_that_does_not_exist_and_one_that_was_never_generated_are_told_apart(
    client, tmp_path, monkeypatch
):
    """Two different absences, two different answers, and neither is a stack trace.

    A layer this server has never heard of is a 404 that lists the ones it has, because the
    reader mistyped a name and the useful reply is the vocabulary. A layer it knows but
    nobody has generated is the ordinary state of a fresh checkout: HEAD says 204 so a
    probing page leaves no red line in the console, and GET says which tool would write it
    -- gen_map_renders.py for a render, gen_map_image.py for the artwork, because "run the
    generator" is not help when there are two.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    unknown = client.get("/api/maptiles/bathymetry/0/0/0")
    assert unknown.status_code == 404
    message = unknown.json()["error"]
    assert "bathymetry" in message
    for layer in LEGACY_LAYERS:
        assert layer in message

    for layer, tool in (
        ("map", "gen_map_image.py"),
        ("terrain", "gen_map_renders.py"),
        ("satellite", "gen_map_renders.py"),
    ):
        assert client.head(f"/api/maptiles/{layer}/0/0/0").status_code == 204, layer
        absent = client.get(f"/api/maptiles/{layer}/0/0/0")
        assert absent.status_code == 404
        assert tool in absent.json()["error"], layer

    # An unknown layer is refused before any of that, so it can never name a file.
    assert web_tiles.map_tile_path(0, 0, 0, layer="bathymetry") is None
    assert web_tiles._layer_dir("bathymetry") is None
    assert web_tiles._layer_sidecar("bathymetry") is None
    # Including when it is shaped like an escape: there is no join for it to escape through.
    for shape in ("..", "../..", "map/../..", ".", ""):
        assert web_tiles.map_tile_path(0, 0, 0, layer=shape) is None, shape
    for path in (
        "/api/maptiles/..%2f..%2ftiles/0/0/0",
        "/api/maptiles/%2e%2e/0/0/0",
        "/api/maptiles/terrain/0/0/..%2f..%2fmap",
        "/api/maptiles/terrain/0/0/0.png",
    ):
        assert client.get(path).status_code in (404, 422), path


def test_every_layer_answers_with_its_own_depth_build_and_corners(client, tmp_path, monkeypatch):
    """A layer's headers come from the sidecar beside its own tiles, never from another's.

    They are generated by different tools at different times: the artwork can be two levels
    deeper than the renders if it was enhanced, and a satellite recut this morning must not
    invalidate the terrain a browser cached last week. So depth, corners and build tag are
    read per layer -- and the tag folds the layer's NAME in, because two pyramids that
    happen to agree on every recorded number would otherwise share a cache key and serve
    each other's ``immutable`` tiles.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    fake_pyramid(local, max_z=1)
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 5}}}), encoding="utf-8"
    )
    # Deliberately the SAME recorded numbers as the artwork, and a different depth for the
    # third, so both halves of the claim are exercised at once.
    same = {"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 5}}}
    _fake_layer(local, "terrain", max_z=1, sidecar=same)
    _fake_layer(
        local,
        "satellite",
        max_z=3,
        sidecar={
            "x_min_m": -100.0,
            "_meta": {"tiles": {"tile_px": 256, "max_z": 3, "count": 85}},
        },
    )

    heads = {layer: client.head(f"/api/maptiles/{layer}/0/0/0") for layer in LEGACY_LAYERS}
    assert [
        heads[layer].headers["x-map-tile-max-z"] for layer in ("map", "terrain", "satellite")
    ] == ["1", "1", "3"]
    # The artwork stops at z1 while the satellite goes to z3, from the same request shape.
    assert client.get("/api/maptiles/map/2/0/0").status_code == 404
    assert client.get("/api/maptiles/satellite/3/7/7").status_code == 200

    tags = {layer: head.headers["x-map-build"] for layer, head in heads.items()}
    assert len(set(tags.values())) == 3, "identical sidecars must still not share a cache tag"
    assert tags["map"] != tags["terrain"], "and the name is what separates these two"

    # Corners are the layer's own as well: the satellite's sidecar moves its western edge
    # and the other two stay where the default puts them.
    assert heads["satellite"].headers["x-map-bounds-m"].startswith("-100.0,")
    assert heads["map"].headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"
    assert heads["terrain"].headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"

    # And the tag still moves when that layer's pyramid does, which is what makes a
    # ``?v=``-tagged tile safe to cache forever.
    versioned = client.get(f"/api/maptiles/terrain/1/1/1?v={tags['terrain']}")
    assert "immutable" in versioned.headers["cache-control"]
    etag = heads["terrain"].headers["etag"]
    assert (
        client.get("/api/maptiles/terrain/0/0/0", headers={"If-None-Match": etag}).status_code
        == 304
    )
    (
        local / web_tiles.MAP_RENDERS_DIR_NAME / "terrain" / web_tiles.MAP_RENDER_SIDECAR_NAME
    ).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 6}}}), encoding="utf-8"
    )
    assert client.head("/api/maptiles/terrain/0/0/0").headers["x-map-build"] != tags["terrain"]
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] == tags["map"]


#: A tile out of the @2x tree, distinguishable from the 1x one by its bytes rather than by
#: its size -- what is under test is which DIRECTORY a request reached, and a fixture whose
#: two trees held the same bytes could not tell.
_PNG_DENSE = PNG_BYTES[:-4] + b"AT2X"


def test_a_hi_dpi_client_asks_for_the_same_tile_and_gets_twice_the_pixels(
    client, tmp_path, monkeypatch
):
    """``?px=512`` is one directory hop, and everything else about the request is unchanged.

    The @2x tree is the identical tile GRID -- level z is still 2**z tiles a side over the
    identical squares of the world -- at 512 px a tile instead of 256. So the whole of the
    serving design is that one query parameter picks a directory: same route, same
    coordinates, same corners, same cache tag.

    Three things have to hold, and each of them is a way the feature could be quietly wrong.
    The probe has to advertise BOTH depths, because a client builds its layer from that one
    response and cannot ask for a tree it has not been told about. A layer with no @2x tree
    has to serve the 1x tile rather than a 404, or asking for density on the wrong layer takes
    the base map down. And the @2x tree's own depth has to be the one enforced, since it is one
    level shallower and a request past its top must be refused against ITS grid rather than the
    1x one's.

    The @2x-less layer here is the ARTWORK, and it is now a pyramid cut before that tree
    existed or with ``--no-tiles-2x`` rather than one no tool can write -- ``gen_map_image.py``
    cuts both trees today. The case it stands for is the one that outlives the tool: a
    directory on somebody's disk from an older run.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / registry.local_dir().name
    local.mkdir()
    fake_pyramid(local, max_z=2)  # an artwork pyramid with no @2x tree beside it
    directory = local / web_tiles.MAP_RENDERS_DIR_NAME / "terrain"
    _fake_layer(
        local,
        "terrain",
        max_z=2,
        sidecar={
            "_meta": {
                "tiles": {"tile_px": 256, "max_z": 2, "count": 21},
                "tiles_2x": {"tile_px": 512, "max_z": 1, "count": 5},
            }
        },
        payload=_PNG_TERRAIN,
    )
    for z in range(2):
        for x in range(1 << z):
            for y in range(1 << z):
                path = directory / TILES_2X_DIR_NAME / str(z) / f"{x}_{y}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_PNG_DENSE)

    head = client.head("/api/maptiles/terrain/0/0/0")
    assert head.headers["x-map-tile-px"] == "256"
    assert head.headers["x-map-tile-max-z"] == "2"
    assert head.headers["x-map-tile-2x-px"] == "512"
    assert head.headers["x-map-tile-2x-max-z"] == "1"

    # The same coordinates, one parameter apart, reach the two trees.
    assert client.get("/api/maptiles/terrain/1/1/1").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/terrain/1/1/1?px=512").content == _PNG_DENSE
    # Corners and cache tag are untouched by the density: it is the same picture.
    dense = client.head("/api/maptiles/terrain/0/0/0?px=512")
    assert dense.headers["x-map-bounds-m"] == head.headers["x-map-bounds-m"]
    assert dense.headers["x-map-build"] == head.headers["x-map-build"]

    # The @2x tree's own depth is what a request is checked against, and it is shallower.
    assert client.get("/api/maptiles/terrain/2/3/3").status_code == 200
    off = client.get("/api/maptiles/terrain/2/3/3?px=512")
    assert off.status_code == 404 and "z0..z1" in off.json()["error"]

    # A size this layer has no tree for, and a layer with no dense tree at all, both answer
    # with the 1x tile -- which every client can draw at any density.
    assert client.get("/api/maptiles/terrain/1/1/1?px=1024").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/terrain/1/1/1?px=nonsense").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/map/1/1/1?px=512").content == PNG_BYTES
    assert "x-map-tile-2x-px" not in client.head("/api/maptiles/map/0/0/0").headers
