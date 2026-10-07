"""The base map: a user-supplied render, and the tile pyramids cut from it.

Everything here is a LOADER. Nothing in this repository ships a picture of this world, so
every route answers either "here is the file you generated" or "here is the exact tool that
would write it" -- which is why the 404s are long: they are the whole of the documentation a
reader gets at the moment they need it.

Many layers, one grid. A layer is a map type in the registry (``domain.maps.registry``):
``map`` is the game's own artwork under ``local/tiles/``, ``terrain`` and ``satellite`` the
renders under ``local/renders/<layer>/``, and every generated type has an id of its own.
Every one is cut on the same frame at the same tile size into the same ``{z}/{x}_{y}.png``,
so switching layers is switching a directory. The layout is ``core.gameassets.pyramid``'s;
what this side owns is the bounds check, because only a server has requests to refuse.

Handler names are operation_ids; wire rules: docs/web-wire.md. Three routes carry explicit
ids; see ``OPERATION_MAPIMAGE``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from typing_extensions import TypedDict

from .....core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    tile_relpath,
)
from .....core.jsontypes import JsonObject, JsonValue
from .....domain.maps import registry
from .....domain.spatial import geo
from ...serial import cached_file, error_response, json_object, sidecar_meta_block

__all__ = ["DEFAULT_MAP_BOUNDS_M", "router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


#: A user-supplied map render, and the sidecar that may pin its corners, under
#: ``data/local``. Nothing there is ever committed.
MAP_IMAGE_NAME = "map.png"
MAP_BOUNDS_NAME = "map.json"

#: The query parameter that picks the tile density, as the tile size in pixels the client
#: wants, so a third density is one more value and not one more spelling.
MAP_TILE_PX_PARAM = "px"

#: The type ``/api/maptiles/{z}/{x}/{y}`` serves: the game's own artwork under ``local/tiles/``.
#: Every other type is a registry id, each with its own sidecar naming its depth and build.
MAP_LAYER_DEFAULT = "map"
MAP_RENDERS_DIR_NAME = "renders"
MAP_RENDER_SIDECAR_NAME = "meta.json"

#: A pyramid's depth when its sidecar does not say: z0 (the world in one tile) through z5
#: (the full 8192 px sheet in 32x32).
MAP_TILE_MAX_Z = 5

#: The corners of the in-game map square, metres, game axes. The playable content is strictly
#: inside it -- ``geo.CONTENT_BBOX`` is x [-2988.4, 4065.6], y [-3141.0, 3042.0] -- so an
#: image pinned here cannot clip anything the map draws. Also the frame the map-area raster
#: is pinned on.
_X0, _Y0, _X1, _Y1 = geo.MAP_SQUARE_M
DEFAULT_MAP_BOUNDS_M = {"x_min_m": _X0, "x_max_m": _X1, "y_min_m": _Y0, "y_max_m": _Y1}


def _layer_dir(layer: str) -> Path | None:
    """Where one ready type's ``tiles/`` tree and sidecar live, or ``None``."""
    return registry.directory(layer)


def _layer_sidecar(layer: str) -> Path | None:
    """The JSON beside one type's pyramid, named by its registry entry: ``map.json`` for the
    artwork, whose corners a reader may have written by hand, and ``meta.json`` for a render."""
    entry, directory = registry.lookup(layer)
    if entry is None or directory is None:
        return None
    return directory / entry["sidecar"]


def _map_bounds(layer: str = MAP_LAYER_DEFAULT) -> dict[str, float]:
    """Where to pin a layer, defaults overridden by its own sidecar if present.

    A malformed override is ignored rather than fatal: the picture is decoration, and a typo
    in an optional sidecar must not take down the endpoint that serves it. Per layer, because
    the corners are a property of a picture and a reader who drops in their own ``map.png``
    pinned somewhere else must not thereby move the renders.
    """
    bounds = dict(DEFAULT_MAP_BOUNDS_M)
    path = _layer_sidecar(layer)
    if path is None:
        return bounds
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
        override = json_object(raw)
        bounds.update({k: _number(override[k]) for k in DEFAULT_MAP_BOUNDS_M if k in override})
    except (OSError, ValueError, TypeError):
        return bounds
    return bounds


def _number(value: JsonValue) -> float:
    """``float(value)``, with its ``TypeError`` for a list, an object or a null."""
    if isinstance(value, (str, int, float)):
        return float(value)
    raise TypeError(f"not a number: {value!r}")


def _whole(source: JsonObject, key: str, default: int, floor: int) -> int:
    """An integer sidecar field at or above ``floor``, else ``default``."""
    value = source.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value >= floor:
        return value
    return default


class Pyramid(TypedDict):
    """A layer's tile pyramid as its sidecar describes it; the @2x pair is null without one."""

    tile_px: int
    max_z: int
    tile_2x_px: int | None
    max_2x_z: int | None
    build: str


def _map_pyramid(layer: str = MAP_LAYER_DEFAULT) -> Pyramid:
    """What a layer's sidecar says about its pyramid: tile size, depth, build.

    An absent or malformed sidecar describes a default pyramid, and ``build`` is only a cache
    tag, so saying nothing still yields a stable tag. The layer's name and both trees' numbers
    are in its digest: a tag shared between two pictures, or two cuts of one, is how an
    ``immutable`` tile of one gets cached as a tile of the other.
    """
    block = sidecar_meta_block(_layer_sidecar(layer))
    tiles = json_object(block.get("tiles"))
    found = block.get("tiles_2x")
    dense = found if isinstance(found, dict) else None
    stamp = "|".join(
        [
            layer,
            *(str(tiles.get(key)) for key in ("game_version_pinned", "count", "bytes", "max_z")),
            *(str((dense or {}).get(key)) for key in ("count", "bytes", "max_z")),
        ]
    )
    return {
        "tile_px": _whole(tiles, "tile_px", PYRAMID_TILE_PX, 1),
        "max_z": _whole(tiles, "max_z", MAP_TILE_MAX_Z, 0),
        "tile_2x_px": _whole(dense, "tile_px", PYRAMID_TILE_2X_PX, 1) if dense else None,
        "max_2x_z": _whole(dense, "max_z", 0, 0) if dense else None,
        "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
    }


def map_tile_path(
    z: int,
    x: int,
    y: int,
    max_z: int = MAP_TILE_MAX_Z,
    layer: str = MAP_LAYER_DEFAULT,
    tree: str = TILES_DIR_NAME,
) -> Path | None:
    """Where one pyramid tile lives, or ``None`` if ``(z, x, y)`` is off the pyramid.

    **Nothing here joins a string a caller supplied.** The three coordinates arrive as ints
    -- FastAPI answers anything else with a 422 before this runs -- and are range-checked
    against the ``2**z`` grid of their own level before they become a filename. ``layer`` is
    the one segment that IS a string, and it never reaches a path: it is a key into the
    registry, whose ``dir`` only the server writes and which is checked to resolve inside
    ``data/local``, so a layer segment shaped like an escape is an unknown layer. ``tree`` is
    chosen by ``_tile_tree`` from the two tree names and is never a request's string.
    """
    directory = _layer_dir(layer)
    if directory is None or tree not in (TILES_DIR_NAME, TILES_2X_DIR_NAME):
        return None
    if not 0 <= z <= max_z:
        return None
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        return None
    return directory / tree / tile_relpath(z, x, y)


#: The query parameter that asks a lit layer for one of its lighting trees instead of its
#: colour: the unlit colour, or the light pyramid's normals or horizons. docs/maps_contract.md
#: section 8.1.
MAP_TILE_KIND_PARAM = "kind"
LIGHT_KINDS = {"unlit": ".png", "nrm": ".nrm.webp", "hz": ".hz.webp"}


class LightHeader(TypedDict):
    """``X-Map-Light``: what the page needs to relight a layer live."""

    build: str
    max_z: int
    unlit_max_z: int
    params: JsonValue
    baked_sun: JsonValue
    model: JsonObject


class Light(TypedDict):
    """A lit layer's two trees, their depths, and the header that describes them."""

    root: Path
    unlit: Path
    max_z: int
    unlit_max_z: int
    header: LightHeader


def _light(layer: str) -> Light | None:
    """A lit layer's lighting: its unlit tree, the light pyramid, the shader's numbers.

    ``None`` for a layer drawn lit. The light pyramid's folder comes from the sidecar and is
    refused unless it resolves inside ``data/local``, like a registry ``dir``.
    """
    directory = _layer_dir(layer)
    block = sidecar_meta_block(_layer_sidecar(layer)).get("light")
    if directory is None or not isinstance(block, dict):
        return None
    rel = block.get("dir")
    if not isinstance(rel, str):
        return None
    root = (directory / rel).resolve()
    if not root.is_relative_to(registry.local_dir().resolve()):
        return None
    meta = sidecar_meta_block(root / MAP_RENDER_SIDECAR_NAME)
    tiles = json_object(meta.get("tiles"))
    unlit = json_object(block.get("unlit_tiles"))
    model = json_object(meta.get("light"))
    max_z, unlit_max_z = tiles.get("max_z"), unlit.get("max_z")
    if not isinstance(max_z, int) or not isinstance(unlit_max_z, int):
        return None
    stamp = "|".join(
        [
            layer,
            str(model.get("digest")),
            *(str(tiles.get(key)) for key in ("count", "bytes")),
            str(unlit.get("bytes")),
        ]
    )
    return {
        "root": root,
        "unlit": directory / str(block.get("unlit_dir") or "unlit"),
        "max_z": max_z,
        "unlit_max_z": unlit_max_z,
        "header": {
            "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
            "max_z": max_z,
            "unlit_max_z": unlit_max_z,
            "params": block.get("params"),
            "baked_sun": block.get("baked_sun"),
            "model": {k: v for k, v in model.items() if k not in ("label", "digest")},
        },
    }


def _light_tile(request: Request, layer: str, z: int, x: int, y: int) -> Response:
    """One tile of a lit layer's ``unlit``, ``nrm`` or ``hz`` tree; ``kind`` picks which."""
    kind = request.query_params.get(MAP_TILE_KIND_PARAM, "")
    light = _light(layer)
    if kind not in LIGHT_KINDS or light is None:
        return error_response(
            f"no {kind!r} tiles for {layer}: kind is one of {', '.join(LIGHT_KINDS)}, on a "
            "layer drawn with --unlit",
            404,
        )
    depth = light["unlit_max_z"] if kind == "unlit" else light["max_z"]
    span = 1 << z
    if not (0 <= z <= depth and 0 <= x < span and 0 <= y < span):
        return error_response(
            f"no {kind} tile {layer}/{z}/{x}/{y}: that tree runs z0..z{depth}", 404
        )
    stem = tile_relpath(z, x, y)[: -len(".png")]
    tree = light["unlit"] if kind == "unlit" else light["root"] / TILES_DIR_NAME
    path = tree / (stem + LIGHT_KINDS[kind])
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        return error_response(f"no {kind} tile {layer}/{z}/{x}/{y}: {path} is not there", 404)
    media = "image/png" if kind == "unlit" else "image/webp"
    return cached_file(request, path, light["header"]["build"], media_type=media)


def _tile_tree(request: Request, pyramid: Pyramid) -> tuple[str, int]:
    """Which of a layer's two trees this request asked for, and how deep that one goes.

    Forgiving in one direction only: a client that asks for a density this layer has gets it,
    and one that asks for a density it has not gets the 1x tile, which every client can draw
    at any density. The @2x tree is one level shallower, since 512 px tiles run out of sheet
    a level before 256 px ones do.
    """
    if pyramid["max_2x_z"] is None:
        return TILES_DIR_NAME, pyramid["max_z"]
    asked = request.query_params.get(MAP_TILE_PX_PARAM)
    if asked is not None and asked.isdigit() and int(asked) == pyramid["tile_2x_px"]:
        return TILES_2X_DIR_NAME, pyramid["max_2x_z"]
    return TILES_DIR_NAME, pyramid["max_z"]


# ------------------------------------------------------------------- mapimage


#: Explicit, on the three routes that serve GET and HEAD from one handler: FastAPI walks
#: ``route.methods``, which is a SET, so an implicit id is ``..._get`` or ``..._head`` at
#: random per interpreter run and the committed schema grows a diff that is nothing of the
#: kind. Naming them fixes the id to one string that is true of both methods. Their
#: ``response_model`` is ``object``, the unconstrained body a picture route has always published.
OPERATION_MAPIMAGE = "mapimage"
OPERATION_MAPTILES = "maptiles"
OPERATION_MAPTILES_LAYER = "maptiles_layer"


@router.api_route(
    "/mapimage", methods=["GET", "HEAD"], operation_id=OPERATION_MAPIMAGE, response_model=object
)
def mapimage(request: Request) -> Response:
    """A map render the *user* dropped in, if they dropped one in. Never shipped.

    HEAD is routed alongside GET because the page probes with HEAD before it builds an
    ``imageOverlay``, and FastAPI -- unlike bare Starlette -- does not add HEAD to a GET
    route by itself, so a probe would come back 405 and read as "no image".

    An absent file is the *expected* state, so the HEAD probe answers **204**, not 404: a 404
    on every clean page load trains the reader to ignore console errors. The GET keeps its
    404 with the where-to-put-it message.

    The corners travel with the file in ``X-Map-Bounds-M`` (``x_min,y_min,x_max,y_max``,
    metres, game axes) so the one probe the page already makes answers both questions.
    """
    path = registry.local_dir() / MAP_IMAGE_NAME
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        return error_response(
            f"no map image: put a map render at {path}; it is only ever read locally, "
            "never uploaded and never committed. Optionally pin its corners with "
            f"{path.with_name(MAP_BOUNDS_NAME)} "
            '{"x_min_m":…,"x_max_m":…,"y_min_m":…,"y_max_m":…}',
            404,
        )
    bounds = _map_bounds()
    return FileResponse(
        path,
        headers={
            "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**bounds),
            "Cache-Control": "no-cache",
        },
    )


# ------------------------------------------------------------------- maptiles


#: Which tool writes which painter's layers, so an absent pyramid can say what would fill it.
_LAYER_TOOLS = {
    MAP_LAYER_DEFAULT: (
        "tools/gen_map_image.py, which cuts it out of your own installed game beside map.png"
    ),
    "terrain": (
        "tools/gen_map_renders.py, which draws a hypsometric relief map of this world from "
        "the 1 m heightfield in data/local/heightmap/"
    ),
    "satellite": (
        "tools/gen_map_renders.py, which draws the same relief coloured from the game's own "
        "biome raster, from the 1 m heightfield in data/local/heightmap/"
    ),
}


def _pyramid_headers(layer: str, pyramid: Pyramid) -> dict[str, str]:
    """The layer's own corners, grid and build, so the page configures its tile layer from
    the probe it already makes; the @2x pair is absent, not zero, without a denser tree."""
    bounds = _map_bounds(layer)
    headers = {
        "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**bounds),
        "X-Map-Layer": layer,
        "X-Map-Tile-Px": str(pyramid["tile_px"]),
        "X-Map-Tile-Max-Z": str(pyramid["max_z"]),
        "X-Map-Build": pyramid["build"],
    }
    if pyramid["max_2x_z"] is not None:
        headers["X-Map-Tile-2x-Px"] = str(pyramid["tile_2x_px"])
        headers["X-Map-Tile-2x-Max-Z"] = str(pyramid["max_2x_z"])
    return headers


def _serve_tile(request: Request, layer: str, z: int, x: int, y: int) -> Response:
    """One tile of one layer's pyramid. The whole of what both tile routes do.

    **HEAD 204 for an absent pyramid, like the image probe next door.** The page probes
    ``0/0/0`` to decide between the pyramid and the single overlay, and to decide which
    layers exist at all; an absent optional file is the ordinary answer. GET keeps its 404
    and names the tool that would write that particular tree.

    **Off the pyramid is 404.** ``z``, ``x`` and ``y`` are typed ``int``, so a segment that
    is not one never reaches this function, and ``map_tile_path`` range-checks the three
    against the level's own grid before building a name.

    **Every header is that layer's own.** Depth, tile size, corners and build tag are read
    from the sidecar beside the tiles being served, because the layers are generated
    separately and by different tools -- a regenerated satellite must not invalidate the
    terrain a browser is holding.

    **Cached hard, and stamped with the build.** A tile is immutable for a given cut, so the
    page asks for it with ``?v=`` the build tag this endpoint hands out on the probe; the
    ETag carries the same tag for anything that revalidates instead.

    **``?px=`` picks the density.** A hi-DPI client asks for the same ``{z}/{x}/{y}`` and
    names the tile size it wants. The @2x tree is one level shallower, which is why the depth
    in the headers is the depth of the tree actually being served and both are advertised.
    """
    if MAP_TILE_KIND_PARAM in request.query_params:
        return _light_tile(request, layer, z, x, y)
    pyramid = _map_pyramid(layer)
    tree, depth = _tile_tree(request, pyramid)
    path = map_tile_path(z, x, y, depth, layer, tree)
    if path is None:
        return error_response(
            f"no tile {layer}/{z}/{x}/{y}: this pyramid runs z0..z{depth}, and level z is a "
            "2**z by 2**z grid, so x and y stop there",
            404,
        )
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        entry, _where = registry.lookup(layer)
        painter = entry.get("layer", layer) if entry else layer
        tool = _LAYER_TOOLS.get(painter, "the Maps tab of the Settings page")
        return error_response(
            f"no {layer} tiles: {path.parent.parent} is written by {tool}. "
            "Like the map image, it is only ever read locally, never uploaded and never "
            "committed.",
            404,
        )
    headers = _pyramid_headers(layer, pyramid)
    light = _light(layer) if z == 0 else None
    if light is not None:
        # On the z0 probe only: what the page needs to relight this layer live.
        headers["X-Map-Light"] = json.dumps(light["header"], separators=(",", ":"))
    return cached_file(request, path, pyramid["build"], headers=headers)


@router.api_route(
    "/maptiles/{z}/{x}/{y}",
    methods=["GET", "HEAD"],
    operation_id=OPERATION_MAPTILES,
    response_model=object,
)
def maptiles(request: Request, z: int, x: int, y: int) -> Response:
    """The artwork pyramid, at the URL it has always had. An alias for ``map``.

    Not a redirect and not a deprecation: the default layer's name is optional, and this
    answers byte for byte and header for header what ``/api/maptiles/map/{z}/{x}/{y}`` does.
    """
    return _serve_tile(request, MAP_LAYER_DEFAULT, z, x, y)


@router.api_route(
    "/maptiles/{layer}/{z}/{x}/{y}",
    methods=["GET", "HEAD"],
    operation_id=OPERATION_MAPTILES_LAYER,
    response_model=object,
)
def maptiles_layer(request: Request, layer: str, z: int, x: int, y: int) -> Response:
    """One tile of a named base layer: a map type id from ``/api/maps``.

    Four segments where the alias above has three, so the two routes cannot collide.

    An unknown layer is a 404 that lists the ones there are, rather than a 422 about a path
    parameter: a page probing for layers it might find deserves to be told which names exist.
    A type still being generated, or one whose job failed, is not served: HEAD says 204 and
    GET says which.
    """
    entry, _where = registry.lookup(layer)
    if entry is None:
        return error_response(
            f"no base layer {layer!r}: this server serves {', '.join(registry.known_ids())}. "
            f"{MAP_LAYER_DEFAULT} is the game's own artwork; the rest are listed by /api/maps.",
            404,
        )
    if entry.get("status") != "ready":
        if request.method == "HEAD":
            return Response(status_code=204)
        return error_response(f"{layer} is {entry.get('status')}, not ready to be served", 404)
    return _serve_tile(request, layer, z, x, y)
