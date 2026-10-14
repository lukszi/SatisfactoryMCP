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

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from .....core.gameassets.pyramid import (
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    tile_relpath,
)
from .....core.gameassets.versions import RETIRED_STYLES
from .....domain.maps import registry
from .....domain.maps.pyramids import (
    COLOUR_SUFFIXES,
    DEFAULT_MAP_BOUNDS_M,
    LIGHT_KINDS,
    MAP_LAYER_DEFAULT,
    MAP_RENDER_SIDECAR_NAME,
    MAP_RENDERS_DIR_NAME,
    MAP_TILE_MAX_Z,
    Pyramid,
)
from .....domain.maps.pyramids import layer_dir as _layer_dir
from .....domain.maps.pyramids import light as _light
from .....domain.maps.pyramids import map_bounds as _map_bounds
from .....domain.maps.pyramids import map_pyramid as _map_pyramid
from .....domain.maps.pyramids import pyramid_headers as _pyramid_headers
from ...serial import IMMUTABLE, cached_file, error_response

__all__ = [
    "DEFAULT_MAP_BOUNDS_M",
    "MAP_RENDERS_DIR_NAME",
    "MAP_RENDER_SIDECAR_NAME",
    "router",
]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


#: A user-supplied map render, and the sidecar that may pin its corners, under
#: ``data/local``. Nothing there is ever committed.
MAP_IMAGE_NAME = "map.png"
MAP_BOUNDS_NAME = "map.json"

#: The query parameter that picks the tile density, as the tile size in pixels the client
#: wants, so a third density is one more value and not one more spelling.
MAP_TILE_PX_PARAM = "px"


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
#: colour: the unlit colour (the ground, on a layer drawn apart at its trees), the trees, or
#: the light pyramid's normals or horizons. docs/maps_contract.md section 8.1.
MAP_TILE_KIND_PARAM = "kind"


def _light_tile(request: Request, layer: str, z: int, x: int, y: int) -> Response:
    """One tile of a lit layer's ``unlit``, ``trees``, ``nrm`` or ``hz`` tree; ``kind`` picks
    which. The trees are sparse: a tile on their grid that is not there has no tree in it, a
    204 to GET and HEAD alike, cached as the tiles are."""
    kind = request.query_params.get(MAP_TILE_KIND_PARAM, "")
    light = _light(layer)
    if kind not in LIGHT_KINDS or light is None:
        return error_response(
            f"no {kind!r} tiles for {layer}: kind is one of {', '.join(LIGHT_KINDS)}, on a "
            "layer drawn with --unlit",
            404,
        )
    trees = light["trees"]
    if kind == "trees" and trees is None:
        return error_response(
            f"no trees tiles for {layer}: it was drawn before its trees came apart from the "
            "ground, or draws none; render it again to have them",
            404,
        )
    depth = {"unlit": light["unlit_max_z"], "trees": light["trees_max_z"]}.get(kind, light["max_z"])
    span = 1 << z
    if not (0 <= z <= depth and 0 <= x < span and 0 <= y < span):
        return error_response(
            f"no {kind} tile {layer}/{z}/{x}/{y}: that tree runs z0..z{depth}", 404
        )
    stem = tile_relpath(z, x, y)[: -len(".png")]
    build = light["header"]["build"]
    tree = {"unlit": light["unlit"], "trees": trees}.get(kind) or light["root"] / TILES_DIR_NAME
    suffix = light["suffix"].get(kind, LIGHT_KINDS[kind])
    path = tree / (stem + suffix)
    if not path.is_file():
        if kind == "trees":
            return _no_trees(request, build)
        if request.method == "HEAD":
            return Response(status_code=204)
        return error_response(f"no {kind} tile {layer}/{z}/{x}/{y}: {path} is not there", 404)
    media = COLOUR_SUFFIXES.get(suffix, "image/webp")
    return cached_file(request, path, build, media_type=media)


def _no_trees(request: Request, build: str) -> Response:
    """A tile of a sparse tree with nothing in it: 204, behind ``?v=`` immutable like a tile."""
    cache = IMMUTABLE if "v" in request.query_params else "no-cache"
    return Response(status_code=204, headers={"Cache-Control": cache, "ETag": f'"{build}"'})


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
    **{
        style["layer"]: "an earlier tools/gen_map_renders.py; nothing draws this style now"
        for style in RETIRED_STYLES.values()
    },
}


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
    separately and by different tools -- a regenerated painted layer must not invalidate the
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
