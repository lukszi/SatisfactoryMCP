"""Generate data/world_collectibles.json from the game's own map assets plus the saves.

    uv run --extra gen python tools/gen_world_collectibles.py <saves dir>

The code is the ``tools.collectibles`` package; docs/world-collectibles.md says what the
table holds and how each number in its ``_meta`` is measured.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from tools.collectibles.command import main

if __name__ == "__main__":
    raise SystemExit(main())
