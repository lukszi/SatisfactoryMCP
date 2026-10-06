"""Which build an artifact was cut from, and writing it so it can never say the wrong one.

Every table and raster under ``data/`` derives from one installed build, so this reads the
build installed, reads the build an artifact claims, and installs a new artifact all at once:
this build's rasters beside last build's sidecar would not fail, they would answer wrong.
:func:`installed_build` reads the build system's JSON and is the pin every staleness guard
compares; :func:`installed_build_from_exe` reads the engine's own literal out of the binary.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..jsontypes import JsonObject, JsonValue
from .versions import PROVENANCE_SCHEMA

#: The engine's own version file, the one the build system writes beside the executable.
VERSION_GLOB = "Engine/Binaries/Win64/*-Win64-Shipping.version"

#: Where the executable's version resource states the branch, and how far past it to read
#: when the string is not terminated where it should be. Bytes, not characters: the scan is
#: over UTF-16, so 200 of them is 100 characters of branch name and nothing is ever that long.
BRANCH_MARK = "++FactoryGame+rel-"
BRANCH_MAX_BYTES = 200

#: What a directory is called while it is being written, and while it is being replaced.
#: Neither name is ever served: a reader looks for the real one and finds it whole or not
#: at all, and the next run deletes whatever an interrupted one left behind.
STAGING_SUFFIX = ".incoming"
RETIRED_SUFFIX = ".retired"


class InstallNotFound(Exception):
    """The directory given is not an installed copy of the game.

    Raised rather than exited on: nothing in ``core`` decides that a process should stop, and
    the generator that owns the ``--game`` argument is the one that can name the fix.
    """


def installed_build(game: Path) -> tuple[str, JsonObject]:
    """The installed build as ``(pin string, the raw version JSON)``. The string is built here
    rather than per generator so two artifacts cannot spell one build two ways."""
    found = sorted(game.glob(VERSION_GLOB))
    if not found:
        raise InstallNotFound(f"no {VERSION_GLOB} under {game}")
    raw: JsonValue = json.loads(found[0].read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise InstallNotFound(f"{found[0]} holds no JSON object")
    pin = (
        f"buildVersion {raw.get('Changelist')} "
        f"(engine branch {raw.get('BranchName')}), the installed build"
    )
    return pin, raw


def installed_build_from_exe(game: Path) -> str | None:
    """The engine's own build string, out of the shipping executable's version resource --
    ``++FactoryGame+rel-main-1.2.0-CL-495413`` on build 495413, verbatim.

    Scanned as UTF-16 rather than parsed as a PE resource. ``None`` when no shipping
    executable carries it: a caller writing a sidecar records nothing rather than a guess.
    """
    needle = BRANCH_MARK.encode("utf-16-le")
    for candidate in sorted(game.glob("*/Binaries/Win64/*Shipping.exe")):
        blob = candidate.read_bytes()
        at = blob.find(needle)
        if at < 0:
            continue
        end = blob.find(b"\0\0", at)
        limit = at + BRANCH_MAX_BYTES
        stop = end + 1 if at < end < limit else limit
        return blob[at:stop].decode("utf-16-le", "replace")
    return None


def read_path(sidecar: JsonValue, path: Iterable[str]) -> JsonValue:
    """Walk nested dicts to whatever is at ``path``, or ``None`` the moment the walk fails.

    Any JSON value, because the staleness guards in ``tools/`` want a string, a bool and an
    int off the identical walk. Incurious about what it finds: an older sidecar, a truncated
    one, a JSON document that is not an object are all "this path is not there", which is
    what makes a guard refuse rather than crash. Checking the type is the caller's, not this.
    """
    node = sidecar
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def read_str_path(sidecar: JsonValue, path: Iterable[str]) -> str | None:
    """:func:`read_path`, refusing anything that is not a string. A pin that arrived as a
    number is a sidecar this reader does not understand, which refuses the same way."""
    node = read_path(sidecar, path)
    return node if isinstance(node, str) else None


#: Every generated artifact that records the build it was cut from, as (what to call it in a
#: sentence, its path under ``data/``, the key path holding the pin). The four spellings are
#: not tidyable from here: each generator chose where in its own sidecar the pin lives, and the
#: constants naming those places are in ``tools/`` beside the writers.
#:
#: The last three are under ``data/local/`` and are NOT in git, so a checkout that has never
#: run the generators simply has none of them. Absent is not stale -- see `stale_artifacts`.
PINNED_ARTIFACTS = (
    ("the resource node table", ("resource_nodes.json",), ("_meta", "sources", "primary")),
    ("the world node table", ("world_resource_nodes.json",), ("_meta",)),
    ("the region names", ("region_names.json",), ("_meta",)),
    ("the height field", ("local", "heightmap", "meta.json"), ("sources", "game")),
    ("the map image", ("local", "map.json"), ("_meta", "sources", "map_slices")),
    ("the item icons", ("local", "icons", "manifest.json"), ("_meta", "source")),
)

#: The leaf every one of them agrees on, whatever it is nested under.
PIN_KEY = "game_version_pinned"


def stale_artifacts(game: Path, data: Path) -> list[str]:
    """Which pinned artifacts were cut from a build other than the one installed here.

    The standing hazard is that a game update MOVES resource nodes, and every table under
    ``data/`` is a photograph of one build. ``installed_build`` has stated the build the
    machine has since the generators were written and nothing at runtime has ever asked it,
    so the tables have been free to describe a world that is no longer there.

    Silent about everything it cannot check, and that is the whole discipline of it: no
    install, an unreadable sidecar, an artifact that was never generated and a sidecar too old
    to carry a pin are all "no comparison", never "stale". Only two pins that exist and differ
    are a finding.
    """
    try:
        pin, _ = installed_build(game)
    except (InstallNotFound, OSError, ValueError):
        return []
    drifted: list[str] = []
    for name, parts, where in PINNED_ARTIFACTS:
        try:
            sidecar: JsonValue = json.loads(data.joinpath(*parts).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        recorded = read_str_path(sidecar, (*where, PIN_KEY))
        if recorded and recorded != pin:
            # Quoted, because every pin this project writes ends "the installed build" -- true
            # of the build it was written under and a lie about now, which is the whole finding.
            drifted.append(f"{name} records {recorded!r}")
    if not drifted:
        return []
    return [
        f"the game installed here is {pin}, and {len(drifted)} generated table(s) describe a "
        "different one, so positions and names taken from them may not be where the game now "
        "puts them: " + "; ".join(drifted) + ". Re-run the generators in tools/"
    ]


def sha256_hex(*blobs: bytes | memoryview) -> str:
    """``sha256:<hex>`` over the blobs in order, the spelling every ``digest`` field uses."""
    digest = hashlib.sha256()
    for blob in blobs:
        digest.update(blob)
    return "sha256:" + digest.hexdigest()


def files_digest(hashes: Mapping[str, str]) -> str:
    """One digest for a directory: sha256 over the sorted ``name:sha256`` lines."""
    lines = "".join(f"{name}:{hashes[name]}\n" for name in sorted(hashes))
    return sha256_hex(lines.encode("utf-8"))


def changelist(raw: JsonValue) -> int | None:
    """The build number out of a raw version JSON or a pin string, or ``None``."""
    if isinstance(raw, dict):
        value = raw.get("Changelist")
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if isinstance(raw, str):
        found = re.search(r"buildVersion (\d+)", raw)
        return int(found.group(1)) if found else None
    return None


def provenance_block(
    game_raw: Mapping[str, JsonValue],
    inputs: Mapping[str, object],
    renderer: Mapping[str, object],
    style: Mapping[str, object],
) -> dict[str, object]:
    """The ``_meta.provenance`` block every map generator writes (docs/maps_contract.md §3).

    ``inputs`` lists only what this map read; each entry carries its own ``cl``.
    """
    return {
        "schema": PROVENANCE_SCHEMA,
        "game": {
            "cl": changelist(dict(game_raw)),
            "branch": game_raw.get("BranchName"),
            "game_version": game_raw.get("GameVersion"),
        },
        "inputs": inputs,
        "renderer": renderer,
        "style": style,
    }


def install_directory(out_dir: Path, payload: Mapping[str, bytes]) -> dict[str, int]:
    """Write a whole artifact directory into staging and rename it into place, so ``out_dir``
    appears complete or not at all.

    The previous directory is renamed aside rather than deleted first, so the window in which
    nothing is in place is a rename wide. Returns ``{name: bytes}`` for the sidecar to record.
    """
    staging = out_dir.with_name(out_dir.name + STAGING_SUFFIX)
    retired = out_dir.with_name(out_dir.name + RETIRED_SUFFIX)
    for stale in (staging, retired):
        if stale.exists():
            shutil.rmtree(stale)
    staging.mkdir(parents=True)
    written: dict[str, int] = {}
    for name, blob in payload.items():
        (staging / name).write_bytes(blob)
        written[name] = len(blob)
    if out_dir.exists():
        out_dir.rename(retired)
    staging.rename(out_dir)
    if retired.exists():
        shutil.rmtree(retired)
    return written
