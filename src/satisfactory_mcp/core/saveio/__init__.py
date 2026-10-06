"""The sidecar seam: reading a save file and caching what came back.

Everything above this package consumes the plain-dict projection and never learns
that a save parser, a subprocess or a pickle cache exists. That is what lets the
whole test suite run from a committed JSON fixture with no game install.
"""

from __future__ import annotations

from .projection import (
    SaveError,
    World,
    list_worlds,
    load_projection,
    prune_cache,
    resolve_save,
    scan_saves,
    ticks_to_epoch_seconds,
)

__all__ = [
    "SaveError",
    "World",
    "list_worlds",
    "load_projection",
    "prune_cache",
    "resolve_save",
    "scan_saves",
    "ticks_to_epoch_seconds",
]
