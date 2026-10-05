"""Extract the landscape's paint layers once, into ``data/local/paint/``.

A shim for ``python -m mapgen paint``, which holds the code (tools/mapgen). The path
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
    import warnings

    warnings.warn(
        "tools/gen_paint_layers.py is deprecated and will be removed in 0.3.0; "
        "use `python -m mapgen paint`",
        DeprecationWarning,
        stacklevel=1,
    )
    from mapgen.cli import main

    raise SystemExit(main(["paint", *sys.argv[1:]]))
