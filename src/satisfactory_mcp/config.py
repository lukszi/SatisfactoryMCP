"""Paths and environment configuration."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from platformdirs import user_cache_dir, user_data_dir

PKG_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PKG_ROOT.parents[1]

#: Candidate install locations, checked in order, when SATISFACTORY_DOCS is unset.
_DOCS_CANDIDATES = (
    r"G:\SteamLibrary\steamapps\common\Satisfactory",
    r"C:\Program Files (x86)\Steam\steamapps\common\Satisfactory",
    r"C:\Program Files\Epic Games\SatisfactoryEarlyAccess",
    r"D:\SteamLibrary\steamapps\common\Satisfactory",
    r"E:\SteamLibrary\steamapps\common\Satisfactory",
)
_DOCS_SUFFIX = Path("CommunityResources") / "Docs" / "en-US.json"


def docs_path() -> Path:
    """Locate Docs/en-US.json, or raise ``FileNotFoundError`` naming every place it looked.

    Raises rather than returning a guess. A returned path that was never checked is a wrong
    answer wearing the type of a right one: it travels to whichever loader opens it, and the
    reader is then told the dump is missing from a drive letter this machine has never had.
    """
    env = os.environ.get("SATISFACTORY_DOCS")
    if env:
        chosen = Path(env)
        if not chosen.is_file():
            raise FileNotFoundError(
                f"SATISFACTORY_DOCS is set to {chosen}, which is not a file. It names the "
                rf"dump itself, <install>\{_DOCS_SUFFIX}, not the install directory."
            )
        return chosen
    for base in _DOCS_CANDIDATES:
        candidate = Path(base) / _DOCS_SUFFIX
        if candidate.is_file():
            return candidate
    looked = ", ".join(_DOCS_CANDIDATES)
    raise FileNotFoundError(
        f"no game install found: looked for {_DOCS_SUFFIX} under {looked}. Set "
        r"SATISFACTORY_DOCS to <install>\CommunityResources\Docs\en-US.json to say where "
        "the game is."
    )


def game_root() -> Path:
    """The install directory, found through the dump that sits inside it.

    Derived rather than probed for a second time: ``_DOCS_SUFFIX`` names the three levels
    between them, so this cannot drift from ``docs_path`` and cannot answer for an install
    the docs did not come from. Raises whatever ``docs_path`` raises.
    """
    return docs_path().parents[len(_DOCS_SUFFIX.parts) - 1]


def saves_root() -> Path:
    """Root of the save directory tree (contains one folder per Steam account)."""
    env = os.environ.get("SATISFACTORY_SAVES")
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "FactoryGame" / "Saved" / "SaveGames"


WEB_HOST = "127.0.0.1"
WEB_PORT = 8712


def web_port() -> int:
    """The web map's port: ``SATISFACTORY_WEB_PORT`` when it is a valid port, else 8712."""
    raw = (os.environ.get("SATISFACTORY_WEB_PORT") or "").strip()
    if raw.isdigit() and 0 < int(raw) < 65536:
        return int(raw)
    return WEB_PORT


def web_url() -> str:
    """The web map's root URL, as the server started with the same environment binds it."""
    return f"http://{WEB_HOST}:{web_port()}/"


#: The extractor, named as a module rather than a file. Spawning it with ``-m`` means the
#: child resolves it through the same import machinery this process used, so it can never
#: run a stale copy sitting next to a path someone built by hand.
EXTRACTOR_MODULE = "satisfactory_mcp.core.saveio.extract"


def source_root() -> Path | None:
    """The ``src/`` tree, when this package is running from a checkout.

    The child needs both ``satisfactory_mcp`` and ``pioneersav``, and putting this on its
    PYTHONPATH is what guarantees it imports the source *this* process came from rather than
    whatever an inherited environment resolves first. Installed from a wheel there is no
    ``src/`` and no ambiguity, so the answer is None and the child uses its own site-packages.
    """
    src = PKG_ROOT.parent
    return src if (src / "pioneersav" / "__init__.py").is_file() else None


def data_dir() -> Path:
    return REPO_ROOT / "data"


@lru_cache(maxsize=1)
def cache_dir() -> Path:
    """Machine-local regenerable cache.

    LOCALAPPDATA, not APPDATA: this must not roam.
    """
    d = Path(user_cache_dir("satisfactory-mcp", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_dir() -> Path:
    """Where the player's own writing lives: ``SATISFACTORY_USER_DATA``, else the platform's."""
    env = os.environ.get("SATISFACTORY_USER_DATA")
    return Path(env) if env else Path(user_data_dir("satisfactory-mcp", appauthor=False))


@lru_cache(maxsize=1)
def plans_dir() -> Path:
    """Named plans the player saved. Same reasoning as labels_dir: not regenerable,
    not the cache, not the repo."""
    d = user_dir() / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


@lru_cache(maxsize=1)
def labels_dir() -> Path:
    """Factory names the player typed. NOT the cache, and NOT the repo.

    Deliberately separate from ``cache_dir``: cache_prune deletes everything under
    that tree, and a hand-written name is not regenerable. Out of the repo because it
    belongs to a save file, not to the source.
    """
    d = user_dir() / "labels"
    d.mkdir(parents=True, exist_ok=True)
    return d
