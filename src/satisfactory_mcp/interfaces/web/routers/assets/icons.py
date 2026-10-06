"""``/api/icons/{desc}``: one item's picture, out of the reader's own game.

A loader and only a loader: an item icon is the game's artwork, this repository ships none
of it, and every answer here is either the file the reader generated or the name of the tool
that would write it. That is why the 404s are long -- they are the whole of the
documentation a reader gets at the moment they need it.

Handler names are operation_ids; wire rules: docs/web-wire.md. This route carries an
explicit id; see ``OPERATION_ICON``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from .....domain.maps import registry
from ...serial import cached_file, error_response, sidecar_meta_block

__all__ = ["ICONS_DIR_NAME", "router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


#: Where ``tools/gen_item_icons.py`` writes, under the same ``data/local/`` the map render
#: and the tile pyramids live in. Nothing here is ever committed.
ICONS_DIR_NAME = "icons"
ICONS_MANIFEST_NAME = "manifest.json"

#: The tool that fills the directory, named in the 404 so an empty one explains itself.
ICONS_TOOL = (
    "tools/gen_item_icons.py, which decodes them out of your own installed game's container"
)

#: What a ``{desc}`` segment may be: a descriptor class is letters, digits and underscores,
#: and every class the generator writes matches. The segment is VALIDATED and then used,
#: never joined and then cleaned -- a name that is not this shape never becomes a path, so
#: there is no ``..`` to reject, no separator to normalise and no encoding to unwrap.
DESC_RE = re.compile(r"\A[A-Za-z0-9_]{1,128}\Z")


def _icons_dir() -> Path:
    """The generated directory, read at call time so a test can point it somewhere else."""
    return registry.local_dir() / ICONS_DIR_NAME


def _icons_build() -> str:
    """A short digest of what the generator recorded, used only ever as a cache tag.

    It changes when the directory is regenerated and at no other time, which is what makes
    ``immutable`` safe to send behind a ``?v=`` carrying it. An absent or malformed manifest
    is not an error but a stable tag for "nothing": a typo in an optional file must not take
    down the endpoint that serves a picture.
    """
    block = sidecar_meta_block(_icons_dir() / ICONS_MANIFEST_NAME)
    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    counts = block.get("counts") if isinstance(block.get("counts"), dict) else {}
    stamp = "|".join(
        [
            str(source.get("game_version_pinned")),
            *(str(counts.get(key)) for key in ("icons_written", "bytes", "written_px")),
        ]
    )
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12]


def icon_path(desc: str) -> Path | None:
    """Where one item's PNG lives, or ``None`` if ``desc`` is not a descriptor class name.

    The whole of this module's path handling, and it is a refusal rather than a repair; see
    :data:`DESC_RE`.
    """
    return _icons_dir() / f"{desc}.png" if DESC_RE.match(desc) else None


# -------------------------------------------------------------------- serving


#: Explicit, because this route serves GET and HEAD from one handler: FastAPI walks
#: ``route.methods``, which is a SET, so an implicit id is ``..._get`` or ``..._head`` at
#: random per interpreter run and the committed schema churns for it.
OPERATION_ICON = "icon"


@router.api_route("/icons/{desc}", methods=["GET", "HEAD"], operation_id=OPERATION_ICON)
def icon(request: Request, desc: str) -> Any:
    """One item descriptor's icon as a PNG, from the reader's own install.

    **Absent is the ordinary state, so HEAD answers 204 rather than 404.** A page decides
    whether to draw icons at all by probing one, and a 404 on every clean load trains the
    reader to ignore console errors. The GET keeps its 404 and names the generator.

    **Two different absences, told apart.** A directory that was never generated is answered
    with the command that would fill it. A directory that exists without this class is a
    different fact: a few item classes have no picture anywhere in the game's own docs, so
    "no icon for this one" is a complete answer rather than a missing file.

    **Cached hard, and stamped with the build.** An icon is immutable for a given cut, so a
    client asks with ``?v=`` the tag handed out in ``X-Icons-Build``; an untagged request
    revalidates instead, and the ETag makes that a 304 rather than the bytes.
    """
    path = icon_path(desc)
    if path is None:
        return error_response(
            f"{desc!r} is not a descriptor class name: these are named exactly as the save "
            "and the docs dump name them, e.g. Desc_IronPlate_C or "
            "Build_StorageContainerMk1_C, and nothing else is looked up",
            404,
        )
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        directory = _icons_dir()
        if not (directory / ICONS_MANIFEST_NAME).is_file():
            return error_response(
                f"no icons: {directory} is written by {ICONS_TOOL}. Like the map image, it "
                "is only ever read locally, never uploaded and never committed.",
                404,
            )
        return error_response(
            f"no icon for {desc}: the directory was generated and holds no such file. Either "
            "the class is one of the few the game ships no picture for -- manifest.json "
            "lists every one under 'unresolved', with the reason -- or it is not an item "
            "class at all.",
            404,
        )
    build = _icons_build()
    return cached_file(
        request, path, build, media_type="image/png", headers={"X-Icons-Build": build}
    )
