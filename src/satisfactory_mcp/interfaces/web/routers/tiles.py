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

WARNING: the function names are the operation_ids -- renaming one churns the committed
schema. These three routes carry EXPLICIT ids; see ``OPERATION_MAPIMAGE``.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from .... import config
from ....core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    tile_relpath,
)
from ....domain.maps import registry
from ....domain.spatial import geo
from ..serial import error_response

__all__ = ["DEFAULT_MAP_BOUNDS_M", "router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


#: Where a user-supplied map render goes, and nothing here is ever committed. Drop your own
#: render at ``data/local/map.png`` and, if its corners are not the standard in-game map
#: square, ``data/local/map.json`` next to it.
LOCAL_DIR_NAME = "local"
MAP_IMAGE_NAME = "map.png"
MAP_BOUNDS_NAME = "map.json"

#: And where the same render's tile pyramid goes, if the generator cut one. One 8192 px
#: sheet is 16 MB on the wire and 268 MB of RGBA in the browser however far out the view is
#: zoomed; the pyramid is that sheet at one resolution per zoom, so a whole-world framing
#: costs the 16 tiles of z2 and nothing else. ``tools/gen_map_image.py`` writes it, renaming
#: the finished tree into place so this endpoint can never serve half of one.
#:
#: Imported from the cutter rather than typed again, here and for the three below: the
#: layout of that directory is ONE fact, and the ``MAP_`` aliases are only a prefix this
#: module reads better with.
MAP_TILES_DIR_NAME = TILES_DIR_NAME

#: ...and the same tile GRID at twice the pixels, for a display whose device pixel ratio is
#: above one. Level z of ``tiles@2x/`` covers the identical squares of the world that level z
#: of ``tiles/`` does, at 512 px instead of 256, so a client asks for the same
#: ``{z}/{x}/{y}`` and draws twice the pixels into the same CSS box.
#:
#: One level shallower than the 1x tree by arithmetic, since ``512 * 2**z`` runs out of sheet
#: before ``256 * 2**z`` does -- so the probe advertises the two depths separately and a
#: client past the @2x top asks for 1x tiles again.
MAP_TILES_2X_DIR_NAME = TILES_2X_DIR_NAME
MAP_TILE_2X_PX = PYRAMID_TILE_2X_PX

#: The query parameter that picks between them, and it takes the tile size the client wants
#: in pixels rather than a flag, so a third density is one more value and not one more
#: spelling. A density this server has no tree for falls back to the 1x tile.
MAP_TILE_PX_PARAM = "px"

#: The type ``/api/maptiles/{z}/{x}/{y}`` serves: the game's own artwork under ``local/tiles/``.
#: Every other type is a registry id, each with its own sidecar naming its depth and build.
MAP_LAYER_DEFAULT = "map"
MAP_RENDERS_DIR_NAME = "renders"
MAP_RENDER_SIDECAR_NAME = "meta.json"

#: What a pyramid looks like when the sidecar does not say: 256 px tiles, z0 (the world in
#: one tile) through z5 (the full 8192 in 32x32). Both are read back from ``_meta.tiles``
#: when it is there, so a pyramid cut at another size is served at that size rather than
#: half-refused.
MAP_TILE_PX = PYRAMID_TILE_PX
MAP_TILE_MAX_Z = 5

#: The corners of the in-game map square, metres, game axes. The playable content is strictly
#: inside it -- ``geo.CONTENT_BBOX`` is x [-2988.4, 4065.6], y [-3141.0, 3042.0] -- so an
#: image pinned here cannot clip anything the map draws. Also the frame the map-area raster
#: is pinned on.
_X0, _Y0, _X1, _Y1 = geo.MAP_SQUARE_M
DEFAULT_MAP_BOUNDS_M = {"x_min_m": _X0, "x_max_m": _X1, "y_min_m": _Y0, "y_max_m": _Y1}


def _local_dir() -> Path:
    """The user's own files, read at call time so a test can point it somewhere else."""
    return config.data_dir() / LOCAL_DIR_NAME


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
        override = json.loads(path.read_text(encoding="utf-8"))
        bounds.update({k: float(override[k]) for k in DEFAULT_MAP_BOUNDS_M if k in override})
    except (OSError, ValueError, TypeError):
        return bounds
    return bounds


def _map_pyramid(layer: str = MAP_LAYER_DEFAULT) -> dict[str, Any]:
    """What a layer's sidecar says about its pyramid: tile size, depth, build.

    Same posture as ``_map_bounds``: an absent or malformed sidecar is not an error but a
    pyramid described by the defaults above. ``build`` is only ever a cache tag, so a sidecar
    that says nothing produces a stable tag for "nothing".

    The layer's NAME is folded into that digest, so two layers cut from one build at one tile
    count still get different tags -- sharing a tag between two pictures is how an
    ``immutable`` tile of one ends up cached as a tile of the other.
    """
    path = _layer_sidecar(layer)
    meta: Any = {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    except (OSError, ValueError):
        meta = {}
    block: Any = meta.get("_meta") if isinstance(meta, dict) else None
    block = block if isinstance(block, dict) else {}
    tiles = block.get("tiles") if isinstance(block.get("tiles"), dict) else {}
    dense = block.get("tiles_2x") if isinstance(block.get("tiles_2x"), dict) else None

    def _whole(source: dict, key: str, default: int, floor: int) -> int:
        value = source.get(key)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= floor
            else default
        )

    # The @2x tree's own numbers ride in the SAME digest, so recutting one and not the other
    # still changes every URL of that layer: two trees of one picture that disagree about
    # which build they came from is the state an ``immutable`` tile must not be served in.
    stamp = "|".join(
        [
            layer,
            *(str(tiles.get(key)) for key in ("game_version_pinned", "count", "bytes", "max_z")),
            *(str((dense or {}).get(key)) for key in ("count", "bytes", "max_z")),
        ]
    )
    return {
        "tile_px": _whole(tiles, "tile_px", MAP_TILE_PX, 1),
        "max_z": _whole(tiles, "max_z", MAP_TILE_MAX_Z, 0),
        "tile_2x_px": _whole(dense, "tile_px", MAP_TILE_2X_PX, 1) if dense else None,
        "max_2x_z": _whole(dense, "max_z", 0, 0) if dense else None,
        "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
    }


def map_tile_path(
    z: int,
    x: int,
    y: int,
    max_z: int = MAP_TILE_MAX_Z,
    layer: str = MAP_LAYER_DEFAULT,
    tree: str = MAP_TILES_DIR_NAME,
) -> Path | None:
    """Where one pyramid tile lives, or ``None`` if ``(z, x, y)`` is off the pyramid.

    **Nothing here joins a string a caller supplied.** The three coordinates arrive as ints
    -- FastAPI answers anything else with a 422 before this runs -- and are range-checked
    against the ``2**z`` grid of their own level before they become a filename. ``layer`` is
    the one segment that IS a string, and it never reaches a path: it is a key into the
    registry, whose ``dir`` only the server writes and which is checked to resolve inside
    ``data/local``, so a layer segment shaped like an escape is an unknown layer. ``tree`` is
    chosen by ``_tile_tree`` from the two names above and is never a request's string.
    """
    directory = _layer_dir(layer)
    if directory is None or tree not in (MAP_TILES_DIR_NAME, MAP_TILES_2X_DIR_NAME):
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


def _sidecar_meta(path: Path | None) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    except (OSError, ValueError):
        return {}
    block = raw.get("_meta") if isinstance(raw, dict) else None
    return block if isinstance(block, dict) else {}


def _light(layer: str) -> dict[str, Any] | None:
    """A lit layer's lighting: its unlit tree, the light pyramid, the shader's numbers.

    ``None`` for a layer drawn lit. The light pyramid's folder comes from the sidecar and is
    refused unless it resolves inside ``data/local``, like a registry ``dir``.
    """
    directory = _layer_dir(layer)
    block = _sidecar_meta(_layer_sidecar(layer)).get("light")
    if directory is None or not isinstance(block, dict) or not isinstance(block.get("dir"), str):
        return None
    root = (directory / block["dir"]).resolve()
    if not root.is_relative_to(_local_dir().resolve()):
        return None
    meta = _sidecar_meta(root / MAP_RENDER_SIDECAR_NAME)
    tiles = meta.get("tiles") if isinstance(meta.get("tiles"), dict) else {}
    unlit = block.get("unlit_tiles") if isinstance(block.get("unlit_tiles"), dict) else {}
    model = meta.get("light") if isinstance(meta.get("light"), dict) else {}
    if not isinstance(tiles.get("max_z"), int) or not isinstance(unlit.get("max_z"), int):
        return None
    stamp = "|".join([layer, str(model.get("digest")), *(str(tiles.get(k)) for k in ("count", "bytes")),
                      str(unlit.get("bytes"))])  # fmt: skip
    return {
        "root": root,
        "unlit": directory / str(block.get("unlit_dir") or "unlit"),
        "max_z": tiles["max_z"],
        "unlit_max_z": unlit["max_z"],
        "header": {
            "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
            "max_z": tiles["max_z"],
            "unlit_max_z": unlit["max_z"],
            "params": block.get("params"),
            "baked_sun": block.get("baked_sun"),
            "model": {k: v for k, v in model.items() if k not in ("label", "digest")},
        },
    }


def _light_tile(request: Request, layer: str, z: int, x: int, y: int) -> Any:
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
    tree = light["unlit"] if kind == "unlit" else light["root"] / MAP_TILES_DIR_NAME
    path = tree / (stem + LIGHT_KINDS[kind])
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        return error_response(f"no {kind} tile {layer}/{z}/{x}/{y}: {path} is not there", 404)
    etag = f'"{light["header"]["build"]}"'
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable"
        if "v" in request.query_params
        else "no-cache",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    media = "image/png" if kind == "unlit" else "image/webp"
    return FileResponse(path, media_type=media, headers=headers)


def _tile_tree(request: Request, pyramid: dict[str, Any]) -> tuple[str, int]:
    """Which of a layer's two trees this request asked for, and how deep that one goes.

    Forgiving in one direction only: a client that asks for a density this layer has gets it,
    and one that asks for a density it has not gets the 1x tile, which every client can draw
    at any density. A client that asks for nothing wants 256 and there is no fallback to make.
    """
    if pyramid["max_2x_z"] is None:
        return MAP_TILES_DIR_NAME, pyramid["max_z"]
    asked = request.query_params.get(MAP_TILE_PX_PARAM)
    if asked is not None and asked.isdigit() and int(asked) == pyramid["tile_2x_px"]:
        return MAP_TILES_2X_DIR_NAME, pyramid["max_2x_z"]
    return MAP_TILES_DIR_NAME, pyramid["max_z"]


# ------------------------------------------------------------------- mapimage


#: Explicit, on the three routes that serve GET and HEAD from one handler: FastAPI walks
#: ``route.methods``, which is a SET, so an implicit id is ``..._get`` or ``..._head`` at
#: random per interpreter run and the committed schema grows a diff that is nothing of the
#: kind. Naming them fixes the id to one string that is true of both methods.
OPERATION_MAPIMAGE = "mapimage"
OPERATION_MAPTILES = "maptiles"
OPERATION_MAPTILES_LAYER = "maptiles_layer"


@router.api_route("/mapimage", methods=["GET", "HEAD"], operation_id=OPERATION_MAPIMAGE)
def mapimage(request: Request) -> Any:
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
    path = _local_dir() / MAP_IMAGE_NAME
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
    b = _map_bounds()
    return FileResponse(
        path,
        headers={
            "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**b),
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


def _serve_tile(request: Request, layer: str, z: int, x: int, y: int) -> Any:
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
        painter = (entry or {}).get("layer", layer)
        tool = _LAYER_TOOLS.get(painter, "the Maps tab of the Settings page")
        return error_response(
            f"no {layer} tiles: {path.parent.parent} is written by {tool}. "
            "Like the map image, it is only ever read locally, never uploaded and never "
            "committed.",
            404,
        )
    b = _map_bounds(layer)
    etag = f'"{pyramid["build"]}"'
    # ``immutable`` is earned by the ``?v=`` build tag and only by it: a tagged URL changes
    # whenever the pyramid is recut, so the response behind it never can. An UNTAGGED fetch
    # must revalidate -- caching those hard is how a regenerated map stays invisible behind a
    # year-old probe -- and the ETag makes that a 304 rather than bytes.
    versioned = "v" in request.query_params
    headers = {
        # The corners, the shape of the grid and the build, on the probe the page already
        # makes, so the client configures its tile layer from the server.
        "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**b),
        "X-Map-Layer": layer,
        "X-Map-Tile-Px": str(pyramid["tile_px"]),
        "X-Map-Tile-Max-Z": str(pyramid["max_z"]),
        "X-Map-Build": pyramid["build"],
        # The denser tree, when this layer has one, on the same probe: a client has to know
        # both that @2x tiles exist and how deep they go BEFORE it builds its layer. Absent,
        # not zero, when there is no such tree.
        **(
            {
                "X-Map-Tile-2x-Px": str(pyramid["tile_2x_px"]),
                "X-Map-Tile-2x-Max-Z": str(pyramid["max_2x_z"]),
            }
            if pyramid["max_2x_z"] is not None
            else {}
        ),
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
        "ETag": etag,
    }
    light = _light(layer) if z == 0 else None
    if light is not None:
        # On the z0 probe only: what the page needs to relight this layer live.
        headers["X-Map-Light"] = json.dumps(light["header"], separators=(",", ":"))
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, headers=headers)


@router.api_route("/maptiles/{z}/{x}/{y}", methods=["GET", "HEAD"], operation_id=OPERATION_MAPTILES)
def maptiles(request: Request, z: int, x: int, y: int) -> Any:
    """The artwork pyramid, at the URL it has always had. An alias for ``map``.

    Not a redirect and not a deprecation: the default layer's name is optional, and this
    answers byte for byte and header for header what ``/api/maptiles/map/{z}/{x}/{y}`` does.
    """
    return _serve_tile(request, MAP_LAYER_DEFAULT, z, x, y)


@router.api_route(
    "/maptiles/{layer}/{z}/{x}/{y}", methods=["GET", "HEAD"], operation_id=OPERATION_MAPTILES_LAYER
)
def maptiles_layer(request: Request, layer: str, z: int, x: int, y: int) -> Any:
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
