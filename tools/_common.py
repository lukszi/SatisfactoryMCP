"""What every generator here needs before it can read the installed game's container.

The one copy lives in ``mapgen.common``; this name stays for the generators that are still
plain scripts in ``tools/``::

    uv run --extra gen python tools/gen_world_collectibles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_MAPGEN_SRC = Path(__file__).resolve().parent / "mapgen" / "src"
if str(_MAPGEN_SRC) not in sys.path:
    sys.path.insert(0, str(_MAPGEN_SRC))

from mapgen.common import (
    DEFAULT_GAME,
    GEN_MODULES,
    base_parser,
    gen_invocation,
    require_gen,
)

__all__ = ["DEFAULT_GAME", "GEN_MODULES", "base_parser", "gen_invocation", "require_gen"]
