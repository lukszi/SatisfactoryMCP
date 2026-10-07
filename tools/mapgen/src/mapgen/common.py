"""What every generator here needs before it can read the installed game's container.

``ooz`` (from ``pyooz``), ``texture2ddecoder``, Pillow and ``zstandard`` (the render caches'
codec) are the ``gen`` extra, pinned exactly because they decide the bytes a generator
writes, and imported at module scope nowhere in this repository --
``tests/architecture/test_optional_extras.py`` holds that line, so ``require_gen`` proves them
present at run time instead::

    uv run --extra gen python tools/gen_world_collectibles.py
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import TypedDict

#: The repository checkout: tools/mapgen/src/mapgen/common.py is four levels below it.
ROOT = Path(__file__).resolve().parents[4]

#: ``renders/<layer>/`` holds a ``tiles/`` tree of exactly the shape ``data/local/tiles/``
#: has, so the endpoint that serves one serves the others with a directory swapped.
LOCAL_DIR = ROOT / "data" / "local"
RENDERS_DIR_NAME = "renders"

DEFAULT_GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")

#: Import name -> distribution: the import name is what fails, the distribution name is
#: what the sidecars record, because it is what you install.
GEN_MODULES = {
    "ooz": "pyooz",
    "texture2ddecoder": "texture2ddecoder",
    "PIL.Image": "pillow",
    "zstandard": "zstandard",
}


def gen_invocation() -> str:
    """The command line that fixes a missing ``gen`` extra, naming the running script."""
    script = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    return f"uv run --extra gen python tools/{script or 'gen_<tool>.py'}"


def require_gen(*names: str) -> dict[str, str]:
    """Prove the ``gen`` extra's modules import, or print the fix and exit 2.

    Returns ``{distribution: version}`` for the names asked for -- what the generators
    record in their sidecars. The caller imports the modules at the point of use.
    """
    versions: dict[str, str] = {}
    missing: list[str] = []
    for name in names:
        distribution = GEN_MODULES[name]
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(distribution)
        else:
            versions[distribution] = _installed_version(distribution)
    if missing:
        print(
            f"{', '.join(missing)} not importable, so the game's own container cannot be "
            "opened. These are the `gen` extra: generation-time tools, imported at module "
            "scope by nothing in this repository, and installed by asking for them:\n"
            f"    {gen_invocation()}"
        )
        raise SystemExit(2)
    return versions


def base_parser(description: str) -> argparse.ArgumentParser:
    """A parser carrying ``--game``, the one argument every container-reading generator takes."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--game",
        type=Path,
        default=DEFAULT_GAME,
        help="Satisfactory install directory (the one holding FactoryGame/ and Engine/)",
    )
    return parser


def _installed_version(distribution: str) -> str:
    """The installed version, or ``"unknown"`` -- a sidecar field, never a control flow."""
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return "unknown"


class ReaderStamp(TypedDict):
    """What a cache read off the game must agree with to be read: the build and the reader."""

    game_version_pinned: str | None
    reader_version: int


class Refusal(Exception):
    """A run the generator will not do, with the exit code ``main`` returns for it."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
