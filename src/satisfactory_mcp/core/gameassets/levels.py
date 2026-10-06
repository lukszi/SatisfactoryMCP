"""One walk over the world's cooked level packages, for the generators that sweep them.

This decides which packages exist and hands them over parsed; what is in them is the
generators' business. The order is part of the contract: the generators write pinned
artifacts checked by regenerating and hashing, so the walk is sorted by container path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from .iostore import IoStore
from .packages import PackageView, ScriptObjects

__all__ = ["LEVEL_SUFFIX", "WORLD_LEVEL_DIR", "level_paths", "walk_levels"]

#: What a level package is called. The world's geometry, its actors and its landscape all
#: arrive in these; ``.uasset`` is everything else and is read by class, not by sweep.
LEVEL_SUFFIX = ".umap"

#: The one world. A substring rather than a prefix because the container path carries a
#: mount point in front of it that is not this module's business.
WORLD_LEVEL_DIR = "Map/GameLevel01"


def level_paths(
    store: IoStore, *, contains: str = WORLD_LEVEL_DIR, suffix: str = LEVEL_SUFFIX
) -> list[str]:
    """Every level package of one world, sorted -- the sweep order the generators bank on.

    Over ``by_path``, which names each package once; ``paths.values()`` has one entry per
    container ENTRY, so a duplicate entry would sweep a package twice.
    """
    return sorted(p for p in store.by_path if p.endswith(suffix) and contains in p)


def walk_levels(
    store: IoStore,
    scripts: ScriptObjects,
    *,
    paths: list[str] | None = None,
    contains: str = WORLD_LEVEL_DIR,
    suffix: str = LEVEL_SUFFIX,
    on_unreadable: Callable[[str, Exception], None] | None = None,
) -> Iterator[tuple[int, int, str, PackageView]]:
    """Yield ``(index, total, path, view)`` for every readable level package, in order.

    ``index`` and ``total`` are handed out rather than left to the caller to count because
    both callers print progress off them and neither wants to hold the path list as well.
    ``index`` counts packages VISITED, including the unreadable ones, so a progress line
    stays a fraction of the whole sweep.

    Unreadable packages are skipped; ``on_unreadable``, when given, hears each one. One
    unreadable package must not cost the rest, and only the caller knows whether to count
    it, name its exception type, or refuse the run.

    ``paths`` is for the caller that needs the total BEFORE the sweep, which a count of what
    the sweep yielded would understate by every failure. Passing it skips the listing.
    """
    if paths is None:
        paths = level_paths(store, contains=contains, suffix=suffix)
    total = len(paths)
    for index, path in enumerate(paths):
        try:
            view = PackageView(store.read_path(path), scripts)
        except Exception as exc:
            if on_unreadable is not None:
                on_unreadable(path, exc)
            continue
        yield index, total, path, view
