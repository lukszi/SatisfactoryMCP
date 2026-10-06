"""A fake tile pyramid: the ``tiles/`` tree the generators write, with tiny PNGs in it."""

from __future__ import annotations

import base64
from pathlib import Path

from satisfactory_mcp.core.gameassets.pyramid import TILES_DIR_NAME

#: A 1x1 PNG; the tests need a tree of the right shape, not any picture.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
)


def fake_pyramid(local: Path, max_z: int = 2, payload: bytes = PNG_BYTES) -> int:
    """Write a ``tiles/`` tree of ``payload`` at the generator's layout; return the tile count.

    A distinct ``payload`` per tree is how a test shows a request reached the tree it named.
    """
    count = 0
    for z in range(max_z + 1):
        (local / TILES_DIR_NAME / str(z)).mkdir(parents=True)
        for x in range(1 << z):
            for y in range(1 << z):
                (local / TILES_DIR_NAME / str(z) / f"{x}_{y}.png").write_bytes(payload)
                count += 1
    return count
