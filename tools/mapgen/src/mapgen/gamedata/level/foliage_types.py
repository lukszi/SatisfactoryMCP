"""Which foliage type painted a foliage component, and the material that type puts on it.

A foliage component keeps no ``OverrideMaterials`` of its own: the foliage type does, and the
component's ``InstancedFoliageActor`` maps each type to its component in its native tail
(``FoliageInfos``). docs/map/painted.md section 30, "The desert shells".
"""

from __future__ import annotations

import struct

from mapgen.gamedata.install import open_package
from mapgen.gamedata.materials import first_override
from satisfactory_mcp.core.gameassets.iostore import IoStore
from satisfactory_mcp.core.gameassets.packages import AssetIndex, PackageView, ScriptObjects

__all__ = ["FoliageTypeMaterials", "component_foliage_type"]

#: In ``FoliageInfos`` a type's import index stands this many bytes before its component's
#: export index: the index itself, then the one-byte implementation kind.
_TYPE_BEFORE_COMPONENT = 5


def component_foliage_type(view: PackageView, slot: int) -> str | None:
    """The foliage type whose entry in the owning actor's ``FoliageInfos`` names the
    component at ``slot``, as a package path; None where none does."""
    actor = view.outer_of.get(slot)
    if actor is None:
        return None
    body = view.pkg.body(view.exports[actor])
    want = struct.pack("<i", slot + 1)
    at = body.find(want, _TYPE_BEFORE_COMPONENT)
    while at >= 0:
        key = body[at - _TYPE_BEFORE_COMPONENT : at - 1]
        if struct.unpack("<i", key)[0] < 0 and (path := view.import_path(key)):
            return path
        at = body.find(want, at + 1)
    return None


class FoliageTypeMaterials:
    """Each foliage type's first override material, read once per type."""

    def __init__(self, store: IoStore, scripts: ScriptObjects, index: AssetIndex) -> None:
        self.store, self.scripts, self.index = store, scripts, index
        self._found: dict[str, str | None] = {}

    def material(self, foliage_type: str | None) -> str | None:
        """The material ``foliage_type`` puts on its instances, or None for the mesh's own."""
        if foliage_type is None:
            return None
        if foliage_type not in self._found:
            view = open_package(self.store, self.scripts, self.index, foliage_type)
            found = None
            for export in view.exports if view is not None else ():
                payload = view.props(export["slot"]).get("OverrideMaterials") if view else None
                if view is not None and (found := first_override(view, payload)):
                    break
            self._found[foliage_type] = found
        return self._found[foliage_type]
