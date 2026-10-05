"""Cut a real 1 m heightmap of this world out of the installed game.

A shim for ``python -m mapgen heightmap``, which holds the code (tools/mapgen). The path
stays because the registry, the provenance strings and the docs name it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "src", ROOT / "tools" / "mapgen" / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

if __name__ == "__main__":
    from mapgen.cli import main

    raise SystemExit(main(["heightmap", *sys.argv[1:]]))
