"""The heightfield seam: one loader, so a test can replace the terrain for the whole app.

**Call it through the module.** ``terrain.field()``, never ``from .terrain import field``:
a ``from`` import binds the function object into the caller's namespace at import time, so
``monkeypatch.setattr`` on this module would be invisible to it and the test would silently
measure the reader's own machine instead of its synthetic field.
"""

from __future__ import annotations

from ...domain.spatial import heightfield as spatial_heightfield

__all__ = ["field"]


def field() -> spatial_heightfield.Field | None:
    """The extracted 1 m heightfield, or ``None`` on a machine that has none.

    The raster is derived from the reader's own cooked game assets, so this repository ships
    none and most installs have none.
    """
    return spatial_heightfield.load_field()
